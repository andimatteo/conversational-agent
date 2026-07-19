"""Frozen, auditable knowledge supplied to every call in a batch.

All calls in one batch receive the same snapshot.  Quotes written while that
batch is running therefore cannot leak into a peer conversation; they become
visible only when the scheduler opens the next batch.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone

from . import db
from .benchmarks import market_range
from .config import MAX_VENDOR_RECALLS, vertical
from .packs import load_pack


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def spec_hash(spec: dict) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _vendor_key(value: str) -> str:
    """Identity key used to keep a vendor's own uploaded quote out of the
    competing-bid pool. Matching below is limited to exact/contained normalized
    names; it never guesses from price, phone fragments, or similarity scores."""
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def _same_vendor_name(left: str, right: str) -> bool:
    legal_suffixes = {"llc", "inc", "incorporated", "corp", "corporation",
                      "company", "co", "ltd", "limited", "pllc"}

    def identity(value: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", (value or "").casefold())
        while tokens and tokens[-1] in legal_suffixes:
            tokens.pop()
        return "".join(tokens)

    a, b = identity(left), identity(right)
    return bool(a and b and a == b)


def latest_offers(job_id: str, *, completed_only: bool = True) -> list[dict]:
    """Latest quote per company+phase, limited to completed attempts when the
    quote is correlated to a call.  Legacy/document records remain usable."""
    calls = {c["id"]: c for c in db.where("calls", job_id=job_id)}
    selected: dict[tuple[str, str], dict] = {}
    for quote in db.where("quotes", job_id=job_id):
        call_id = quote.get("call_id", "")
        if completed_only and call_id:
            call = calls.get(call_id)
            if not call or not call.get("ended_at"):
                continue
        key = (quote["company_id"], quote.get("phase", "initial"))
        if key not in selected or quote.get("created_at", "") >= selected[key].get("created_at", ""):
            selected[key] = quote
    return list(selected.values())


def _is_expired(valid_until: str, frozen_at: datetime) -> bool:
    """Recognise explicit ISO dates/timestamps and fail open on natural text.

    A value such as "Friday" is retained as an unverified condition for the
    agent to reconfirm; an unambiguously elapsed ISO value is never spendable
    leverage.
    """
    raw = (valid_until or "").strip()
    if not raw:
        return False
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw) < frozen_at.date()
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) < frozen_at
    except ValueError:
        return False


def create_snapshot(job_id: str, version: int, *,
                    allow_debug_leverage: bool = False) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise LookupError(f"job {job_id} not found")
    pack = load_pack(job.get("vertical") or vertical()["meta"]["vertical"],
                     job.get("area_code", ""))
    companies = {c["id"]: c for c in db.where("companies", job_id=job_id)}
    frozen_at = datetime.now(timezone.utc)
    latest = latest_offers(job_id)
    current_by_company: dict[str, dict] = {}
    for q in latest:
        if not q.get("company_id"):
            continue
        current = current_by_company.get(q["company_id"])
        if current is None or (q.get("phase") == "negotiated"
                               and current.get("phase") != "negotiated") \
                or (q.get("phase") == current.get("phase")
                    and q.get("created_at", "") >= current.get("created_at", "")):
            current_by_company[q["company_id"]] = q
    current_ids = {q["id"] for q in current_by_company.values()}

    offers = []
    for q in latest:
        company = companies.get(q["company_id"], {})
        # A phone quote becomes usable leverage only after the post-call
        # finalizer found its vendor-side verbatim evidence in a terminal
        # transcript. Synthetic debug evidence can only circulate inside an
        # explicitly synthetic run, never into live telephony.
        is_debug = q.get("evidence_kind") == "debug_generated"
        expired = _is_expired(q.get("valid_until", ""), frozen_at)
        leverage_verified = bool(q.get("call_id") and q.get("evidence_verified")
                                 and q.get("grounding_verified")
                                 and q.get("itemization_verified") is True
                                 and not expired
                                 and (allow_debug_leverage or not is_debug))
        offers.append({
            "quote_id": q["id"],
            "call_id": q.get("call_id", ""),
            "company_id": q["company_id"],
            "company": company.get("name", "Unknown vendor"),
            "total": q["total"],
            "binding": q.get("binding", False),
            "valid_until": q.get("valid_until", ""),
            "expired": expired,
            "phase": q.get("phase", "initial"),
            "current_offer": q["id"] in current_ids,
            "line_items": q.get("line_items", []),
            "itemization_verified": q.get("itemization_verified") is True,
            "conditions": q.get("conditions", []),
            "red_flags": q.get("red_flags", []),
            "verbatim_evidence": q.get("verbatim_evidence", ""),
            "evidence_verified": q.get("evidence_verified", False),
            "evidence_kind": q.get("evidence_kind", ""),
            "synthetic": is_debug,
            "leverage_verified": leverage_verified,
        })

    # Written quotes supplied by the customer are legitimate leverage only
    # when they came from an uploaded document and the user subsequently
    # confirmed the merged spec. A hand-edited JSON object is not evidence.
    doc_quotes = list(job.get("spec", {}).get("existing_quotes") or [])
    if job.get("spec", {}).get("existing_quote"):
        doc_quotes.append(job["spec"]["existing_quote"])
    for index, q in enumerate(doc_quotes):
        if not q.get("total"):
            continue
        line_items = q.get("line_items", []) if isinstance(q.get("line_items", []), list) else []
        itemized_total = sum(float(item.get("amount", 0)) for item in line_items
                             if isinstance(item, dict)
                             and isinstance(item.get("amount"), (int, float)))
        itemization_verified = bool(line_items and abs(itemized_total - float(q["total"])) <= 1.0)
        expired = _is_expired(q.get("valid_until", ""), frozen_at)
        offers.append({
            "quote_id": q.get("id") or f"document:{index}",
            "call_id": "",
            "company_id": "",
            "company": q.get("company", "prior written quote"),
            "total": q["total"],
            "binding": bool(q.get("binding", False)),
            "valid_until": q.get("valid_until", ""),
            "expired": expired,
            "phase": "document",
            "current_offer": True,
            "line_items": q.get("line_items", []),
            "itemization_verified": itemization_verified,
            "conditions": q.get("conditions", []),
            "red_flags": [],
            "verbatim_evidence": q.get("verbatim_evidence", "document supplied by customer"),
            "evidence_verified": bool(q.get("_document_id") and job.get("confirmed")),
            "evidence_kind": "customer_document",
            "document_id": q.get("_document_id", ""),
            "leverage_verified": bool(q.get("_document_id") and job.get("confirmed")
                                      and itemization_verified and not expired),
        })
    return {
        "version": version,
        "created_at": frozen_at.isoformat(),
        "job_id": job_id,
        "spec": job.get("spec", {}),
        "spec_hash": spec_hash(job.get("spec", {})),
        "benchmark": market_range(job.get("spec", {}), pack),
        "companies": {company_id: {"name": company.get("name", ""),
                                     "vendor_key": _vendor_key(company.get("name", ""))}
                      for company_id, company in companies.items()},
        "offers": sorted(offers, key=lambda q: (q["company"], q["phase"], q["total"])),
    }


def context_for(snapshot: dict, company_id: str) -> dict:
    selected_name = snapshot.get("companies", {}).get(company_id, {}).get("name", "")

    def is_own(q: dict) -> bool:
        if q.get("company_id") == company_id:
            return True
        # An uploaded quote naming this same vendor is its own history, never
        # a competing bid. Empty/ambiguous names are kept out of both pools.
        return bool(selected_name and _same_vendor_name(q.get("company", ""), selected_name))

    verified = [q for q in snapshot.get("offers", []) if q.get("leverage_verified")]
    own = [q for q in verified if is_own(q)]
    competing = [q for q in verified if not is_own(q)
                 and q.get("current_offer", True)
                 and _vendor_key(q.get("company", ""))]
    allowed = [{"quote_id": q["quote_id"], "company": q["company"], "total": q["total"],
                "binding": q["binding"], "valid_until": q.get("valid_until", ""),
                "phase": q.get("phase", "initial"),
                "evidence_kind": q.get("evidence_kind", "")}
               for q in competing]
    return {
        "knowledge_version": snapshot.get("version", 0),
        "snapshot_created_at": snapshot.get("created_at", ""),
        "spec": snapshot.get("spec", {}),
        "spec_hash": snapshot.get("spec_hash", ""),
        "benchmark": snapshot.get("benchmark", {}),
        "own_quote_history": own,
        "competing_quotes": competing,
        "allowed_competitive_claims": allowed,
        "excluded_unverified_offer_count": len(snapshot.get("offers", [])) - len(verified),
        "rules": (
            "Use the spec verbatim. Competitive claims are allowed ONLY when their exact "
            "quote_id, company and total occur in allowed_competitive_claims. If a fact is "
            "absent, say you do not have verified information; never infer or round it."
        ),
    }


def follow_up_plan(job_id: str, knowledge_version: int) -> list[dict]:
    """Explainable recall suggestions.  This plans; it never dials by itself."""
    frozen_at = datetime.now(timezone.utc)
    offers = [q for q in latest_offers(job_id)
              if q.get("company_id") and q.get("evidence_verified")
              and q.get("grounding_verified")
              and q.get("itemization_verified") is True
              and not _is_expired(q.get("valid_until", ""), frozen_at)]
    by_company: dict[str, list[dict]] = {}
    for q in offers:
        by_company.setdefault(q["company_id"], []).append(q)
    current = []
    for company_id, rows in by_company.items():
        negotiated = next((q for q in rows if q.get("phase") == "negotiated"), None)
        initial = next((q for q in rows if q.get("phase") == "initial"), None)
        best = negotiated or initial
        if best:
            current.append(best)
    safe = [q for q in current if not any(f.get("severity") == "high" for f in q.get("red_flags", []))]
    if not safe:
        safe = current
    if not safe:
        return []
    best_market = min(safe, key=lambda q: q["total"])
    companies = {c["id"]: c for c in db.where("companies", job_id=job_id)}
    plan = []
    from .recall_limits import for_company as recall_reservations_for_company
    for q in current:
        # Reservations are consumed before a callback starts, so planned and
        # concurrent attempts count toward the hard cap too.
        negotiation_attempts = len(recall_reservations_for_company(job_id, q["company_id"]))
        max_attempts = MAX_VENDOR_RECALLS
        reasons = []
        source_ids = []
        if not q.get("binding"):
            reasons.append("Convert the estimate into a binding written total.")
        if q.get("red_flags"):
            reasons.append("Resolve flagged fees or terms before recommending the offer.")
        if q["id"] != best_market["id"] and q["total"] <= best_market["total"] * 1.35:
            reasons.append(
                f"A verified ${best_market['total']:,.0f} competing offer now creates price-match leverage."
            )
            source_ids.append(best_market["id"])
        if q["id"] == best_market["id"] and not q.get("binding"):
            reasons.append("Protect the current best price by clarifying validity, deposit, and scope.")
        if reasons:
            co = companies.get(q["company_id"], {})
            exhausted = negotiation_attempts >= max_attempts
            plan.append({
                "company_id": q["company_id"],
                "company_name": co.get("name", "Unknown vendor"),
                "reasons": list(dict.fromkeys(reasons)),
                "source_quote_ids": source_ids,
                "knowledge_version": knowledge_version,
                "quote_knowledge_version": q.get("knowledge_version", 0),
                "attempts": negotiation_attempts,
                "max_attempts": max_attempts,
                "status": "exhausted" if exhausted else "recommended",
            })
    return plan
