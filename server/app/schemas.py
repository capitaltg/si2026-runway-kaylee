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
    direct_rate: Optional[float] = Field(
        default=None,
        description="Unburdened direct labor rate in US dollars per hour, as printed "
        "on a cost-buildup exhibit ('Direct Rate/Hr'). This is the wage before any "
        "indirect pool or fee. Null on an ordinary rate schedule, which prints only "
        "the loaded rate — never derive one from the loaded rate.",
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
        default=None,
        description="Contract type for this CLIN, e.g. 'T&M', 'CPFF', 'FFP'",
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
    # --- Cost and fee, as a cost-type CLIN prices them (#78) ---------------
    #
    # A cost-reimbursement award does not state one number per line. It states
    # estimated cost and fee as separate priced lines footing to the CLIN total
    # (FAR 16.306), because they behave differently: cost is reimbursed and may
    # overrun, fee is negotiated and can be lost. `ceiling` stays the CLIN total
    # and gains an identity against these — `ceiling == estimated_cost + fee` —
    # which `confidence.py` *checks* rather than assumes. When it fails, one of
    # the two figures was misread, and that is what review is for.
    #
    # Flat scalars, every one of them. `share_ratio` is a string ("50/50") and
    # not a pair of numbers for the reason on ContractHeader.field_confidence:
    # each nested structure added to this schema costs a dead Bedrock
    # grammar-compilation timeout on every ingest that uses it.
    estimated_cost: Optional[float] = Field(
        default=None,
        description="Total estimated (reimbursable) cost for this CLIN, exclusive of "
        "fee — the 'Total Estimated Cost' line on a cost-type CLIN, or 'Target Cost' "
        "on an incentive one. Null on a fixed-price CLIN, which has no cost line.",
    )
    fixed_fee: Optional[float] = Field(
        default=None,
        description="Fixed fee in dollars on a cost-plus-fixed-fee CLIN (FAR 16.306), "
        "the 'Fixed Fee' line. Not a percentage — the dollar figure.",
    )
    base_fee: Optional[float] = Field(
        default=None,
        description="Base fee in dollars on a cost-plus-award-fee CLIN (FAR "
        "16.401(e)) — the portion payable without regard to performance.",
    )
    award_fee_pool: Optional[float] = Field(
        default=None,
        description="The award fee pool in dollars on a CPAF CLIN: the at-risk "
        "amount earned only as determined under the Award Fee Plan. Kept separate "
        "from base_fee — their sum is not a fee anyone is guaranteed.",
    )
    target_fee: Optional[float] = Field(
        default=None,
        description="Target fee in dollars on a cost-plus-incentive-fee CLIN (FAR "
        "16.304), the fee earned if allowable cost lands on the target cost.",
    )
    min_fee: Optional[float] = Field(
        default=None,
        description="Minimum fee in dollars on a CPIF CLIN — the bracket the fee "
        "stops falling at on a cost overrun.",
    )
    max_fee: Optional[float] = Field(
        default=None,
        description="Maximum fee in dollars on a CPIF CLIN — the bracket the fee "
        "stops rising at on a cost underrun.",
    )
    target_profit: Optional[float] = Field(
        default=None,
        description="Target profit in dollars on a fixed-price incentive CLIN (FAR "
        "16.403). Profit, not fee: an FPI line is fixed-price, so the figure it "
        "prints is 'Target Profit' rather than a fee.",
    )
    ceiling_price: Optional[float] = Field(
        default=None,
        description="Price ceiling in dollars on an FPI CLIN (FAR 16.403) — the "
        "'Price Ceiling' line, above which the contractor bears all cost. Distinct "
        "from `ceiling`, which is this CLIN's own not-to-exceed amount.",
    )
    share_ratio: Optional[str] = Field(
        default=None,
        description="The Government/Contractor share ratio on an incentive CLIN, as "
        "printed, e.g. '50/50' or '80/20' (Government share first).",
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
    # Written by confidence.py, never by the model — it is the *reason* a
    # deterministic check lowered this row, and a model asked to explain its own
    # score will write one whether or not a check failed. Excluded from the
    # extraction prompt's schema for the same reason (see extract._parse_schema).
    confidence_note: Optional[str] = Field(
        default=None,
        description="Do not fill this in. Set by the server when a cross-field check "
        "on this CLIN fails, to say which figures disagree.",
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
    total_estimated_cost: Optional[float] = Field(
        default=None,
        description="Contract-level total estimated (reimbursable) cost, exclusive of "
        "fee, if the award states one. Null on a fixed-price award (#78).",
    )
    total_fee: Optional[float] = Field(
        default=None,
        description="Contract-level total fee in dollars, if the award states one. On "
        "a CPAF award this is base fee plus the award fee pool only where the document "
        "itself totals them — do not add them up yourself.",
    )
    # The negotiated indirect rates, read off the face (#78 slice 3a). A cost-type
    # award prints them above its pricing exhibit because they are terms of the
    # contract, so the cheap read is here rather than waiting on a separate rate
    # agreement upload. Decimal fractions, not percents: 0.32, never 32.
    #
    # What the face line does NOT carry is each pool's application base or whether
    # the rates are provisional or final — that is the FPRA / billing-rate letter,
    # and the reason `rates.DEFAULT_BASES` and `PROVISIONAL` fill in on confirm.
    indirect_fringe: Optional[float] = Field(
        default=None,
        description="Negotiated fringe rate as a decimal fraction (0.32 for 32%), "
        "from an 'Indirect Rates: Fringe X% | Overhead Y% | G&A Z%' disclosure on the "
        "award face. Null when the award prints no such line.",
    )
    indirect_overhead: Optional[float] = Field(
        default=None,
        description="Negotiated overhead rate as a decimal fraction, same source.",
    )
    indirect_gna: Optional[float] = Field(
        default=None,
        description="Negotiated G&A rate as a decimal fraction, same source.",
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
        "total_obligated, total_estimated_cost, total_fee, effective_date, "
        "contracting_officer).",
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
        description="Total obligated on the WHOLE CONTRACT after this action, in "
        "dollars — and only when the document states a running total ('increased "
        "from $X to $Y', 'total obligated to date'). Most SF-30s state none: leave "
        "it null. 'Obligated this action $X' is `amount_obligated`, never this "
        "field.",
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


class IndirectPool(BaseModel):
    """One indirect cost pool as a rate agreement states it.

    The base is carried per pool because it matters as much as the rate does and is
    the part a summary drops: 45% of labor-plus-fringe and 45% of direct labor are
    different numbers on the same contract.
    """

    pool: str = Field(
        description="Which pool this row is, normalised to one of exactly: 'fringe', "
        "'overhead', 'gna'. A letter may print it as 'Fringe Benefits', 'Overhead' or "
        "'G&A' / 'General and Administrative' — map it to one of those three keys."
    )
    rate: float = Field(
        description="The negotiated rate as a DECIMAL FRACTION: 0.325 for 32.5%, "
        "never 32.5."
    )
    base: Optional[str] = Field(
        default=None,
        description="The application base this rate applies to, normalised to one of "
        "exactly: 'direct_labor' ('Direct Labor'), 'labor_plus_fringe' ('Direct Labor "
        "+ Fringe'), 'total_cost_input' ('Total Cost Input' / 'TCI'). Null when the "
        "letter states no base for this pool.",
    )


class RateAgreement(BaseModel):
    """One indirect rate agreement: a Forward Pricing Rate Agreement (FAR 15.407-3)
    or a provisional billing rate letter (FAR 42.704), and the final determination
    (FAR 42.705) where one has been made.

    A letter routinely states two sets for the same fiscal year — the provisional
    rates that were billed and the final rates the incurred-cost submission settled
    to — so both are extracted. The difference between them is a real receivable or
    payable, which is what #87 trues up.
    """

    contractor: Optional[str] = Field(
        default=None, description="Contractor / awardee name the letter is addressed to"
    )
    piid: Optional[str] = Field(
        default=None,
        description="The contract number the letter names as applicable, if any. A "
        "company-wide rate letter names none — leave it null rather than guessing.",
    )
    fiscal_year: Optional[str] = Field(
        default=None,
        description="The government fiscal year these rates are in force for, as a "
        "four-digit year string, e.g. '2026' for 'FY2026'",
    )
    status: Optional[str] = Field(
        default=None,
        description="Whether `pools` are 'provisional' (billing rates, FAR 42.704) or "
        "'final' (determined after the incurred-cost proposal, FAR 42.705)",
    )
    far_authority: Optional[str] = Field(
        default=None,
        description="The FAR citation the letter states, e.g. 'FAR 42.704'",
    )
    cognisant_agency: Optional[str] = Field(
        default=None,
        description="The cognisant agency that determined the rates, e.g. 'DCMA'",
    )
    determination_date: Optional[str] = Field(
        default=None,
        description="Date the rates were determined, ISO 8601. Null for a provisional "
        "letter, which has no determination.",
    )
    pools: List[IndirectPool] = Field(
        description="Every indirect pool the letter states for the fiscal year, at the "
        "status named in `status`."
    )
    final_pools: Optional[List[IndirectPool]] = Field(
        default=None,
        description="The FINAL determined pools for the same fiscal year, when the "
        "letter prints a second table for them alongside the provisional set. Null "
        "when the letter states only one set of rates.",
    )
