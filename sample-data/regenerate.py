#!/usr/bin/env python3
"""Regenerate the Fixtura demo bundles in this directory.

    python3 sample-data/regenerate.py                 # both bundles
    python3 sample-data/regenerate.py funding-pace    # just one
    python3 sample-data/regenerate.py --measure       # measure, write nothing

Why this exists. Both bundles are `pop_in_progress`: Fixtura anchors the period of
performance to *today*, so a committed CSV goes stale on its own as real time
passes — the timesheets stop moving and the clock does not. A bundle frozen at
week 36 of a 52-week PoP is read against week 40 a month later, and the numbers
in its README drift out from under it. Rather than pin a fake "today" (which
Fixtura has no knob for, and which would make the award dates wrong), the bundles
are cheap to rebuild: run this, and the README numbers are re-measured against
Runway's own burn engine rather than asserted.

Point it at a Fixtura checkout with FIXTURA_PATH if it is not beside this repo.
"""

import csv
import io
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FIXTURA = Path(
    os.environ.get("FIXTURA_PATH", REPO.parent / "si2026-test-generator-kaylee")
)

sys.path.insert(0, str(FIXTURA))
sys.path.insert(0, str(REPO / "server"))

from testgen import presets, writers  # noqa: E402
from testgen.formfill import fill_form_bytes, fill_forms_bytes  # noqa: E402
from app import burn  # noqa: E402


# The two bundles, and the ONE thing each is for.
#
# Both pin `funding: "incremental"`. That is deliberate and replaces the old
# practice of hunting for a seed whose funding posture happened to lag: the
# posture is a knob, so it should be pinned, and a bundle that depends on an
# un-pinned draw stops demonstrating its own point the moment anything upstream
# moves. What is NOT pinned is the burn — staffing and target_hours are tuned to
# put each bundle in the band its README claims, and re-tuned by measuring, not
# by guessing.
BUNDLES = {
    "burn": {
        "stem": "fixtura-runway-burn-demo",
        "title": "Runway burn demo",
        "purpose": "A contract genuinely in trouble: red. Burn has already "
        "outrun the funded slice and projects past the ceiling before the "
        "period ends.",
        "seed": 42,
        "opts": {
            "pop_in_progress": True,
            "option_years": 0,
            "contract_type": "T&M",
            "funding": "incremental",
            # Crewed ABOVE plan, which is what makes it hot.
            "staffing": 1.2,
            "target_hours": 40,
            # Shared cross-contract people pool. Both bundles draw from it so a
            # handful of employees recur across contracts, which is the only thing
            # the portfolio resource-conflict detector (booked >100% across
            # contracts) has to find. Runway's live sync pins this too — see
            # server/app/sources.py DEMO_SCENARIO_OPTS.
            "shared_pool": True,
        },
        "want": "over",
    },
    "funding-pace": {
        "stem": "fixtura-runway-funding-pace-demo",
        "title": "Runway funding-pace demo",
        "purpose": "The contract that must NOT cry wolf: amber. Its funded "
        "dollars run dry inside FAR 52.232-22(c)'s 60-day notification window, "
        "but the ceiling holds and the obligations are landing as fast as the "
        "dollars burn — so it reads 'Funding due', never red.",
        "seed": 19,
        "opts": {
            "pop_in_progress": True,
            "option_years": 0,
            "contract_type": "T&M",
            "funding": "incremental",
            # Crewed a little under plan at a 35-hour billable target, which is
            # what puts the funded slice inside the 60-day window rather than
            # already behind it.
            "staffing": 0.75,
            "target_hours": 35,
            "shared_pool": True,
        },
        "want": "funding",
    },
}

# The acceptance bar, from #53's own measurement comment. Runway says "Funding
# due" when funded runway is at or inside FAR 52.232-22(c)'s notification
# lookahead — NOT when funding merely lags the clock. A CLIN 62% obligated at 69%
# elapsed reads the ceiling story if its runway is 74 days.
AMBER_DAYS = 60


