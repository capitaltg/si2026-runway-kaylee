"""LCAT → rate-line resolution (#64).

Matching a timesheet's labor category to an award's rate line is the one join in
the app where both sides arrive from different machines: the award side comes out
of PDF extraction (`extract.py`), the timesheet side arrives as clean CSV. Small
divergences between them are expected, not exceptional.

The engine used to do that join with one line — exact, lowercased, stripped
equality — and report every failure as one bare flag: an LCAT in
`unmatched_lcats`, a red cell in the allocation matrix, a dead-end ⚠ tooltip. In
testing that fired for nearly every "Senior Cyber SME" on a contract, and the
honest reaction was *"why are they generated in there if there is not something
for them?"* — a question the flag could not answer, because **three different
failures rendered identically**:

  A. the CLIN has no rate table at all (an SF-26 face ingested without its
     continuation sheet) — one document-level fact, wrongly retold as N
     per-person alarms
  B. the LCAT is priced, but on a *different* CLIN than the one being charged
  C. the strings genuinely differ ("Sr. Cyber SME" vs "Senior Cyber SME") or the
     rate line is really absent

This module makes that distinction computable. It answers, for one (CLIN, LCAT)
pair: did we resolve a rate, and if not, *which* of the three, and what would fix
it. `burn.py` and `allocation.py` both resolve rates through here, so the Flight
Deck and the allocation matrix can never disagree about why a cell is flagged.

Two rules it exists to enforce — the same posture `pricing.py` takes:

**Normalise deterministically, suggest fuzzily, never auto-apply a guess.**
Case, punctuation, honorifics and trailing parentheticals are *notation*, so
folding them and billing on the result is safe (`normalize`). Anything softer
than that — edit-distance similarity — only ever produces a *suggestion* for a
human to confirm (`suggest`), because a fuzzy match applied silently would move
spend-to-date on a contract without anyone having agreed to it.

**Ambiguity is reported, never resolved.** If two rate lines on a CLIN normalise
to the same key at different rates, this module refuses the match and says so
(`AMBIGUOUS`). Picking the cheaper or the first would be inventing a number.
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Why an LCAT charged on a CLIN did not resolve to one of that CLIN's rate lines.
# These are the values the UI branches on, so they're strings rather than an enum
# (they travel through JSON) and they're named for the cause, not the symptom.
#
#   RATE_TABLE_MISSING — cause A. A property of the CLIN, not of the person. The
#     UI must show this once per CLIN; N red cells for one missing PDF page trains
#     people to ignore red.
#   PRICED_ELSEWHERE   — cause B. Names the CLIN that does price it. Legitimate on
#     some contracts (a person genuinely charging an unpriced line) and a data bug
#     on others, so it is surfaced with the fact and not a verdict.
#   AMBIGUOUS          — two rate lines, one normalised key, different rates.
#   NO_RATE_LINE       — cause C. Carries the closest candidate when there is one.
RATE_TABLE_MISSING = "clin_unpriced"
#   RATE_TABLE_UNBURDENED — cause A's other half (#139). Also a property of the
#     CLIN, and also stated once, but the rate schedule is already ingested: it
#     prices each category at an unburdened direct rate. No import fixes it.
RATE_TABLE_UNBURDENED = "clin_unburdened"
PRICED_ELSEWHERE = "priced_elsewhere"
AMBIGUOUS = "ambiguous_rate_line"
NO_RATE_LINE = "no_rate_line"

# How the rate that *was* used got resolved. `blended` and `none` are the two
# not-really-matched outcomes and always carry a `cause`.
VIA_EXACT = "exact"
VIA_NORMALIZED = "normalized"
VIA_ALIAS = "alias"
VIA_BLENDED = "blended"
VIA_NONE = "none"
#   VIA_BURDENED — a real rate line backed this hour, but the award printed no rate
#     to bill from: the $/hr is that category's direct rate carried through the
#     contract's own indirect pools (#144). Reported apart from VIA_EXACT because
#     it is a match on a derived number, and a reader must be able to see that.
VIA_BURDENED = "burdened"

# What a resolved rate line's $/hr actually is (#144). `loaded` is a figure the
# award printed. `burdened` is one built from the award's own cost buildup — this
# category's direct rate carried through the contract's fringe, overhead and G&A —
# for the cost-type CLINs where the award prints no rate to bill from at all.
#
# Kept on the line, and reported, because the two are not interchangeable: a user
# looking at $123.14/hr for a Business Analyst is entitled to know the document
# never printed $123.14, and an accountant is entitled to know which indirect rates
# produced it. It is still a rate the award fully determines — every input is on
# the page — which is what makes it a rate line rather than an assumption.
BASIS_LOADED = "loaded"
BASIS_BURDENED = "burdened"

# Token-level folding applied before matching. Deliberately short and boring:
# every entry here changes what money a timesheet row bills at, so the bar is
# "this is two spellings of one thing in federal labor-category usage", not "these
# are probably the same job". Multi-word expansions are allowed (the result is
# re-tokenised), which is what lets "PM" and "Program Manager" fold together.
_SYNONYMS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "jnr": "junior",
    "pm": "program manager",
    "pgm": "program",
    "prog": "program",
    "mgr": "manager",
    "mgmt": "management",
    "sme": "subject matter expert",
    "engr": "engineer",
    "eng": "engineer",
    "anlst": "analyst",
    "admin": "administrator",
    "assoc": "associate",
    "spec": "specialist",
    "tech": "technician",
    "arch": "architect",
    "dev": "developer",
    "sys": "systems",
    "sw": "software",
    "hw": "hardware",
    "info": "information",
    "sec": "security",
    "asst": "assistant",
    "prin": "principal",
    "lvl": "level",
    # Roman numerals for LCAT levels. "Engineer III" and "Engineer 3" are the same
    # line item on every rate schedule that prints both, and levels above V are
    # vanishingly rare in practice, so the table stops there rather than shipping a
    # general numeral parser that could fold something it shouldn't.
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
}

# A trailing parenthetical on an LCAT is usually a qualifier on the *person* rather
# than the category — "Senior Cyber SME (TS/SCI)" and "Senior Cyber SME" are one
# rate line, and so are "Program Manager (PMP)" and "Program Manager". Only stripped
# when something survives it, so "(Unassigned)" doesn't normalise to nothing.
_TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")

# …except when the parenthetical is the *level*, which is priced. Real schedules in
# this app's own test data print "Software Engineer (Mid)" — dropping "(Mid)" would
# make a timesheet's "Software Engineer (Senior)" match the Mid rate line and bill
# senior hours at the mid rate, silently, which is the one outcome this module must
# never produce. So a level-bearing parenthetical is folded into the key as content
# instead of being discarded, and a spelling that omits the level falls through to a
# suggestion the user confirms.
#
# Checked *after* `_SYNONYMS` folding, so "(Sr)" and "(II)" arrive here as "senior"
# and "2".
_LEVEL_TOKENS = {
    "junior",
    "senior",
    "mid",
    "midlevel",
    "intermediate",
    "entry",
    "lead",
    "principal",
    "staff",
    "journeyman",
    "master",
    "expert",
    "apprentice",
    "level",
    "1",
    "2",
    "3",
    "4",
    "5",
}

# Anything that isn't a letter or a digit is punctuation for our purposes — "Sr."
# / "Sr" / "SR-" all have to land on the same token.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Minimum similarity for `suggest` to offer a candidate at all. Tuned so the
# near-misses this ticket was filed over ("Sr. Cyber SME" → "Senior Cyber SME",
# which normalisation already catches; "Cyber Security SME" → "Cybersecurity SME",
# which it doesn't) clear it, while two genuinely different categories on the same
# schedule ("Program Manager" vs "Program Analyst") do not. It only gates a
# suggestion a human then confirms, so the cost of being slightly wrong here is a
# declined offer, not a wrong number.
SUGGEST_MIN = 0.72


def normalize(lcat: Optional[str]) -> str:
    """Fold an LCAT string to its comparison key.

    Order matters: the trailing parenthetical is handled first (while the parens are
    still there to find), then punctuation becomes whitespace, then each token is
    expanded through `_SYNONYMS` and the result re-tokenised so multi-word
    expansions ("pm" → "program manager") normalise like the words they stand for.

    A trailing parenthetical is dropped when it qualifies the person (a clearance, a
    certification) and *kept* when it names the level, because the level is priced —
    see `_LEVEL_TOKENS`.

    Token *order* is preserved on purpose. Sorting the tokens would fold "Lead
    Analyst" into "Analyst Lead", which is usually right and occasionally isn't —
    and this key decides what an hour bills at. `suggest` uses an order-insensitive
    comparison instead, where being wrong costs nothing.
    """
    s = (lcat or "").strip().lower()
    if not s:
        return ""

    def fold(text: str) -> List[str]:
        out: List[str] = []
        for tok in _NON_ALNUM.sub(" ", text).split():
            out.extend(_SYNONYMS.get(tok, tok).split())
        return out

    m = _TRAILING_PAREN.search(s)
    if m and s[: m.start()].strip():
        head, inner = fold(s[: m.start()]), fold(m.group(1))
        # Level qualifiers stay (as content, after the head), everything else goes.
        return " ".join(head + inner if set(inner) & _LEVEL_TOKENS else head)
    return " ".join(fold(s))


def _token_set_ratio(a: str, b: str) -> float:
    """Order-insensitive similarity: how much of the two token *sets* overlap."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def similarity(a: Optional[str], b: Optional[str]) -> float:
    """0..1 similarity between two LCAT strings, on their normalised forms.

    The max of a sequence ratio (catches typos, elisions, "cyber security" vs
    "cybersecurity") and a token-set ratio (catches reordering). Suggestion-only —
    nothing in this module prices an hour off this number.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return max(
        difflib.SequenceMatcher(None, na, nb).ratio(),
        _token_set_ratio(na, nb),
    )


@dataclass(frozen=True)
class Floors:
    """The qualification minimums an award prints beside a rate line (#66).

    Rides on the resolved line rather than being looked up by LCAT name later, which
    matters most in the case the name lookup gets wrong: when a user-confirmed alias
    prices `Cyber Analyst III` off the `Senior Cyber SME` line, the floors that apply
    are the ones on the line that sets the rate, not the ones next to the string the
    timesheet happened to use.

    Every field is optional because every field is optional in the document. A missing
    floor is "the award asks nothing here", which `compliance.py` treats as nothing to
    check — never as a bar of zero that everyone clears.
    """

    min_education: Optional[str] = None
    min_experience_yrs: Optional[int] = None
    clearance: Optional[str] = None

    @property
    def any_printed(self) -> bool:
        return any(
            v is not None
            for v in (self.min_education, self.min_experience_yrs, self.clearance)
        )

    @classmethod
    def from_rate_line(cls, lr: Optional[dict]) -> "Floors":
        lr = lr or {}
        return cls(
            min_education=(lr.get("min_education") or None),
            min_experience_yrs=lr.get("min_experience_yrs"),
            clearance=(lr.get("clearance") or None),
        )

    def payload(self) -> dict:
        return {
            "min_education": self.min_education,
            "min_experience_yrs": self.min_experience_yrs,
            "clearance": self.clearance,
        }


@dataclass(frozen=True)
class RateLine:
    """One priced labor line: which CLIN prices it, the LCAT as the award spells
    it, and the loaded $/hr. `key` is its normalised comparison form."""

    clin: str
    lcat: str
    rate: float
    key: str
    # The award's qualification minimums for this category (#66). Defaults to an
    # empty `Floors` so every existing construction site stays valid and an award
    # that prints no minimums is indistinguishable from one nobody extracted — both
    # are "no floor", which is the honest reading of either.
    floors: "Floors" = Floors()
    # Whether `rate` is the award's printed figure or one built from its cost
    # buildup (#144). Defaults to `loaded`, so every pre-existing construction site
    # keeps describing exactly what it always described.
    basis: str = BASIS_LOADED
    # The direct rate a burdened line was built from, kept so the buildup can be
    # shown without re-deriving it. None on a loaded line, which was never built.
    direct: Optional[float] = None

    def payload(self) -> dict:
        return {
            "clin": self.clin,
            "lcat": self.lcat,
            "rate": round(self.rate, 2),
            "floors": self.floors.payload(),
            "basis": self.basis,
            "direct": round(self.direct, 2) if self.direct is not None else None,
        }


@dataclass(frozen=True)
class Resolution:
    """The result of resolving one LCAT against one CLIN's rate table.

    `rate` is what the hour bills at — a real rate line when `matched`, the CLIN's
    blended fallback when not, and None when the CLIN can't even be blended
    (`via == VIA_NONE`, the `unpriced` data-quality state #40 reports).

    `matched` is the money question: did a rate line back this rate? `cause` is the
    diagnosis when it didn't, and is always set when `matched` is False.
    """

    rate: Optional[float]
    matched: bool
    via: str
    cause: Optional[str] = None
    # The rate line that produced the rate (matched), or the one that *would* fix
    # this (PRICED_ELSEWHERE names where it's priced; NO_RATE_LINE offers the
    # closest candidate for a human to confirm).
    line: Optional[RateLine] = None
    # Fuzzy score behind a NO_RATE_LINE suggestion. None for every other outcome —
    # a score on a match would imply the match was fuzzy, and it never is.
    score: Optional[float] = None
    # Candidate lines behind an AMBIGUOUS refusal, so the UI can show what it is
    # being asked to choose between.
    candidates: Tuple[RateLine, ...] = field(default_factory=tuple)


def line_rate(lr: dict, clin: Optional[dict] = None, burden=None):
    """What one rate line bills at, as `(rate, basis)` — or `(None, None)`.

    The one place that answers this question, so the index, the resolver and
    `rate_table_state` can never disagree about whether a line is priced.

    A printed `loaded_rate` always wins: it is what the award says the hour costs
    the government, and no derivation may override a stated price. Only when there
    is none does `burden` get a say — a cost-type award prints an unburdened
    `direct_rate` per category and its indirect factors separately, which fully
    determines the rate without ever printing it (#144).

    `burden` is `fn(clin, direct) -> Optional[float]`, passed in rather than
    computed here: which pools apply, and whether burdening is even the right
    reading of this CLIN's type, are contract-scoped facts this module cannot see.
    Returning None means "not on this line", and the line stays unpriced.
    """
    loaded = lr.get("loaded_rate")
    if loaded:
        return float(loaded), BASIS_LOADED
    direct = lr.get("direct_rate")
    if direct and burden:
        built = burden(clin or {}, float(direct))
        if built:
            return float(built), BASIS_BURDENED
    return None, None


def build_index(clins: List[dict], burden=None) -> Dict[str, List[RateLine]]:
    """Normalised LCAT → the rate lines pricing it, across a set of CLINs.

    Built from the *active period's* CLINs by the callers, not the whole award: a
    rate line on an un-exercised option year prices nothing today, and offering it
    as the fix for an unmatched LCAT would send the user to a CLIN that has no
    money on it.

    `burden` is `line_rate`'s, and reaches here for the same reason it reaches the
    resolver: a burdened line is a priced line, so leaving it out of the index would
    let "priced elsewhere" and the alias picker disagree with what actually billed.
    """
    index: Dict[str, List[RateLine]] = {}
    for c in clins:
        num = str(c.get("clin") or "").strip()
        for lr in c.get("labor_rates") or []:
            name = (lr.get("lcat") or "").strip()
            rate, basis = line_rate(lr, c, burden)
            if not name or not rate:
                continue
            key = normalize(name)
            if not key:
                continue
            index.setdefault(key, []).append(
                RateLine(
                    clin=num,
                    lcat=name,
                    rate=float(rate),
                    key=key,
                    floors=Floors.from_rate_line(lr),
                    basis=basis,
                    direct=lr.get("direct_rate"),
                )
            )
    return index


def parse_aliases(raw) -> Dict[str, dict]:
    """Normalise a contract's stored LCAT alias map for lookup.

    Stored shape (on the contract blob, see `db.set_lcat_alias`) is a list of
    `{"from": ..., "lcat": ..., "clin": ...}` — the LCAT as the timesheet spells
    it, pointed at a rate line the award does price, optionally on another CLIN
    (that's the fix for cause B). Keyed here by the *normalised* source, so an
    alias written against "Sr. Cyber SME" also catches "SR CYBER SME".

    Tolerant of a dict-shaped map too, since that's the obvious thing for a future
    caller to hand us, and of junk entries, which are skipped rather than raised —
    a malformed alias must not take down a burn calculation.
    """
    out: Dict[str, dict] = {}
    if isinstance(raw, dict):
        items = [
            {"from": k, **(v if isinstance(v, dict) else {"lcat": v})}
            for k, v in raw.items()
        ]
    elif isinstance(raw, list):
        items = [a for a in raw if isinstance(a, dict)]
    else:
        return out
    for a in items:
        src = normalize(a.get("from") or a.get("source"))
        target = (a.get("lcat") or a.get("to") or "").strip()
        if not src or not target:
            continue
        out[src] = {
            "from": (a.get("from") or a.get("source") or "").strip(),
            "lcat": target,
            "clin": str(a.get("clin") or "").strip() or None,
        }
    return out


def suggest(
    lcat: Optional[str],
    index: Dict[str, List[RateLine]],
    prefer_clin: Optional[str] = None,
) -> Tuple[Optional[RateLine], Optional[float]]:
    """The closest rate line to `lcat`, or (None, None) below `SUGGEST_MIN`.

    Ties break toward `prefer_clin` (the CLIN actually being charged), because a
    mapping that keeps the money on the line it was charged to is the smaller
    change of the two.
    """
    key = normalize(lcat)
    if not key or not index:
        return None, None
    best: Optional[RateLine] = None
    best_score = 0.0
    for cand_key, lines in index.items():
        score = similarity(key, cand_key)
        if score < SUGGEST_MIN:
            continue
        for line in lines:
            better = score > best_score or (
                score == best_score
                and prefer_clin is not None
                and line.clin == prefer_clin
                and (best is None or best.clin != prefer_clin)
            )
            if better:
                best, best_score = line, score
    return (best, round(best_score, 3)) if best else (None, None)


#: A CLIN with at least one rate line we can bill from.
TABLE_PRESENT = "present"
#: Rate lines were ingested, but none carries a loaded rate — a cost-type award
#: prints unburdened direct rates per LCAT and the indirect factors separately,
#: so there is nothing for `resolver` to bill at even though the schedule is in
#: (#139). Not the same absence as `TABLE_ABSENT`, and no document fixes it.
TABLE_UNBURDENED = "unburdened"
#: No rate lines at all — the continuation sheet never landed. Importing a rate
#: schedule is the remedy.
TABLE_ABSENT = "absent"


def rate_table_state(clin: dict, burden=None) -> str:
    """Why a CLIN's rate table is or isn't usable, as one of the three labels above.

    `resolver` skips any line it cannot price, which collapses "we have no rate
    lines" and "we have rate lines priced a way we can't bill from" into one
    `source == "blended"`. Both are a rate gap; only one is a missing document, and
    the UI has to tell them apart before it names a fix.

    With a `burden` the second case largely stops existing: a direct-rate line the
    contract's own indirect pools can carry through IS a line we can bill from, so
    the table reads `present` (#144). `unburdened` then means what it now says —
    direct rates and no way to burden them, because no indirect pool was ever
    stored — and remains the state the app cannot resolve for the user.
    """
    named = [
        lr for lr in (clin.get("labor_rates") or []) if (lr.get("lcat") or "").strip()
    ]
    if any(line_rate(lr, clin, burden)[0] for lr in named):
        return TABLE_PRESENT
    if named:
        return TABLE_UNBURDENED
    return TABLE_ABSENT


def resolver(
    clin: dict,
    index: Optional[Dict[str, List[RateLine]]] = None,
    aliases: Optional[Dict[str, dict]] = None,
    burden=None,
):
    """Build the LCAT → $/hr resolver for one CLIN.

    Returns `(resolve, blended, source)`:
      * `resolve(lcat)` → `Resolution`
      * `blended` — the CLIN's fallback $/hr, `ceiling / est_hours`, which is real
        contract arithmetic and not an invention
      * `source` — `"rate_table"` / `"blended"` / `"none"`, unchanged from before
        this ticket, because the Flight Deck and the `unpriced` status read it

    `index` (all the period's rate lines, from `build_index`) is what makes cause B
    detectable; without it a miss can only be reported as an absence. `aliases` is
    the user's confirmed mappings, and is the *only* path by which a non-identical
    string moves money to another CLIN's rate line.

    Resolution order — exact, then normalised, then alias:
      1. exact, as the award spells it. Never overridden, so a contract with a
         genuinely odd-but-correct LCAT keeps billing exactly as it did.
      2. normalised on this CLIN's own table (notation folding, safe to apply).
      3. an alias the user confirmed, which may point at another CLIN's line.
    Alias comes last so a mapping written to fix an early misspelling can't
    silently outrank a rate line the award actually prints today.

    `burden` is `line_rate`'s (#144). With one, a cost-type CLIN's direct-rate lines
    become priced lines and resolve exactly as printed ones do — same order, same
    causes, same ambiguity refusal — differing only in the `basis` they carry and
    the `VIA_BURDENED` they report. Without one the behaviour is bit-for-bit what it
    was: `loaded_rate` or nothing.
    """
    index = index or {}
    aliases = aliases or {}
    table = clin.get("labor_rates") or []
    num = str(clin.get("clin") or "").strip()

    by_exact: Dict[str, float] = {}
    # The floors beside each printed line, keyed the same way the rate is, so the
    # exact-match path below can carry them without re-reading the table (#66).
    floors_by_exact: Dict[str, Floors] = {}
    # …and the same for how each rate was arrived at (#144), so an exact match can
    # say whether it landed on a printed figure or a derived one.
    line_by_exact: Dict[str, RateLine] = {}
    own_lines: Dict[str, List[RateLine]] = {}
    for lr in table:
        name = (lr.get("lcat") or "").strip()
        rate, basis = line_rate(lr, clin, burden)
        if not name or not rate:
            continue
        by_exact[name.lower()] = float(rate)
        floors_by_exact[name.lower()] = Floors.from_rate_line(lr)
        line_by_exact[name.lower()] = RateLine(
            clin=num,
            lcat=name,
            rate=float(rate),
            key=normalize(name),
            floors=Floors.from_rate_line(lr),
            basis=basis,
            direct=lr.get("direct_rate"),
        )
        key = normalize(name)
        if key:
            own_lines.setdefault(key, []).append(
                RateLine(
                    clin=num,
                    lcat=name,
                    rate=float(rate),
                    key=key,
                    floors=Floors.from_rate_line(lr),
                    basis=basis,
                    direct=lr.get("direct_rate"),
                )
            )

    ceiling = clin.get("ceiling") or 0
    est_hours = clin.get("est_hours") or 0
    blended = (ceiling / est_hours) if est_hours else None
    source = "rate_table" if by_exact else ("blended" if blended else "none")
    fallback_via = VIA_BLENDED if blended is not None else VIA_NONE

    def _unmatched(cause: str, **kw) -> Resolution:
        return Resolution(
            rate=blended, matched=False, via=fallback_via, cause=cause, **kw
        )

    def _matched(line: RateLine, via: str) -> Resolution:
        # A burdened line says so however it was reached (#144). The route — exact,
        # normalised, alias — is a matching fact; the basis is a money fact, and the
        # money fact is the one a reader must not miss, so it wins the one field
        # both would otherwise want. `line.basis` still carries the route's answer
        # for anyone who needs both.
        if line.basis == BASIS_BURDENED:
            via = VIA_BURDENED
        return Resolution(rate=line.rate, matched=True, via=via, line=line)

    def resolve(lcat: Optional[str]) -> Resolution:
        raw = (lcat or "").strip()
        key = normalize(raw)

        # 1. exact
        hit = by_exact.get(raw.lower())
        if hit is not None:
            printed = line_by_exact.get(raw.lower())
            return _matched(
                RateLine(
                    clin=num,
                    lcat=raw,
                    rate=hit,
                    key=key,
                    floors=floors_by_exact.get(raw.lower(), Floors()),
                    basis=printed.basis if printed else BASIS_LOADED,
                    direct=printed.direct if printed else None,
                ),
                VIA_EXACT,
            )

        # 2. normalised, on this CLIN only. Distinct rates behind one key is the
        #    ambiguity this module refuses rather than resolves.
        lines = own_lines.get(key) or []
        distinct = {round(ln.rate, 4) for ln in lines}
        if len(distinct) == 1:
            return _matched(lines[0], VIA_NORMALIZED)
        if len(distinct) > 1:
            return _unmatched(AMBIGUOUS, candidates=tuple(lines))

        # 3. a confirmed alias. Resolved through the index so it can legitimately
        #    land on another CLIN's rate line — and dropped, not honoured blindly,
        #    if the line it names has since gone (a re-import can change the
        #    schedule under a saved mapping). Falling through then reports the real
        #    cause instead of a rate nobody can point at.
        alias = aliases.get(key)
        if alias:
            target_key = normalize(alias["lcat"])
            for line in index.get(target_key, []):
                if alias["clin"] and line.clin != alias["clin"]:
                    continue
                return _matched(line, VIA_ALIAS)

        # Unmatched: which of the four, in order of how much it explains.
        if not by_exact:
            # Cause A splits on whether a document is actually missing (#139).
            return _unmatched(
                RATE_TABLE_UNBURDENED
                if rate_table_state(clin, burden) == TABLE_UNBURDENED
                else RATE_TABLE_MISSING
            )
        elsewhere = [ln for ln in index.get(key, []) if ln.clin != num]
        if elsewhere:
            return _unmatched(PRICED_ELSEWHERE, line=elsewhere[0])
        line, score = suggest(raw, index, prefer_clin=num)
        return _unmatched(NO_RATE_LINE, line=line, score=score)

    return resolve, blended, source


def issue_payload(lcat: str, res: Resolution, hours: float) -> dict:
    """One unmatched-LCAT row for the API: what didn't match, why, how much labour
    is riding on it, and the fix if there is one.

    `hours` is here because it is the only thing that tells a PM whether a flag is
    a rounding error or a third of the contract's labour, and the old bare
    `unmatched_lcats` list of strings could not say.
    """
    return {
        "lcat": lcat,
        "cause": res.cause,
        "hours": round(hours, 1),
        # PRICED_ELSEWHERE: where it *is* priced. NO_RATE_LINE: the closest
        # candidate, with its score, for the mapping affordance to offer.
        "priced_on": (
            res.line.clin if res.cause == PRICED_ELSEWHERE and res.line else None
        ),
        "suggestion": (
            res.line.payload() if res.cause == NO_RATE_LINE and res.line else None
        ),
        "score": res.score,
        "candidates": [c.payload() for c in res.candidates] or None,
        # What the hours billed at in the meantime, so the number on screen is
        # always traceable to a rate the user can see.
        "billed_at": round(res.rate, 2) if res.rate is not None else None,
    }
