"""#158 — an hour is priced by the rate set covering the week it was worked.

Rates were stored with a fiscal year from #77 onward and then read back through a
map keyed by pool alone, so a contract holding two years kept one arbitrary rate per
pool and repriced its entire history with it. A period of performance that crosses
October 1 — which is most of them — got one year's overhead applied to both halves,
and which year won depended on row order.

The promises here:

  * two stored years both survive the read, and each half of a straddling PoP is
    costed with its own rates;
  * a contract with one rate set behaves exactly as it did before, to the cent;
  * the dateless callers (the rates panel, the buildup preview) get the newest year
    deterministically instead of whatever the merge saw last;
  * a week outside every stored year still gets costed, by a declared fallback
    rather than by dropping to Level 1.
"""

import pytest

from app import burn, db, rates


def _pools(oh, fy):
    return [
        {"pool": rates.FRINGE, "rate": 0.30, "base": None, "fiscal_year": fy},
        {"pool": rates.OVERHEAD, "rate": oh, "base": None, "fiscal_year": fy},
        {"pool": rates.GNA, "rate": 0.10, "base": None, "fiscal_year": fy},
    ]


def _direct(fy, rate=50.0):
    return [
        {
            "lcat": "Software Engineer",
            "employee_id": None,
            "rate": rate,
            "fiscal_year": fy,
        }
    ]


# FY25 overhead 40%, FY26 overhead 60% — a 20-point movement, far larger than a real
# true-up, so a wrong year cannot hide inside rounding.
_FY25 = _pools(0.40, "2025")
_FY26 = _pools(0.60, "FY26")  # deliberately the other spelling of a year


def _schedule():
    return rates.schedule_from_rows(_FY25 + _FY26, _direct("2025") + _direct("FY26"))


def _cost_per_hour(oh):
    """$50 direct through fringe 30% → overhead → G&A 10%."""
    return 50.0 * 1.30 * (1 + oh) * 1.10


# ----------------------------------------------------------------- year resolution


def test_normalize_fiscal_year_reads_every_spelling_as_one_year():
    for label in ("2026", "FY26", "FY2026", "FY 26", " fy2026 "):
        assert rates.normalize_fiscal_year(label) == "2026"
    assert rates.normalize_fiscal_year(None) is None
    assert rates.normalize_fiscal_year("") is None


def test_fiscal_year_of_turns_over_on_october_first():
    assert rates.fiscal_year_of("2025-09-30") == "2025"
    assert rates.fiscal_year_of("2025-10-01") == "2026"
    assert rates.fiscal_year_of(None) is None


def test_schedule_keeps_both_years_instead_of_collapsing_them():
    s = _schedule()
    assert s.fiscal_years == ["2025", "2026"]
    assert s.for_year("2025").rate_set.rate_of(rates.OVERHEAD).rate == 0.40
    assert s.for_year("FY26").rate_set.rate_of(rates.OVERHEAD).rate == 0.60


def test_a_week_picks_its_own_years_rates():
    s = _schedule()
    # Same contract, one day apart, across the federal year boundary.
    assert s.for_week("2025-09-26").rate_set.fiscal_year == "2025"
    assert s.for_week("2025-10-03").rate_set.fiscal_year == "2026"


def test_unknown_years_fall_back_to_the_nearest_set_not_to_level_one():
    s = _schedule()
    # Later than anything stored: rates carry forward until superseded.
    assert s.for_week("2028-01-02").rate_set.fiscal_year == "2026"
    # Earlier than anything stored: still costed, by the closest set we hold.
    assert s.for_week("2019-01-04").rate_set.fiscal_year == "2025"
    # Both keep a real buildup rather than degrading to the billing-rate fallback.
    assert s.for_week("2028-01-02").margin_available


def test_dateless_callers_get_the_newest_year_deterministically():
    s = _schedule()
    assert s.rate_set.fiscal_year == "2026"
    # Row order must not decide it — the old bug was exactly this.
    flipped = rates.schedule_from_rows(_FY26 + _FY25, _direct("FY26") + _direct("2025"))
    assert flipped.rate_set.fiscal_year == "2026"


def test_schedule_reads_as_a_cost_model_for_callers_with_no_date():
    s = _schedule()
    assert s.level == rates.LEVEL_CATEGORY_COST
    assert s.margin_available
    assert s.payload()["rate_set"]["fiscal_year"] == "2026"
    assert s.cost_for("Software Engineer", 200.0).known


