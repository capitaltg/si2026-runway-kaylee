"""#86 — the multi-sheet workbook, and the promises it has to keep in a new medium.

The export is the artifact that leaves the building: it gets emailed, reconciled against
an accounting system and forwarded to a contracting officer. Everything the app refuses
to claim on screen it must also refuse to claim in a cell, because a spreadsheet reads as
more authoritative than the view it came from, not less.

So these tests are written as promises about what the file may say:

  * **A withheld figure is a dash carrying its reason, never a zero.** This is the
    whole #82 honesty contract restated for openpyxl. `0` is a number an accountant
    would act on; a level-1 contract exporting "$0 cost" would be the worst defect this
    feature could ship.
  * **Totals are live formulas.** The ticket asks for a workbook whose totals move when
    an assumption changes, and a frozen value is a screenshot with extra steps.
  * **A withheld total does not become a `=SUM()` over dashes**, which Excel would
    quietly report as $0 — the same lie by a longer route.
  * **Summary is a reference into By CLIN**, which is how "every dollar figure on the
    Summary sheet is reproducible from a later sheet" survives the reader editing one.
  * **Generated data is marked**, because the SIMULATED caveat is worthless if it is
    the one thing that doesn't travel with the file.
  * **The buildup multiplies against the right base**, so a G&A pool applies to total
    cost input rather than to whatever row happened to precede it.
"""

from app import workbook as W


def _burn(level_2=True, fee_known=True):
    """A minimal burn payload in the shape the engine publishes: one labor CLIN, one
    non-labor CLIN carrying spend and *no* cost/revenue/fee keys at all — the live-payload
    shape that caught three defects in #82."""
    return {
        "contract": {
            "name": "TEST",
            "piid": "W123-25-C-0001",
            "pop_start": "2025-10-01",
            "pop_end": "2026-09-30",
            "obligated": 1_000_000.0,
            "cost_model": {
                "level": 2 if level_2 else 1,
                "margin_available": level_2,
                "rate_set": (
                    {
                        "fiscal_year": "2026",
                        "scope": "contract",
                        "status": "provisional",
                        "complete": True,
                        "pools": [
                            {
                                "name": "fringe",
                                "label": "Fringe",
                                "rate": 0.30,
                                "base": "direct_labor",
                            },
                            {
                                "name": "overhead",
                                "label": "Overhead",
                                "rate": 0.50,
                                "base": "labor_plus_fringe",
                            },
                            {
                                "name": "gna",
                                "label": "G&A",
                                "rate": 0.10,
                                "base": "total_cost_input",
                            },
                        ],
                    }
                    if level_2
                    else {}
                ),
            },
        },
        "totals": {
            "ceiling": 1_000_000.0,
            "spent": 600_000.0,
            "cost": 620_000.0,
            "cost_known": level_2,
            "revenue": 680_000.0,
            "fee": 60_000.0,
            "fee_known": fee_known,
        },
        "hero": {
            "days": 120,
            "stop_date": "2026-06-30",
            "limited_by": "ceiling",
            "clin": "CLIN 0001",
        },
        "sync": {
            "rows": 10,
            "people": 2,
            "weeks": 5,
            "as_of": "2026-03-06",
            "data_age_days": 3,
        },
        "clins": [
            {
                "id": "0001",
                "code": "CLIN 0001",
                "name": "Labor",
                "is_labor": True,
                "pricing_policy": {"label": "Cost Plus Fixed Fee", "known": True},
                "measured_against": "cost",
                "ceiling": 980_000.0,
                "funded": 980_000.0,
                "spent": 580_000.0,
                "cost": 600_000.0,
                "revenue": 660_000.0,
                "fee_earned": 60_000.0,
                "fee_known": fee_known,
                "margin_pct": 0.0909,
                "weekly": 10_000.0,
                "runway_days": 120,
                "status_label": "On plan",
                "fee_position": {
                    "basis": "fixed_fee",
                    "known": level_2,
                    "terms_known": True,
                    "cost_known": level_2,
                    "clause": "52.216-8",
                    "target": 80_000.0,
                    "earned": 60_000.0,
                    "at_completion": 80_000.0,
                    "target_delta": 0.0,
                    "at_risk": 0.0,
                    "absorbed": 0.0,
                    "withhold": 9_000.0,
                    "collectable": 51_000.0,
                    "cost_frac": 0.75,
                },
                "rate_variance": [
                    {
                        "lcat": "Engineer",
                        "derived_cost": 100.0,
                        "fee_rate": 0.08,
                        "derived_price": 108.0,
                        "negotiated_rate": 110.0,
                    }
                ],
            },
            # No cost / revenue / fee_earned keys — the engine publishes one figure here.
            {
                "id": "0002",
                "code": "CLIN 0002",
                "name": "Travel",
                "is_labor": False,
                "pricing_policy": {"label": "Cost Plus Fixed Fee", "known": True},
                "measured_against": "cost",
                "ceiling": 20_000.0,
                "funded": 20_000.0,
                "spent": 20_000.0,
                "status_label": "On plan",
            },
        ],
    }


