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
            "lcat_issues": [],
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
        people,
        [{"id": "0002", "base_status": "over", "planned_lcat_hours": {"Engineer": 24}}],
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


def test_underburning_clins_never_propose_staffing_reductions():
    people = [
        {
            "id": "e1",
            "name": "Aisha Khan",
            "cells": {"0002": {"hours": 40, "rate": 200, "lcat": "Engineer"}},
        }
    ]

    assert (
        allocation._staffing_moves(
            people,
            [
                {
                    "id": "0002",
                    "base_status": "under",
                    "planned_lcat_hours": {"Engineer": 0},
                }
            ],
        )
        == []
    )


def test_trim_moves_land_on_whole_hours_and_reconcile_to_the_plan():
    people = [
        {
            "id": "e1",
            "name": "Aisha Khan",
            "cells": {"0002": {"hours": 20, "rate": 200, "lcat": "Engineer"}},
        },
        {
            "id": "e2",
            "name": "Wei Chen",
            "cells": {"0002": {"hours": 20, "rate": 200, "lcat": "Engineer"}},
        },
    ]

    moves = allocation._staffing_moves(
        people,
        [{"id": "0002", "base_status": "over", "planned_lcat_hours": {"Engineer": 27}}],
    )

    assert sum(move["to_hours"] for move in moves) == 27
    assert all(float(move["to_hours"]).is_integer() for move in moves)


def test_lcat_issue_carries_a_verified_reassignment_destination_not_a_guess():
    people = [
        {
            "id": "e1",
            "name": "Wei Chen",
            "lcat": "Engineer I",
            "cells": {
                "0002": {
                    "unmatched": True,
                    "lcat": "Engineer I",
                    "cause": "priced_elsewhere",
                    "priced_on": "0003",
                    "suggestion": None,
                }
            },
        }
    ]
    moves = [
        {
            "employee_id": "e1",
            "clin": "0002",
            "kind": "roll_off",
            "from_hours": 24,
            "to_hours": 0,
            "clears_lcat_flag": False,
            "weekly_savings": 4800,
        }
    ]

    issue = allocation._person_heat(people, moves)[0]["lcat_issues"][0]

    assert issue == {
        "clin": "0002",
        "lcat": "Engineer I",
        "cause": "priced_elsewhere",
        "priced_on": "0003",
        "suggestion": None,
    }