def test_undated_rows_apply_to_every_year():
    # A fringe rate entered before the user knew about fiscal years must not vanish
    # from a year that only names overhead.
    s = rates.schedule_from_rows(
        [{"pool": rates.FRINGE, "rate": 0.30, "fiscal_year": None}]
        + [{"pool": rates.OVERHEAD, "rate": 0.40, "fiscal_year": "2025"}],
        _direct(None),
    )
    fy25 = s.for_year("2025").rate_set
    assert fy25.rate_of(rates.FRINGE).rate == 0.30
    assert fy25.rate_of(rates.OVERHEAD).rate == 0.40


# --------------------------------------------------------------- through the engine

_CONTRACT = {
    "id": 1,
    "contract": {
        "piid": "TEST-158",
        "total_ceiling": 1_000_000,
        "total_obligated": 1_000_000,
    },
    "clins": [
        {
            "clin": "0001",
            "period": "Base",
            "title": "Services",
            "is_labor": True,
            "type": "CPFF",
            "ceiling": 1_000_000,
            "est_hours": 5_000,
        }
    ],
    "periods": [{"name": "Base", "pop_start": "2025-07-01", "pop_end": "2026-06-30"}],
}

# Four weeks either side of October 1 2025 — the FY25/FY26 boundary.
_ROWS = [
    {
        "charge_code": "0001",
        "labor_category": "Software Engineer",
        "total_hours": 40,
        "week_ending": wk,
        "employee_id": "e1",
    }
    for wk in ("2025-09-05", "2025-09-12", "2025-10-03", "2025-10-10")
]


def _cost(model):
    return burn.compute(_CONTRACT, _ROWS, cost_model=model)["clins"][0]["cost"]


def test_hours_spanning_two_fiscal_years_are_priced_by_their_own_year():
    got = _cost(_schedule())
    want = 2 * 40 * _cost_per_hour(0.40) + 2 * 40 * _cost_per_hour(0.60)
    assert got == round(want, 2)
    # And it is genuinely between the two single-year answers, so neither year
    # silently won the whole span.
    lo = _cost(rates.schedule_from_rows(_FY25, _direct("2025")))
    hi = _cost(rates.schedule_from_rows(_FY26, _direct("FY26")))
    assert lo < got < hi


def test_a_single_year_contract_is_unchanged():
    # The regression bar: one rate set must cost exactly what it always did, whether
    # it arrives as a schedule or as a plain CostModel.
    plain = rates.model_from_rows(_FY26, _direct("FY26"))
    assert _cost(rates.schedule_from_rows(_FY26, _direct("FY26"))) == _cost(plain)
    assert _cost(plain) == round(4 * 40 * _cost_per_hour(0.60), 2)


# ------------------------------------------------------------------ the read path


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    db.save_rate_pools(1, "2025", _pools(0.40, None))
    db.save_rate_pools(1, "FY26", _pools(0.60, None))
    return 1


def test_stored_years_both_survive_the_read(store):
    rows = db.get_rate_rows(store)
    overheads = sorted(r["rate"] for r in rows["pools"] if r["pool"] == rates.OVERHEAD)
    assert overheads == [0.40, 0.60]


def test_the_dateless_read_returns_one_year_and_says_which(store):
    view = db.get_rate_model(store)
    assert view["fiscal_year"] == "2026"
    assert view["fiscal_years"] == ["2025", "2026"]
    assert [r["rate"] for r in view["pools"] if r["pool"] == rates.OVERHEAD] == [0.60]
    # And an older year is still reachable on request, which is what #87 trues up.
    assert db.get_rate_model(store, "2025")["fiscal_year"] == "2025"


def test_company_defaults_still_fill_per_pool_gaps_within_a_year(store):
    # Company-wide G&A, contract-specific overhead — the #77 merge, now year-aware.
    db.save_rate_pools(None, "2025", [{"pool": rates.GNA, "rate": 0.15}])
    db.save_rate_pools(2, "2025", [{"pool": rates.OVERHEAD, "rate": 0.55}])
    pools = {r["pool"]: r["rate"] for r in db.get_rate_model(2, "2025")["pools"]}
    assert pools[rates.OVERHEAD] == 0.55
    assert pools[rates.GNA] == 0.15


def test_level_one_is_untouched():
    # No rates at all: cost falls back to billings and stays flagged as such.
    card = burn.compute(_CONTRACT, _ROWS, cost_model=rates.schedule_from_rows([], []))[
        "clins"
    ][0]
    assert card["cost"] == card["billings"]
    assert not card["cost_known"]