# Fixtura prints an incentive share as a pair, `[80, 20]`; the award prints it as
# "80/20" and `schemas.CLIN.share_ratio` is the string, Government share first.
def _share_ratio(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}/{value[1]}"
    return value or None


# The CLIN-level cost and fee lines (#78, #183), mapped by the name each type prints
# them under. Every one of these is Optional on both sides, so a type that does not
# price a given element simply omits it — an FFP CLIN has no fee element at all, and
# a CPFF one has no incentive bracket.
def _clin_cost_fee(cl):
    out = {
        # "Total Estimated Cost" on a cost-type CLIN, "Target Cost" on an incentive
        # one. FPI prints only the latter, so this is the one field that has to look
        # under two names.
        "estimated_cost": cl.get("estimated_cost") or cl.get("target_cost"),
        "fixed_fee": cl.get("fixed_fee"),  # CPFF
        "base_fee": cl.get("base_fee"),  # CPAF, guaranteed
        "award_fee_pool": cl.get("award_fee_pool"),  # CPAF, at risk
        "target_fee": cl.get("target_fee"),  # CPIF
        "min_fee": cl.get("min_fee"),  # CPIF bracket
        "max_fee": cl.get("max_fee"),
        "target_profit": cl.get("target_profit"),  # FPI: profit, not fee
        "share_ratio": _share_ratio(cl.get("share_ratio")),
    }
    # `ceiling_price` is the FPI price ceiling (FAR 16.403), which is a different
    # quantity from the CLIN total in `ceiling`. Fixtura also stamps it on T&M, where
    # it is just the ceiling under another name — carrying it there would assert an
    # incentive structure the line does not have.
    if _type_code(cl.get("type")) == "FPI":
        out["ceiling_price"] = cl.get("ceiling_price")
    return {k: v for k, v in out.items() if v is not None}


def _type_code(value):
    return (value or "").strip().upper()


# The three indirect rates, read off the cost buildup Fixtura already computed for a
# labor CLIN. Without these the contract ingests at cost tier 1: cost is unburdened
# direct labor, `margin_available` is false, and every margin figure is withheld —
# which is honest, but means a regenerated bundle cannot demonstrate margin at all.
_BUILDUP_KEYS = {
    "fringe": "indirect_fringe",
    "overhead": "indirect_overhead",
    "g_and_a": "indirect_gna",
}


def _indirect_rates(c):
    for p in c["periods"]:
        for cl in p["clins"]:
            buildup = cl.get("cost_buildup")
            if not buildup:
                continue
            rates_out = {
                _BUILDUP_KEYS[step["key"]]: step["rate"]
                for step in buildup
                if step.get("key") in _BUILDUP_KEYS and step.get("rate") is not None
            }
            if rates_out:
                return rates_out
    return {}


def _header_totals(c):
    """The award's own cost/fee totals (#159), footed from the CLINs that price them.

    Summed rather than taken from a header field because Fixtura carries the split at
    CLIN level only — which is also where the award prints it. A type that prices no
    fee (FFP, T&M) contributes nothing and the totals stay absent rather than zero:
    `total_fee: 0` asserts a fee of nothing, which is a different claim from "this
    award does not price fee separately".
    """
    cost = fee = 0.0
    for p in c["periods"]:
        for cl in p["clins"]:
            cost += cl.get("estimated_cost") or cl.get("target_cost") or 0.0
            fee += (
                cl.get("fee") or cl.get("target_fee") or cl.get("target_profit") or 0.0
            )
    out = {}
    if cost:
        out["total_estimated_cost"] = round(cost, 2)
    if fee:
        out["total_fee"] = round(fee, 2)
    return out