def _wb(burn, contract_row=None):
    return W.build_contract_workbook(
        burn,
        {
            "clins": [{"id": "0001", "code": "CLIN 0001"}],
            "employees": [
                {
                    "id": "E-1",
                    "name": "Ada Byron",
                    "lcat": "Engineer",
                    "cells": {
                        "0001": {
                            "hours": 40.0,
                            "lcat": "Engineer",
                            "rate": 110.0,
                            "cost_rate": 100.0,
                            "cost_source": "lcat_direct",
                            "rate_line": {"direct": 55.0},
                        }
                    },
                }
            ],
        },
        [
            {
                "employee": "Ada Byron",
                "employee_id": "E-1",
                "week_ending": "2026-03-06",
                "charge_code": "0001",
                "labor_category": "Engineer",
                "reg_hours": 40.0,
                "ot_hours": 0.0,
                "holiday_hours": 0.0,
                "leave_hours": 0.0,
                "total_hours": 40.0,
                "paid_hours": 40.0,
            }
        ],
        {
            "total_ceiling": 1_000_000.0,
            "total_obligated": 1_000_000.0,
            "incrementally_funded": True,
            "obligation_history": [
                {
                    "mod": "Award",
                    "date": "2025-10-01",
                    "action": "Initial award",
                    "amount": 600_000.0,
                    "cumulative_obligated": 600_000.0,
                },
                {
                    "mod": "P00001",
                    "date": "2026-01-15",
                    "action": "Incremental funding",
                    "amount": 400_000.0,
                    "cumulative_obligated": 1_000_000.0,
                },
            ],
        },
        contract_row or {"id": 1},
    )


def _col(ws, header):
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == header:
            return c
    raise AssertionError(f"no {header!r} column")


def test_every_sheet_the_ticket_asks_for_is_present():
    wb = _wb(_burn())
    assert wb.sheetnames == [
        "Summary",
        "By CLIN",
        "By person",
        "Rate buildup",
        "Fee position",
        "Timesheet detail",
        "Funding history",
    ]


def test_level_1_withholds_cost_as_a_dash_with_its_reason_not_a_zero():
    ws = _wb(_burn(level_2=False))["By CLIN"]
    cost = _col(ws, "Cost")
    labor = ws.cell(2, cost)
    assert labor.value == W.DASH
    assert labor.comment is not None
    assert "level 1" in labor.comment.text
    # And the total under it refuses too, rather than summing a column of dashes to $0.
    assert ws.cell(ws.max_row, cost).value == W.DASH


def test_a_non_labor_clin_reports_its_spend_as_cost_and_revenue_and_withholds_fee():
    """The keys are absent, not zero. Defaulting them printed "$0" on a CLIN holding
    real money — one of the three defects a live payload caught in #82."""
    ws = _wb(_burn())["By CLIN"]
    row = 3
    assert ws.cell(row, _col(ws, "Cost")).value == 20_000.0
    assert ws.cell(row, _col(ws, "Revenue")).value == 20_000.0
    assert ws.cell(row, _col(ws, "Fee earned")).value == W.DASH
    assert "pass-through" in ws.cell(row, _col(ws, "Fee earned")).comment.text


def test_totals_are_live_formulas_and_summary_points_at_them():
    wb = _wb(_burn())
    clins, summary = wb["By CLIN"], wb["Summary"]
    total = clins.max_row
    assert clins.cell(total, _col(clins, "Cost")).value == f"=SUM(H2:H3)"
    assert clins.cell(total, _col(clins, "Revenue")).value == f"=SUM(I2:I3)"
    refs = [
        summary.cell(r, 2).value
        for r in range(1, summary.max_row + 1)
        if isinstance(summary.cell(r, 2).value, str)
        and summary.cell(r, 2).value.startswith("='By CLIN'")
    ]
    assert (
        f"='By CLIN'!H{total}" in refs
    ), "Summary cost must reference the By CLIN total"
    assert f"='By CLIN'!I{total}" in refs


