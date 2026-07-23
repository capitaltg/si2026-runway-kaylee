from typing import Dict, List, Optional

from pydantic import BaseModel, Field


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
    est_hours: Optional[int] = Field(
        default=None, description="Estimated labor hours, if a labor CLIN"
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