def to_runway(c):
    """Fixtura's nested contract in the shape Runway's extraction produces.

    Deterministic stand-in for the LLM ingest, so a re-baseline can be measured
    without a Bedrock round trip. The field names are `server/app/schemas.py`.
    """
    periods, clins = [], []
    for p in c["periods"]:
        periods.append(
            {
                "name": p["name"],
                "pop_start": str(p["pop_start"]),
                "pop_end": str(p["pop_end"]),
                "exercised": p["exercised"],
                "ceiling": p["ceiling"],
            }
        )
        for cl in p["clins"]:
            clins.append(
                {
                    "clin": cl["clin"],
                    "period": p["name"],
                    "title": cl["title"],
                    "type": cl.get("type"),
                    "is_labor": cl["is_labor"],
                    "ceiling": cl["ceiling"],
                    # The award's ACRN block funds each CLIN by name (#21); this
                    # is that figure, not the CLIN's not-to-exceed ceiling.
                    "obligated": cl.get("funded"),
                    "acrn": cl.get("acrn"),
                    "est_hours": cl.get("est_hours"),
                    "labor_rates": cl.get("labor_rates") or None,
                    **_clin_cost_fee(cl),
                }
            )
    return {
        "id": 1,
        "contract": {
            "piid": c["piid"],
            "agency": c["agency"],
            "contractor": c["contractor"]["name"],
            "contract_type": c["contract_type"],
            "total_ceiling": c["total_ceiling"],
            "total_obligated": c["total_obligated"],
            "incrementally_funded": not c.get("fully_funded"),
            "effective_date": str(c["effective_date"]),
            "contracting_officer": c["contracting_officer"],
            **_header_totals(c),
            **_indirect_rates(c),
        },
        "periods": periods,
        "clins": clins,
        "obligation_history": [
            {
                "date": str(h["date"]),
                "amount": h.get("amount"),
                "cumulative_obligated": h.get("cumulative_obligated"),
                "action_type": h.get("action"),
            }
            for h in c.get("obligation_history") or []
        ],
    }


def build(spec):
    """Generate every file of one bundle in memory, and measure it."""
    seed, opts = spec["seed"], dict(spec["opts"])
    scenario = presets.build_scenario(seed, opts)
    contract = scenario["contract"]

    # A full grid: one row per person per week. generate_preset caps at it.
    sheets = presets.generate_preset(
        "govcon_timesheet", rows=1_000_000, seed=seed, opts=dict(opts)
    )
    labor = presets.generate_preset(
        "govcon_labor_export",
        rows=len(scenario["roster"]) * 6,
        seed=seed,
        opts=dict(opts),
    )
    leave = presets.generate_preset(
        "govcon_planned_leave", rows=1_000_000, seed=seed, opts=dict(opts)
    )

    award = presets.generate_preset(
        "govcon_award_sf26", rows=1, seed=seed, opts=dict(opts)
    )
    preset = presets.PRESETS["govcon_award_sf26"]
    award_pdf = fill_forms_bytes(
        preset["form"],
        [presets.preset_form_values("govcon_award_sf26", r) for r in award],
        attachments=[preset["attachment"](r) for r in award],
    )

    mod_record = presets.generate_preset(
        "govcon_mod_sf30", rows=1, seed=seed, opts=dict(opts)
    )[0]
    mods = {
        mod_no: fill_form_bytes("SF30.pdf", values)
        for mod_no, values in presets.contract_to_sf30_trail(mod_record)
    }

    payload = burn.compute(to_runway(contract), sheets)
    return {
        "scenario": scenario,
        "contract": contract,
        "sheets": sheets,
        "labor": labor,
        "leave": leave,
        "award_pdf": award_pdf,
        "mods": mods,
        "payload": payload,
    }


def summarize(spec, built):
    p = built["payload"]
    labor = [c for c in p["clins"] if c.get("is_labor")]
    return {
        "piid": built["contract"]["piid"],
        "status": [c["status"] for c in labor],
        "runway_days": [c["runway_days"] for c in labor],
        "amber_hit": any(
            c["runway_days"] is not None and c["runway_days"] <= AMBER_DAYS
            for c in labor
        ),
        "tripwires": len(p["tripwires"]),
        "all_clear": p["all_clear"],
        "rows": len(built["sheets"]),
        "want": spec["want"],
        "ok": any(c["status"] == spec["want"] for c in labor),
    }


