"""Pydantic models shared by the API, the agent-tool webhooks and the report."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

FeeKind = Literal["base", "fee", "addon", "discount"]
Outcome = Literal["quote", "callback", "decline", "hangup"]
Phase = Literal["initial", "negotiated"]


class LineItem(BaseModel):
    label: str                      # what the rep called it, verbatim-ish
    code: str = "other"             # canonical code from the vertical fee taxonomy
    amount: float
    kind: FeeKind = "fee"
    contingent: bool = False        # "only if there are stairs" etc.
    notes: str = ""


class QuoteIn(BaseModel):
    """What the caller/closer agent logs mid-call via webhook tool."""
    job_id: str
    company_id: str
    line_items: list[LineItem]
    total: float
    binding: bool = False
    deposit: float = 0.0
    valid_until: str = ""
    conditions: list[str] = []
    verbatim_evidence: str = ""     # the exact sentence the rep said, for the report
    phase: Phase = "initial"


class OutcomeIn(BaseModel):
    job_id: str
    company_id: str
    outcome: Outcome
    callback_time: str = ""
    decline_reason: str = ""
    summary: str = ""


class Company(BaseModel):
    id: str
    name: str
    phone: str = ""
    source: Literal["simulated", "tavily", "manual", "human"] = "simulated"
    persona: str = ""               # persona id when simulated
    agent_id: str = ""              # ElevenLabs agent id when simulated
    rating: Optional[float] = None


class Job(BaseModel):
    id: str
    vertical: str
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
