"""Reusable post-call learning helpers.

The call runners can use this module without importing the FastAPI application:

* :func:`questions_from_call` derives conservative, customer-facing intake
  questions from facts that actually appeared in a quote or transcript.
* :func:`persist_questions` stores those questions in the domain/area pool and
  records which calls and companies surfaced them.

Extraction is deliberately deterministic.  A learned question is useful only
if future intake agents can ask it consistently, and no question should assert
a fact that the vendor did not provide.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterable, Mapping

from . import db


_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MONEY_RE = re.compile(r"(?:\$|USD\s*)?\d[\d,.]*(?:\s*(?:dollars?|usd))?", re.IGNORECASE)


def _norm(value: str) -> str:
    """Case/punctuation-insensitive identity used for durable deduplication."""
    return _SPACE_RE.sub(" ", _PUNCT_RE.sub(" ", value.casefold())).strip()


def _unique_append(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _question_dict(value) -> dict | None:
    """Accept strings, mappings, or Pydantic-like objects at integration edges."""
    if isinstance(value, str):
        question, why = value, ""
    else:
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        if not isinstance(value, Mapping):
            return None
        question = str(value.get("question") or "")
        why = str(value.get("why_it_matters") or "")
    question = _SPACE_RE.sub(" ", question).strip()
    why = _SPACE_RE.sub(" ", why).strip()
    if not _norm(question):
        return None
    if "?" not in question:
        question = question.rstrip(" .!;:") + "?"
    return {"question": question, "why_it_matters": why}


def persist_questions(
    job: dict,
    questions: Iterable,
    source_call_id: str = "",
    company_id: str = "",
) -> dict:
    """Upsert learned questions for the job's ``(vertical, area_code)`` pool.

    Duplicate spellings in one call count as one observation.  Replaying the
    same ``source_call_id`` is idempotent; a genuinely different call increments
    ``times_seen`` and extends provenance.  ``job.discovered_questions`` is
    changed only for questions that are new to the global domain/area pool.

    The returned shape intentionally resembles the existing agent-tool response
    while adding an ``updated`` collection for callers that want richer logging.
    """
    if not isinstance(job, dict):
        raise TypeError("job must be a mutable dict")
    job_id = str(job.get("id") or "").strip()
    vertical = str(job.get("vertical") or "").strip()
    area_code = str(job.get("area_code") or "").strip()
    if not job_id or not vertical:
        raise ValueError("job needs non-empty id and vertical")

    # Collapse duplicates within this completion before touching storage.  Keep
    # the first phrasing, but retain a later explanation when the first is bare.
    incoming: dict[str, dict] = {}
    raw_questions = questions or []
    if isinstance(raw_questions, (str, Mapping)) or hasattr(raw_questions, "model_dump"):
        raw_questions = [raw_questions]
    for raw in raw_questions:
        item = _question_dict(raw)
        if not item:
            continue
        key = _norm(item["question"])
        if key in incoming:
            if not incoming[key]["why_it_matters"] and item["why_it_matters"]:
                incoming[key]["why_it_matters"] = item["why_it_matters"]
        else:
            incoming[key] = item

    if not incoming:
        return {"logged": True, "added": [], "updated": [], "already_known": []}

    now = datetime.now(timezone.utc).isoformat()
    added: list[dict] = []
    updated: list[dict] = []
    already_known: list[str] = []

    # Calls may finish concurrently.  A write lock around read-modify-write keeps
    # counts and provenance from losing observations from sibling batch threads.
    with db.conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        stored_rows = conn.execute(
            "SELECT id, data FROM learned_questions WHERE vertical=? AND area_code=?",
            (vertical, area_code),
        ).fetchall()
        known = {}
        for row_id, payload in stored_rows:
            row = json.loads(payload)
            key = _norm(str(row.get("question") or ""))
            if key:
                known[key] = (row_id, row)

        for key, item in incoming.items():
            public = {"question": item["question"], "why_it_matters": item["why_it_matters"]}
            if key not in known:
                row_id = db.new_id("lq")
                row = {
                    "id": row_id,
                    "vertical": vertical,
                    "area_code": area_code,
                    **public,
                    "source_job_id": job_id,
                    "source_call_ids": [source_call_id] if source_call_id else [],
                    "company_ids": [company_id] if company_id else [],
                    "times_seen": 1,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
                conn.execute(
                    "INSERT INTO learned_questions (id, vertical, area_code, data) VALUES (?, ?, ?, ?)",
                    (row_id, vertical, area_code, json.dumps(row)),
                )
                known[key] = (row_id, row)
                added.append(public)
                continue

            row_id, row = known[key]
            call_ids = list(row.get("source_call_ids") or [])
            company_ids = list(row.get("company_ids") or [])
            replay = bool(source_call_id and source_call_id in call_ids)
            _unique_append(call_ids, source_call_id)
            _unique_append(company_ids, company_id)
            row["source_call_ids"] = call_ids
            row["company_ids"] = company_ids
            if not replay:
                row["times_seen"] = int(row.get("times_seen", 1)) + 1
            if not row.get("why_it_matters") and item["why_it_matters"]:
                row["why_it_matters"] = item["why_it_matters"]
            row["updated_at"] = now
            conn.execute(
                "UPDATE learned_questions SET data=? WHERE id=?",
                (json.dumps(row), row_id),
            )
            known[key] = (row_id, row)
            already_known.append(row["question"])
            updated.append({
                "question": row["question"],
                "why_it_matters": row.get("why_it_matters", ""),
                "times_seen": row["times_seen"],
                "source_call_ids": call_ids,
                "company_ids": company_ids,
            })

        if added:
            # Reload under the same lock: several calls in a batch may have
            # started with separate, stale copies of the job record.
            stored_job_row = conn.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            stored_job = json.loads(stored_job_row[0]) if stored_job_row else dict(job)
            discovered = list(stored_job.get("discovered_questions") or [])
            discovered_keys = {_norm(str(q.get("question") or "")) for q in discovered
                               if isinstance(q, Mapping)}
            for item in added:
                if _norm(item["question"]) not in discovered_keys:
                    discovered.append(item)
                    discovered_keys.add(_norm(item["question"]))
            stored_job["discovered_questions"] = discovered
            job["discovered_questions"] = discovered
            conn.execute(
                "INSERT INTO jobs (id, data) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (job_id, json.dumps(stored_job)),
            )

    return {"logged": True, "added": added, "updated": updated,
            "already_known": already_known}


# Each question is customer-facing and stable across vendors.  Aliases are only
# used to identify a signal in fee codes, conditions, or vendor transcript text.
_SIGNALS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("stairs", ("stairs", "staircase", "flight"),
     "Are there stairs at any service location, and how many flights are involved?",
     "Stairs can change labor time, crew requirements, and access fees."),
    ("elevator", ("elevator", "lift", "service elevator"),
     "Is an elevator available, and does it need to be reserved or protected?",
     "Elevator access and reservation rules can add labor time or building fees."),
    ("parking", ("parking", "long carry", "carry distance", "truck access"),
     "How close can the vendor park to the work area, and are there loading restrictions?",
     "Parking distance and loading restrictions can trigger travel or long-carry charges."),
    ("permit", ("permit", "inspection", "hoa", "building approval", "certificate"),
     "Will this job require permits, inspections, certificates, or building/HOA approval?",
     "Approval and permit requirements often add fees and scheduling time."),
    ("timing", ("after hours", "after-hours", "emergency", "urgent", "weekend",
                "time window", "same day"),
     "Does the work need to happen urgently, after hours, on a weekend, or within a fixed time window?",
     "Timing constraints commonly trigger urgency or after-hours surcharges."),
    ("materials", ("parts", "materials", "supplies", "customer supplied", "equipment"),
     "Who should supply the required parts or materials, and are specific products required?",
     "Responsibility and product requirements materially change parts and markup costs."),
    ("disposal", ("disposal", "haul away", "haul-away", "removal", "dump fee"),
     "Will removal, haul-away, or disposal of old equipment or materials be required?",
     "Disposal is frequently priced as a separate or contingent fee."),
    ("access", ("access", "tight access", "crawlspace", "crawl space", "attic", "site access",
                "restricted access", "seventh floor"),
     "Are there tight, elevated, restricted, or otherwise difficult access conditions at the site?",
     "Site access can change labor hours, equipment, and crew size."),
    ("travel", ("fuel", "travel", "mileage", "trip fee", "callout", "call-out"),
     "What is the exact service location, and could distance, mileage, or a trip charge apply?",
     "Travel distance and callout policies can materially change the all-in price."),
    ("deposit", ("deposit", "upfront", "up front", "payment schedule"),
     "What deposit and payment schedule are you willing to authorize?",
     "Capturing payment limits early prevents an agent from accepting unauthorized terms."),
    ("coverage", ("insurance", "valuation", "warranty", "service plan", "guarantee"),
     "Do you require specific insurance, valuation coverage, warranty, or service-plan terms?",
     "Coverage requirements and optional plans can materially change comparable totals."),
    ("minimum", ("minimum charge", "minimum hours", "minimum booking"),
     "Are there scope or duration details that could trigger a vendor minimum charge?",
     "Minimum charges can dominate the price of smaller jobs."),
)

_SIGNAL_BY_NAME = {name: (question, why) for name, _, question, why in _SIGNALS}
_PRICE_CONTEXT = (
    "fee", "charge", "cost", "price", "quote", "extra", "surcharge", "included",
    "not include", "depends", "if ", "unless", "required", "minimum", "deposit",
)
_FALLBACK = {
    "question": (
        "Are there any access constraints, timing requirements, permits, required materials, "
        "or site conditions that could change the final price?"
    ),
    "why_it_matters": (
        "Surfacing price-changing conditions before the next call keeps vendor quotes complete "
        "and comparable."
    ),
}


def _signals_in(text: str) -> list[str]:
    lower = _SPACE_RE.sub(" ", text.casefold())
    return [name for name, aliases, _, _ in _SIGNALS if any(alias in lower for alias in aliases)]


def _clean_fee_label(value: str) -> str:
    value = _MONEY_RE.sub("", value)
    value = _SPACE_RE.sub(" ", value).strip(" -–—:;,.()")
    return value[:90]


def _vendor_transcript_text(call: Mapping) -> str:
    turns = call.get("transcript") or []
    if isinstance(turns, str):
        return turns
    vendor_roles = {"user", "vendor", "counterparty", "callee", "rep"}
    vendor_parts = []
    all_parts = []
    for turn in turns:
        if isinstance(turn, str):
            all_parts.append(turn)
            continue
        if not isinstance(turn, Mapping):
            continue
        text = str(turn.get("text") or turn.get("message") or "").strip()
        if not text:
            continue
        all_parts.append(text)
        if str(turn.get("role") or "").casefold() in vendor_roles:
            vendor_parts.append(text)
    # Prefer the other side of the call so our own fee-checklist questions do
    # not manufacture learnings.  Role-less transcripts still remain usable.
    return " ".join(vendor_parts or all_parts)


def questions_from_call(job: dict, call: dict, quote: dict | None = None,
                        pack: dict | None = None) -> list[dict]:
    """Derive stable price-relevant intake questions from one completed call.

    Sources are, in order: contingent line items, explicit quote conditions, and
    vendor transcript statements.  The function never converts amounts or
    vendor claims into user facts; it only asks the user to clarify a possible
    price driver.  At least one domain-agnostic fallback is always returned.
    """
    if pack is None:
        try:
            from .packs import load_pack
            pack = load_pack(str(job.get("vertical") or ""), str(job.get("area_code") or ""))
        except (FileNotFoundError, ValueError):
            pack = {}
    quote = quote or {}
    fee_taxonomy = (pack or {}).get("fee_taxonomy", {})
    out: list[dict] = []
    seen: set[str] = set()

    def add(question: str, why: str) -> None:
        item = _question_dict({"question": question, "why_it_matters": why})
        if not item:
            return
        key = _norm(item["question"])
        if key not in seen:
            seen.add(key)
            out.append(item)

    def add_signal(name: str) -> None:
        question, why = _SIGNAL_BY_NAME[name]
        add(question, why)

    for line in quote.get("line_items") or []:
        if not isinstance(line, Mapping) or not line.get("contingent"):
            continue
        code = str(line.get("code") or "").casefold().strip()
        label = str(fee_taxonomy.get(code) or line.get("label") or code)
        signals = _signals_in(f"{code} {label} {line.get('notes', '')}")
        if signals:
            for signal in signals:
                add_signal(signal)
        else:
            label = _clean_fee_label(label)
            if label and code not in {"base", "other"}:
                add(f"Could a {label.casefold()} charge apply to this job, and what would trigger it?",
                    "A vendor identified this as a contingent charge; its trigger should be known before quoting.")

    conditions = quote.get("conditions") or []
    if isinstance(conditions, str):
        conditions = [conditions]
    conditions_text = " ".join(str(c) for c in conditions if c)
    for signal in _signals_in(conditions_text):
        add_signal(signal)
    if conditions_text and not _signals_in(conditions_text):
        add("Are there any job-specific conditions or constraints vendors should price explicitly?",
            "A vendor attached conditions to its quote; capturing the underlying facts improves the next comparison.")

    transcript = _vendor_transcript_text(call or {})
    if transcript and any(marker in transcript.casefold() for marker in _PRICE_CONTEXT):
        for signal in _signals_in(transcript):
            add_signal(signal)

    if not out:
        add(_FALLBACK["question"], _FALLBACK["why_it_matters"])
    return out
