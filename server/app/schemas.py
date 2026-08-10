from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LaborRate(BaseModel):
    lcat: str = Field(
        description="Labor category name exactly as printed, e.g. 'Software Engineer (Mid)'"
    )
    loaded_rate: Optional[float] = Field(
        default=None,
        description="Fully-burdened / loaded billing rate in US dollars per hour",
    )
    est_hours: Optional[int] = Field(
        default=None, description="Estimated hours for this LCAT on this CLIN"
    )
    min_education: Optional[str] = Field(
        default=None, description='Minimum education, e.g. "Bachelor\'s"'
    )
    min_experience_yrs: Optional[int] = Field(
        default=None, description="Minimum years of experience"
    )
    clearance: Optional[str] = Field(
        default=None, description="Required clearance, e.g. 'Secret', 'TS/SCI'"
    )


class CLIN(BaseModel):
    clin: str = Field(description="CLIN number, e.g. '0001'")
    period: Optional[str] = Field(
        default=None,
        description="Period of performance this CLIN belongs to, e.g. 'Base' or 'Option 1'",
    )
    title: str = Field(description="Supplies/services description")
    type: Optional[str] = Field(
        default=None, description="Contract type for this CLIN, e.g. 'T&M', 'CR', 'FFP'"
    )
    is_labor: bool = Field(
        description="True if this is a labor CLIN, False for travel/ODC/materials"
    )
    ceiling: Optional[float] = Field(
        default=None, description="Not-to-exceed ceiling amount in dollars"
    )
    obligated: Optional[float] = Field(
        default=None,
        description="Dollars obligated/funded to this CLIN per the Accounting and "
        "Appropriation Data (ACRN) block, summed across every ACRN row citing this "
        "CLIN. Null if the award prints no per-CLIN funding.",
    )
    acrn: Optional[str] = Field(
        default=None,
        description="Accounting Classification Reference Number funding this CLIN, "
        "e.g. 'AA'. A CLIN funded from more than one appropriation carries every "
        "citation, comma-separated ('AA, AB') — one CLIN, never repeated per ACRN.",
    )
    est_hours: Optional[int] = Field(
        default=None, description="Estimated labor hours, if a labor CLIN"
    )
    labor_rates: Optional[List[LaborRate]] = Field(
        default=None,
        description="For a labor CLIN, the fully-burdened Labor Rate Table if the "
        "award prints one (LCAT + loaded rate/hr + est. hours + qualifications). "
        "Null for non-labor CLINs or when no rate table is present.",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Your extraction confidence for this CLIN row as a 0.0-1.0 number "
        "(how certain you are the CLIN number, type, and ceiling were read correctly). "
        "Lower it when a value is ambiguous, spans a page break, or had to be inferred.",
    )


class Period(BaseModel):
    name: str = Field(description="Period name, e.g. 'Base', 'Option 1'")
    pop_start: Optional[str] = Field(
        default=None, description="Period of performance start date, ISO 8601"
    )
    pop_end: Optional[str] = Field(
        default=None, description="Period of performance end date, ISO 8601"
    )
    exercised: bool = Field(
        description="True if this period has been exercised/awarded"
    )
    ceiling: Optional[float] = Field(
        default=None, description="Total ceiling for this period in dollars"
    )


class ContractHeader(BaseModel):
    piid: str = Field(
        description="Contract / Procurement Instrument Identifier (the contract number)"
    )
    agency: Optional[str] = Field(default=None, description="Awarding agency")
    contractor: Optional[str] = Field(
        default=None, description="Contractor / awardee name"
    )
    contract_type: Optional[str] = Field(
        default=None, description="Overall contract type"
    )
    total_ceiling: Optional[float] = Field(
        default=None, description="Total contract ceiling, all periods, in dollars"
    )
    total_obligated: Optional[float] = Field(
        default=None, description="Total funding obligated to date, in dollars"
    )
    incrementally_funded: Optional[bool] = Field(
        default=None, description="True if incrementally funded (obligated < ceiling)"
    )
    mod_in_progress: Optional[bool] = Field(
        default=None,
        description="True if a funding modification (e.g. a pending SF-30) is "
        "outstanding. Reframes the funding tripwire to 'funding request "
        "outstanding' rather than an over-ceiling alarm (#22).",
    )
    effective_date: Optional[str] = Field(
        default=None, description="Contract effective date, ISO 8601"
    )
    contracting_officer: Optional[str] = Field(
        default=None, description="Contracting Officer name"
    )
    cor: Optional[str] = Field(
        default=None, description="Contracting Officer's Representative name"
    )
    # Flat map (not a nested model): Bedrock's constrained decoding hangs on a
    # deeply nested optional sub-model here. The model rarely fills this anyway;
    # confidence.py owns the header scores via signal-based baselines.
    field_confidence: Optional[Dict[str, float]] = Field(
        default=None,
        description="Optional per-field extraction confidence, 0.0-1.0, keyed by "
        "header field name (piid, agency, contractor, contract_type, total_ceiling, "
        "total_obligated, effective_date, contracting_officer).",
    )