def readme(spec, built):
    c, p = built["contract"], built["payload"]
    period = c["periods"][0]
    labor = [x for x in p["clins"] if x.get("is_labor")]
    stem = spec["stem"]
    clins = period["clins"]

    def money(v):
        return f"${v:,.2f}"

    rows = "\n".join(
        f"| {x['id']} | {next(cl['title'] for cl in clins if cl['clin'] == x['id'])} "
        f"| {money(x['ceiling'])} | {money(x['funded'])} "
        f"| {x['funded_frac'] * 100:.1f}% | {money(x['spent'])} "
        f"| wk {x['exhaust_week']} | {x['runway_days']} d | `{x['status']}` |"
        for x in labor
    )
    mods = "\n".join(
        f"| {h['mod']} | {h['date']} | {money(h['amount'])} "
        f"| {money(h['cumulative_obligated'])} | {h['action']} |"
        for h in c["obligation_history"]
    )
    files = "\n".join(
        f"| `{stem}.{suffix}` | {what} |"
        for suffix, what in [
            (
                "award.sf26.pdf",
                "The signed award on a real **SF-26**, plus the Section B "
                "fully-burdened labor-rate schedule. Drop into Runway's Ingest step.",
            ),
        ]
        + [
            (
                f"mod.{mod_no}.sf30.pdf",
                f"**SF-30** modification {mod_no} — feed to "
                "`POST /api/contracts/{id}/mods`, one at a time.",
            )
            for mod_no in built["mods"]
        ]
        + [
            (
                "contract.json",
                "The award as structured data — periods, CLINs, labor rates, "
                "ceiling vs. obligated, and the full `obligation_history`.",
            ),
            (
                "timesheets.csv",
                f"Weekly hours booked to the labor CLINs — {len(built['sheets'])} "
                "rows, one per person per week across the elapsed PoP. "
                "`total_hours` is **billable** (regular + overtime); leave and "
                "holidays are carried separately and are not chargeable.",
            ),
            (
                "labor.csv",
                "Labor distribution sample (bill rate x hours), illustrative "
                "only — Runway burns from the timesheets.",
            ),
            (
                "planned-leave.csv",
                "Dated FUTURE absence for the same roster — the input a what-if "
                "projection needs, which the timesheets cannot provide.",
            ),
        ]
    )
    return f"""# Fixtura "{spec['title']}" data set

{spec['purpose']}

Generated by Fixtura (si2026-test-generator). One coherent contract across every
file — the same seed ties them together.

**Regenerate with `python3 sample-data/regenerate.py`.** Do not hand-edit the
numbers below; they are measured against Runway's burn engine at generation time
and rewritten by that script. See "Staleness" at the bottom.

## Files

| File | What it is |
|---|---|
{files}

## The award

PIID **{c['piid']}** ({c['agency']}), {c['contract_type']}. Ceiling
**{money(c['total_ceiling'])}**, obligated **{money(c['total_obligated'])}** \
({c['total_obligated'] / c['total_ceiling'] * 100:.1f}% funded). PoP
**{period['pop_start']} -> {period['pop_end']}** — mid-flight at week
**{p['contract']['current_week']} of {p['contract']['total_weeks']}**, \
{p['contract']['weeks_remaining']} weeks remaining.

CLINs: {', '.join(f"{cl['clin']} ({'labor' if cl['is_labor'] else 'cost'})" for cl in clins)}.

## The obligation history (what the SF-30s rebuild)

The SF-26 carries only the **initial** obligation. Funding pace comes from the
mods — ingest them one at a time to reconstruct the dated history:

| Action | Effective | Amount | Cumulative obligated | Type |
|---|---|---|---|---|
{mods}

Re-ingesting a mod is idempotent (dedup by mod number).

## Measured result

Measured by `burn.compute` on the generated timesheets, {date.today()}:

| CLIN | Title | Ceiling | Funded | Funded % | Spent | Funds exhaust | Runway | Status |
|---|---|---|---|---|---|---|---|---|
{rows}

Red tripwires firing: **{len(p['tripwires'])}**. Contract `all_clear`: \
**{p['all_clear']}**.

{_verdict(spec, labor)}

## How it was generated

Fixtura preset generation, **seed {spec['seed']}**, with:

```json
{json.dumps(spec['opts'], indent=2)}
```

`staffing` and `target_hours` apply only to the labor exports — the award itself
is identical without them. The award is `govcon_award_sf26`; the structured data
is `govcon_contract_data`; the SF-30s are `govcon_mod_sf30` with `split_mods`.

## Staleness

This bundle is `pop_in_progress`, which anchors the period of performance to
**today**. The committed CSVs therefore age: the timesheets stop at the week they
were generated, while the clock keeps moving, so a bundle built at week 36 of 52
is read against week 40 a month later and every figure above drifts. Runway's
amber gate is a *runway-days* threshold, so drift moves the verdict, not just the
decimals.

Regenerate rather than trust a stale bundle:

```
python3 sample-data/regenerate.py {[k for k, v in BUNDLES.items() if v['stem'] == stem][0]}
```
"""


