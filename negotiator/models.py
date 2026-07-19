"""Pydantic models shared by the API, the agent-tool webhooks and the report."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

FeeKind = Literal["base", "fee", "addon", "discount"]
Outcome = Literal["quote", "callback", "decline", "hangup"]
Phase = Literal["initial", "negotiated"]
NegotiationBasis = Literal["none", "competing_quote", "fee_or_terms", "standing_offer"]


class LineItem(BaseModel):
    label: str                      # what the rep called it, verbatim-ish
    code: str = "other"             # canonical code from the vertical fee taxonomy
    amount: float = Field(allow_inf_nan=False)
    kind: FeeKind = "fee"
    contingent: bool = False        # "only if there are stairs" etc.
    notes: str = ""

    @model_validator(mode="after")
    def amount_matches_kind(self):
        if not self.label.strip():
            raise ValueError("line item label cannot be empty")
        if self.kind == "discount" and self.amount > 0:
            raise ValueError("discount line items must be zero or negative")
        if self.kind != "discount" and self.amount < 0:
            raise ValueError("only discount line items may be negative")
        return self


class QuoteIn(BaseModel):
    """What the caller/closer agent logs mid-call via webhook tool."""
    job_id: str
    company_id: str
    call_id: str = ""              # exact attempt; avoids races during recalls/batches
    line_items: list[LineItem] = Field(min_length=1)
    total: float = Field(gt=0, allow_inf_nan=False)
    binding: bool = False
    deposit: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    valid_until: str = ""
    conditions: list[str] = []
    verbatim_evidence: str = ""     # the exact sentence the rep said, for the report
    phase: Phase = "initial"
    leverage_quote_ids: list[str] = []  # exact DB facts used in a negotiation
    negotiation_basis: NegotiationBasis = "none"

    @model_validator(mode="after")
    def validate_negotiation_claims(self):
        if self.deposit > self.total:
            raise ValueError("deposit cannot exceed the quote total")
        if self.phase == "initial":
            if self.leverage_quote_ids or self.negotiation_basis != "none":
                raise ValueError("initial quotes cannot claim negotiation leverage")
            return self
        if self.negotiation_basis == "none":
            raise ValueError("negotiated quotes must declare their negotiation_basis")
        if self.negotiation_basis == "competing_quote" and not self.leverage_quote_ids:
            raise ValueError("competing_quote basis requires leverage_quote_ids")
        if self.negotiation_basis != "competing_quote" and self.leverage_quote_ids:
            raise ValueError("leverage_quote_ids require negotiation_basis=competing_quote")
        return self


class OutcomeIn(BaseModel):
    job_id: str
    company_id: str
    call_id: str = ""
    outcome: Outcome
    callback_time: str = ""
    decline_reason: str = ""
    summary: str = ""


class Company(BaseModel):
    id: str
    name: str
    phone: str = ""
    # Kept open because discovery adapters are configuration, not a closed
    # enum.  Typical values: google_places, simulated, synthetic, manual.
    source: str = "simulated"
    persona: str = ""               # persona id when simulated
    agent_id: str = ""              # ElevenLabs agent id when simulated
    rating: Optional[float] = None
    review_count: Optional[int] = None
    address: str = ""
    discovery_sources: list[str] = []
    external_ids: dict[str, str] = {}
    # Optional presentation metadata for the explicit allow-listed role-play
    # demo. The real Google identity/phone above remain untouched in storage.
    demo_roleplay: bool = False
    demo_alias: str = ""


class RegisterIn(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class Job(BaseModel):
    id: str
    vertical: str
    user_id: str = ""               # owner — every /api route is scoped to it
    area_code: str = ""             # picks the (vertical, area) pack + learned-question pool
    spec: dict = {}
    spec_source: str = ""           # "interview" | "document" | "interview+document"
    confirmed: bool = False
    discovered_questions: list[dict] = []  # surfaced to the user: what this job's intake taught us
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LearnedQuestion(BaseModel):
    """A price-relevant intake question discovered mid-call. Stored per
    (vertical, area_code); merged into every future intake form for that area."""
    question: str
    why_it_matters: str = ""


class LearnedIn(BaseModel):
    job_id: str
    company_id: str = ""
    call_id: str = ""
    questions: list[LearnedQuestion]


class CallRecord(BaseModel):
    id: str
    job_id: str
    company_id: str
    kind: Literal["quote", "negotiate"]
    conversation_id: str = ""       # ElevenLabs conversation id (recording + transcript live there)
    transcript: list[dict] = []     # [{role, text}] fetched post-call
    audio_path: str = ""
    started_at: str = ""
    ended_at: str = ""