def test_money_cells_carry_a_currency_format():
    ws = _wb(_burn())["By CLIN"]
    assert '"$"' in ws.cell(2, _col(ws, "Cost")).number_format
    assert ws.cell(2, _col(ws, "Margin %")).number_format == W.PCT


def test_dates_are_dates_not_strings():
    ws = _wb(_burn())["Timesheet detail"]
    cell = ws.cell(2, _col(ws, "Week ending"))
    assert cell.value.year == 2026 and cell.value.month == 3
    assert cell.number_format == W.DATE_FMT


def test_generated_data_is_marked_simulated():
    text = " ".join(
        str(c.value)
        for row in _wb(_burn(), {"id": 1, "sync_seed": 42})["Summary"].iter_rows()
        for c in row
        if c.value
    )
    assert "SIMULATED" in text and "seed 42" in text
    imported = " ".join(
        str(c.value)
        for row in _wb(_burn(), {"id": 1})["Summary"].iter_rows()
        for c in row
        if c.value
    )
    assert "SIMULATED" not in imported


def test_the_buildup_applies_each_pool_to_its_own_base():
    """G&A applies to total cost input — the running cost *after* overhead. Pointing it
    at the previous row would be right by accident here and wrong on any award whose
    pools are stored in another order."""
    ws = _wb(_burn())["Rate buildup"]
    rows = {ws.cell(r, 1).value: r for r in range(1, ws.max_row + 1)}
    fringe, overhead, gna = rows["Fringe"], rows["Overhead"], rows["G&A"]
    base = rows["Direct labor"]
    assert ws.cell(fringe, 5).value == f"=B{fringe}*F{base}"
    assert ws.cell(overhead, 5).value == f"=B{overhead}*F{fringe}"
    assert ws.cell(gna, 5).value == f"=B{gna}*F{overhead}"


def test_no_fee_terms_withholds_rather_than_reporting_zero_fee():
    ws = _wb(_burn(fee_known=False))["By CLIN"]
    cell = ws.cell(2, _col(ws, "Fee earned"))
    assert cell.value == W.DASH
    assert "no fee figures" in cell.comment.text


def test_a_pending_award_fee_period_has_no_amount():
    burn = _burn()
    fp = burn["clins"][0]["fee_position"]
    fp.update(
        basis="base_plus_award",
        award_pool=100_000.0,
        award_earned=25_000.0,
        award_available=75_000.0,
        periods_total=2,
        periods_determined=1,
        periods=[
            {
                "name": "Period 1",
                "start": "2025-10-01",
                "end": "2026-03-31",
                "pool_share": 0.5,
                "status": "determined",
                "determined_amount": 25_000.0,
                "score": 88,
            },
            {
                "name": "Period 2",
                "start": "2026-04-01",
                "end": "2026-09-30",
                "pool_share": 0.5,
                "status": "pending",
                "determined_amount": None,
                "score": None,
            },
        ],
    )
    ws = _wb(burn)["Fee position"]
    cells = {}
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value in ("Period 1", "Period 2"):
            cells[ws.cell(r, 1).value] = ws.cell(r, 6)
    assert cells["Period 1"].value == 25_000.0
    assert cells["Period 2"].value == W.DASH
    assert "not determined" in cells["Period 2"].comment.text


def test_the_people_sheet_states_what_it_does_not_publish():
    """Per-person cost, revenue and fee contribution is #150 and is not derived here."""
    ws = _wb(_burn())["By person"]
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "#150" in text
    assert ws.cell(2, _col(ws, "Priced by")).value == "Category (LCAT) direct rate"


def test_funding_history_computes_its_own_running_total():
    """Stated cumulative and computed running total sit side by side because those two
    disagreeing is a real, recurring extraction defect (#128/#129)."""
    ws = _wb(_burn())["Funding history"]
    header = next(
        r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "Modification"
    )
    first = header + 1
    assert ws.cell(first + 1, 5).value == 1_000_000.0
    assert ws.cell(first + 1, 6).value == f"=SUM($D${first}:D{first + 1})"


def test_portfolio_workbook_never_sums_a_withheld_column_into_a_number():
    wb = W.build_portfolio_workbook(
        [({"id": 1, "sync_seed": 7}, _burn()), ({"id": 2}, _burn(level_2=False))]
    )
    ws = wb["Portfolio"]
    header = 3
    cost = next(
        c for c in range(1, ws.max_column + 1) if ws.cell(header, c).value == "Cost"
    )
    assert ws.cell(4, cost).value == 620_000.0
    assert ws.cell(5, cost).value == W.DASH
    assert ws.cell(4, ws.max_column).value.startswith("SIMULATED")
    assert ws.cell(5, ws.max_column).value == "imported"