def _verdict(spec, labor):
    hit = [
        c
        for c in labor
        if c["runway_days"] is not None and c["runway_days"] <= AMBER_DAYS
    ]
    if spec["want"] == "funding":
        return (
            f"**Acceptance ({AMBER_DAYS}-day gate):** "
            f"{'PASS' if hit else 'FAIL'} — "
            f"{len(hit)} of {len(labor)} labor CLIN(s) inside FAR 52.232-22(c)'s "
            "notification lookahead. This is the bar, not `funded_frac < "
            "elapsed_frac`: funding lagging the clock is necessary but not "
            "sufficient, and a CLIN 74 days out reads the ceiling story no matter "
            "how far behind its obligations are."
        )
    return (
        "**Acceptance:** "
        f"{'PASS' if any(c['status'] == 'over' for c in labor) else 'FAIL'} — "
        "at least one labor CLIN reads red, with a tripwire firing."
    )


def write(spec, built):
    stem, out = spec["stem"], HERE
    (out / f"{stem}.award.sf26.pdf").write_bytes(built["award_pdf"])
    for mod_no, pdf in built["mods"].items():
        (out / f"{stem}.mod.{mod_no}.sf30.pdf").write_bytes(pdf)
    (out / f"{stem}.contract.json").write_text(
        json.dumps(built["contract"], indent=1, default=str) + "\n"
    )
    (out / f"{stem}.timesheets.csv").write_text(writers.to_csv_string(built["sheets"]))
    (out / f"{stem}.labor.csv").write_text(writers.to_csv_string(built["labor"]))
    (out / f"{stem}.planned-leave.csv").write_text(
        writers.to_csv_string(built["leave"])
    )
    (out / f"{stem}.README.md").write_text(readme(spec, built))


def main(argv):
    measure_only = "--measure" in argv
    names = [a for a in argv if not a.startswith("-")] or list(BUNDLES)
    bad = [n for n in names if n not in BUNDLES]
    if bad:
        sys.exit(f"unknown bundle(s) {bad}; choose from {list(BUNDLES)}")
    failed = []
    for name in names:
        spec = BUNDLES[name]
        built = build(spec)
        s = summarize(spec, built)
        mark = "OK " if s["ok"] else "BAD"
        print(
            f"{mark} {name:14s} {s['piid']} status={s['status']} "
            f"runway_days={s['runway_days']} want={s['want']} "
            f"tripwires={s['tripwires']} all_clear={s['all_clear']} "
            f"rows={s['rows']}"
        )
        if not s["ok"]:
            failed.append(name)
        if not measure_only:
            write(spec, built)
            print(f"    wrote {spec['stem']}.*")
    if failed:
        sys.exit(
            f"\n{failed} no longer land in the band their README claims — retune "
            "staffing / target_hours in BUNDLES and re-run with --measure."
        )


if __name__ == "__main__":
    main(sys.argv[1:])
