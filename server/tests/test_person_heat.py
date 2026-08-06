"""#83 — Flight Deck names people by avoidable staffing overrun."""

from app import allocation


def test_people_are_ranked_by_avoidable_overrun_not_gross_billing():
    people = [
        {"id": "e1", "name": "Expensive but on plan", "lcat": "Senior"},
        {"id": "e2", "name": "Wei Chen", "lcat": "Engineer"},
    ]
    moves = [
        {
            "employee_id": "e2",
            "clin": "0002",
            "kind": "trim",
            "from_hours": 32,
            "to_hours": 16,
            "clears_lcat_flag": False,
            "weekly_savings": 3200,
        }
    ]

    heat = allocation._person_heat(people, moves)

    assert heat == [
        {
            "id": "e2",
            "name": "Wei Chen",
            "lcat": "Engineer",
            "avoidable_weekly_overrun": 3200,
            "moves": moves,
        }
    ]


def test_staffing_moves_prefer_one_exact_roll_off_over_two_smaller_moves():
    # $10k/wk of engineer work is needed. The roster is at $20k/wk, so one person
    # can come off. An unmatched flag breaks ties, but cannot justify disrupting
    # two people when Aisha's one roll-off closes the gap exactly.
    people = [
        {
            "id": "e1",
            "name": "Aisha Khan",
            "cells": {
                "0002": {
                    "hours": 24,
                    "rate": 200,
                    "lcat": "Engineer",
                    "unmatched": False,
                }
            },
        },
        {
            "id": "e2",
            "name": "Wei Chen",
            "cells": {
                "0002": {
                    "hours": 16,
                    "rate": 200,
                    "lcat": "Engineer",
                    "unmatched": True,
                }
            },
        },
        {
            "id": "e3",
            "name": "Dana Yu",
            "cells": {
                "0002": {
                    "hours": 8,
                    "rate": 200,
                    "lcat": "Engineer",
                    "unmatched": False,
                }
            },
        },
    ]

    moves = allocation._staffing_moves(
        people, [{"id": "0002", "planned_lcat_hours": {"Engineer": 24}}]
    )

    assert moves == [
        {
            "employee_id": "e1",
            "clin": "0002",
            "kind": "roll_off",
            "from_hours": 24,
            "to_hours": 0,
            "clears_lcat_flag": False,
            "weekly_savings": 4800,
        },
    ]
