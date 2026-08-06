"""#83 — Flight Deck names the people behind a hot labor read."""

from app import allocation


def _contract():
    return {
        "id": 1,
        "contract": {"piid": "HOT-1", "total_ceiling": 20_000_000},
        "utilization_target": 0.8,
        "clins": [
            {
                "clin": "0001",
                "title": "Labor",
                "is_labor": True,
                "ceiling": 20_000_000,
                "est_hours": 100_000,
                "labor_rates": [{"lcat": "Engineer", "loaded_rate": 200}],
            }
        ],
        "periods": [],
    }


def _rows(employee_id, employee, hours):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Engineer",
            "total_hours": hours,
            "week_ending": f"2026-01-{2 + 7 * week:02d}",
            "employee": employee,
            "employee_id": employee_id,
        }
        for week in range(6)
    ]


def test_person_over_expected_hours_is_returned_with_the_dollar_impact():
    # This fails if the Flight Deck falls back to a CLIN-level reading: Aisha's
    # 48 hours against a 32-hour contract target must remain attributable to her.
    result = allocation.compute_allocation(
        _contract(), _rows("e1", "Aisha Khan", 48) + _rows("e2", "Wei Chen", 24)
    )

    hot = result["hot_people"]
    assert [person["name"] for person in hot] == ["Aisha Khan"]
    assert hot[0]["weekly_dollars"] == 9600.0
    assert hot[0]["reasons"] == [
        {
            "kind": "over_expected",
            "hours": 48.0,
            "expected_hours": 32.0,
            "over_hours": 16.0,
        }
    ]


def test_hot_people_keep_off_pace_and_acceleration_reasons_separate():
    # A combined score would lose the remedy: this person is both driving an
    # overrun and accelerating, which are distinct facts a PM needs to see.
    people = [
        {
            "id": "e1",
            "name": "Aisha Khan",
            "lcat": "Engineer",
            "hours": 25,
            "expected": {"hours": 32},
            "cells": {"0001": {"hours": 25, "rate": 200, "cost_known": False}},
        },
        {
            "id": "e2",
            "name": "Wei Chen",
            "lcat": "Engineer",
            "hours": 20,
            "expected": {"hours": 32},
            "cells": {"0001": {"hours": 20, "rate": 200, "cost_known": False}},
        },
    ]
    heat = allocation._person_heat(
        people,
        [{"id": "0001", "base_status": "over"}],
        {"e1": {"0001": {"2026-01-09": 2_000, "2026-01-16": 5_000}}},
    )

    assert [person["name"] for person in heat] == ["Aisha Khan", "Wei Chen"]
    assert heat[0]["reasons"] == [
        {
            "kind": "off_pace_share",
            "clin": "0001",
            "weekly_dollars": 5000,
            "share": 0.5556,
        },
        {
            "kind": "accelerating",
            "clin": "0001",
            "weekly_dollars": 5000,
            "prior_weekly_dollars": 2000,
            "increase": 3000,
        },
    ]


def test_negative_fee_requires_a_real_cost_and_never_uses_the_fallback():
    people = [
        {
            "id": "e1",
            "name": "Aisha Khan",
            "lcat": "Engineer",
            "hours": 20,
            "expected": {"hours": 32},
            "cells": {
                "0001": {
                    "hours": 20,
                    "rate": 100,
                    "cost_rate": 125,
                    "cost_known": True,
                },
                "0002": {
                    "hours": 20,
                    "rate": 100,
                    "cost_rate": 125,
                    "cost_known": False,
                },
            },
        }
    ]

    heat = allocation._person_heat(people, [], {})

    assert heat[0]["reasons"] == [{"kind": "negative_fee", "weekly_loss": 500}]