class Extraction(BaseModel):
    contract: ContractHeader
    periods: List[Period]
    clins: List[CLIN]


class FundingLine(BaseModel):
    """One CLIN-level obligation recorded by a modification.

    A mod states its money twice — once as a contract total, once broken out by
    CLIN and ACRN — and only the breakout says which line item the dollars landed
    on. An award form can only ever report what its own signature obligated, so
    for any contract past its award (an exercised option year especially) this is
    the only document-backed route to current per-CLIN funding.
    """

    clin: str = Field(
        description="CLIN the dollars are obligated to, e.g. '1001'. Unlike the CLIN "
        "schedule, the same CLIN may appear on several lines here — one per ACRN "
        "citing it — and each line states only its own increment."
    )
    acrn: Optional[str] = Field(
        default=None,
        description="Accounting Classification Reference Number cited for this "
        "line, e.g. 'AB'",
    )
    amount: float = Field(
        description="Dollars obligated to this CLIN BY THIS ACTION (the "
        "increment for this line, not a cumulative total)"
    )


class Modification(BaseModel):
    """One contract modification (SF-30) — a single dated funding action against
    an already-awarded contract. Flat on purpose: the whole obligation history is
    rebuilt by ingesting a *stack* of these one doc at a time, so each extraction
    is one self-contained record (no nested list to trip Bedrock's constrained
    decoding, per the note on ContractHeader.field_confidence).

    An SF-30's Block 14 narrative carries everything a funding-pace read needs:
    the PIID being modified, the mod number, the effective date, the dollars
    obligated by this action, and the resulting cumulative obligated."""

    piid: str = Field(
        description="Contract/order number being modified (SF-30 block 10A)"
    )
    # (The "flat on purpose" note above still holds for the *action* itself —
    # one record per document. `funding_lines` below is the one nesting that
    # earns its keep: without it a mod's money can only be known as a contract
    # total, and per-CLIN funding on any contract past its award is exactly what
    # the mod trail is for.)
    mod_number: str = Field(
        description="Amendment/modification number, e.g. 'P00001' (SF-30 block 2)"
    )
    effective_date: Optional[str] = Field(
        default=None, description="Modification effective date, ISO 8601"
    )
    amount_obligated: Optional[float] = Field(
        default=None,
        description="Dollars obligated BY THIS ACTION (the increment), in dollars",
    )
    prev_obligated: Optional[float] = Field(
        default=None,
        description="Cumulative obligated BEFORE this action, if the narrative "
        "states the 'from' figure (e.g. 'increased from $X to $Y')",
    )
    cumulative_obligated: Optional[float] = Field(
        default=None,
        description="Total cumulative obligated AFTER this action, in dollars",
    )
    total_ceiling: Optional[float] = Field(
        default=None,
        description="Total contract ceiling as restated on the mod, for cross-check",
    )
    action_type: Optional[str] = Field(
        default=None,
        description="One of: 'incremental_funding', 'option_exercise', "
        "'administrative' — inferred from the modification narrative",
    )
    is_bilateral: Optional[bool] = Field(
        default=None,
        description="True if a bilateral supplemental agreement (both parties "
        "sign, block 13C); False if a unilateral change order (CO only, 13A)",
    )
    funding_lines: Optional[List[FundingLine]] = Field(
        default=None,
        description="The per-CLIN breakout of THIS action's money, if the mod "
        "prints one — the ACCOUNTING AND APPROPRIATION DATA block and/or a "
        "'funds are obligated by CLIN as follows' clause in the narrative. Null "
        "when the mod states only a contract-level figure.",
    )
    period_exercised: Optional[str] = Field(
        default=None,
        description="For an option_exercise action, the period of performance "
        "this mod brings into effect, named as the document names it, e.g. "
        "'Option Year 1'. Null for any other action type.",
    )
    description: Optional[str] = Field(
        default=None, description="The modification narrative (SF-30 block 14)"
    )


class ExpenseIn(BaseModel):
    clin: str = Field(
        description="The non-labor CLIN this actual charges to, e.g. '0003'"
    )
    date: Optional[str] = Field(default=None, description="Expense date, ISO 8601")
    description: Optional[str] = Field(
        default=None, description="Free-text description"
    )
    category: Optional[str] = Field(
        default=None,
        description="Travel / ODC / Materials / Subcontractor / Other",
    )
    amount: float = Field(description="Amount in US dollars")
