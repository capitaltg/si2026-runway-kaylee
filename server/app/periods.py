"""Identity rules for the two things an award schedule names: periods and CLINs.

These lived in `main.py` because the mod-ingest path was the only caller. It is not
any more — `burn.py` now has to answer the same question ("is this option in force?")
to warn about a missing option-exercise SF-30, and it cannot import `main`. Two
copies of a matching rule is worse than a module: the copies drift, and the one that
drifted is the one that decides whether a warning fires.
"""

import re
from typing import Optional, Set


def key(name) -> Optional[str]:
    """A period's identity. Award and mod name the same period in different words —
    'Option Year 1' vs 'Option 1' vs 'OY1' — so match on base-or-which-option, not
    text.

    Applied to a whole sentence it still lands on the period that sentence is about:
    'Exercise option period (Option 1)' keys to `option1`, which is what lets a
    free-text action be read for the period it names.
    """
    s = str(name or "").strip().lower()
    if not s:
        return None
    if "base" in s:
        return "base"
    digits = re.search(r"(\d+)", s)
    if digits and ("option" in s or s.startswith("oy")):
        return f"option{int(digits.group(1))}"
    return re.sub(r"[^a-z0-9]", "", s) or None


def clin_key(num) -> str:
    """A CLIN's identity for matching a mod's funding line to an award CLIN.

    Case- and whitespace-insensitive, nothing more. Deliberately NOT the "slot"
    normalisation burn.py uses for cross-period line-item identity: 0001 and 1001
    are the same line item in different years, and a mod that funds 1001 must not
    be allowed to land on 0001.
    """
    return str(num or "").strip().upper()


def is_option_exercise(action) -> bool:
    """Whether an obligation-history action put an option into force.

    `action` comes from the extraction's `action_type`, which the prompt *asks* to be
    one of three values but does not constrain — so a model that answered 'Option
    Exercise' or 'exercise option period' wrote a real option exercise that an
    equality check silently drops, leaving the period un-exercised and the burn clock
    anchored to a closed year. Both words present is the test; 'incremental_funding'
    and 'administrative' carry neither.
    """
    s = str(action or "").strip().lower()
    return "option" in s and ("exercis" in s or s == "option_exercise")


def exercised_keys(contract: dict) -> Set[str]:
    """The keys of every period the contract's obligation history puts into force.

    Three independent reads, because any of them can be the only one present: the
    action's own `period` field, the periods owning the CLINs the money actually
    landed on (money on 1001 is an Option Year 1 obligation whatever the prose says),
    and the period the action text names when it is free prose rather than a code.
    """
    clin_period = {
        clin_key(c.get("clin")): key(c.get("period"))
        for c in (contract.get("clins") or [])
        if (c.get("period") or "").strip()
    }

    found = set()
    for h in contract.get("obligation_history") or []:
        action = h.get("action")
        if not is_option_exercise(action):
            continue
        found.add(key(h.get("period")))
        for line in h.get("funding_lines") or []:
            found.add(clin_period.get(clin_key(line.get("clin"))))
        # Harmless when the text names no option: 'option_exercise' keys to
        # `optionexercise`, which matches no period on any schedule.
        found.add(key(action))
    found.discard(None)
    return found
