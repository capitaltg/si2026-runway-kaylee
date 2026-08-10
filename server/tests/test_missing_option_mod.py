"""Timesheet activity cannot substitute for an option-exercise SF-30."""

from copy import deepcopy

from app import burn


def _contract():
    return {
        "id": 1675,
        "contract": {
            "piid": "W45983-24-C-1675",
            "contract_type": "Firm-Fixed-Price",
            "total_ceiling": 5_960_218.40,
            "total_obligated": 3_037_736.80,
        },
        "periods": [
            {
                "name": "Base",
                "pop_start": "2024-09-24",
                "pop_end": "2025-09-23",
                "exercised": True,
                "ceiling": 3_037_736.80,
            },
            {
                "name": "Option 1",
                "pop_start": "2025-09-24",
                "pop_end": "2026-09-23",
                "exercised": False,
                "ceiling": 2_922_481.60,
            },
        ],
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Base labor",
                "type": "FFP",
                "is_labor": True,
                "ceiling": 2_866_736.80,
                "obligated": 2_866_736.80,
                "est_hours": 20_000,
            },
            {
                "clin": "0002",
                "period": "Base",
                "title": "Base travel",
                "type": "COST",
                "is_labor": False,
                "ceiling": 171_000.00,
                "obligated": 171_000.00,
            },
            {
                "clin": "1001",
                "period": "Option 1",
                "title": "Option 1 labor",
                "type": "FFP",
                "is_labor": True,
                "ceiling": 2_751_481.60,
                "obligated": None,
                "est_hours": 20_000,
            },
            {
                "clin": "1002",
                "period": "Option 1",
                "title": "Option 1 travel",
                "type": "COST",
                "is_labor": False,
                "ceiling": 171_000.00,
                "obligated": None,
            },
        ],
        "obligation_history": [],
    }


def _row(code="1001", week="2026-01-09", hours=40):
    return {
        "charge_code": code,
        "labor_category": "Software Engineer",
        "total_hours": hours,
        "week_ending": week,
        "employee_id": "e1",
    }


def test_option_clin_activity_flags_the_missing_exercise_mod():
    contract = _contract()
    payload = burn.compute(contract, [_row()])

    assert payload["contract"]["missing_option_mods"] == [
        {"period": "Option 1", "clins": ["1001"]}
    ]
    assert contract["periods"][1]["exercised"] is False
    assert contract["clins"][2]["obligated"] is None
    assert payload["contract"]["obligated"] == 3_037_736.80


def test_activity_dated_in_option_window_flags_even_when_code_is_unmapped():
    payload = burn.compute(_contract(), [_row(code="UNMAPPED")])

    assert payload["contract"]["missing_option_mods"] == [
        {"period": "Option 1", "clins": []}
    ]


def test_base_only_or_zero_activity_does_not_flag_an_option():
    assert (
        burn.compute(_contract(), [_row(code="0001", week="2025-01-10")])["contract"][
            "missing_option_mods"
        ]
        == []
    )
    assert (
        burn.compute(_contract(), [_row(hours=0)])["contract"]["missing_option_mods"]
        == []
    )


def test_ingested_option_exercise_history_suppresses_the_warning():
    contract = deepcopy(_contract())
    contract["obligation_history"] = [
        {
            "mod": "P00001",
            "action": "option_exercise",
            "period": "Option 1",
            "amount": 2_922_481.60,
            "cumulative_obligated": 5_960_218.40,
        }
    ]

    payload = burn.compute(contract, [_row()])

    assert payload["contract"]["missing_option_mods"] == []


def test_legacy_human_readable_option_history_also_suppresses_the_warning():
    contract = deepcopy(_contract())
    contract["obligation_history"] = [
        {"action": "Exercise option period (Option 1)", "amount": 2_922_481.60}
    ]

    assert burn.compute(contract, [_row()])["contract"]["missing_option_mods"] == []
