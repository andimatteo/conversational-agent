"""The Closer's deliverable: ranked, evidence-backed comparison.

Ranking = negotiated price vs benchmark, red-flag penalties, binding bonus,
itemisation completeness. Every claim cites a conversation_id + verbatim line."""
from . import db
from .benchmarks import market_range
from .config import vertical
from .packs import load_pack


def _score(best_quote: dict, bench: dict) -> float:
    # Lower price -> higher score, scaled so median == 50.
    price_score = max(0.0, 100.0 * (2 * bench["median"] - best_quote["total"]) / (2 * bench["median"]))
    penalty = sum({"high": 25, "medium": 10, "low": 4}[f["severity"]] for f in best_quote.get("red_flags", []))
    bonus = (10 if best_quote.get("binding") else 0) + min(len(best_quote.get("line_items", [])), 5)
    return round(price_score - penalty + bonus, 1)


def build_report(job_id: str) -> dict:
    job = db.get("jobs", job_id)
    pack = load_pack(job.get("vertical") or vertical()["meta"]["vertical"], job.get("area_code", ""))
    bench = market_range(job["spec"], pack)
    companies = db.where("companies", job_id=job_id)
    calls = db.where("calls", job_id=job_id)
    calls_by_id = {c["id"]: c for c in calls}

    rows = []
    for co in companies:
        quotes = sorted(db.where("quotes", job_id=job_id, company_id=co["id"]),
                        key=lambda q: q.get("created_at", ""))
        # latest initial: a caller may refine mid-call (lowball anchor first,
        # then the real all-in once hidden fees are surfaced)
        initial = next((q for q in reversed(quotes) if q["phase"] == "initial"), None)
        negotiated = next((q for q in reversed(quotes) if q["phase"] == "negotiated"), None)
        trusted_initial = next((q for q in reversed(quotes)
                                if q["phase"] == "initial" and q.get("evidence_verified")
                                and q.get("grounding_verified")), None)
        trusted_negotiated = next((q for q in reversed(quotes)
                                   if q["phase"] == "negotiated" and q.get("evidence_verified")
                                   and q.get("grounding_verified")), None)
        best = trusted_negotiated or trusted_initial or negotiated or initial
        trusted = best in (trusted_negotiated, trusted_initial)
        co_calls = [c for c in calls if c["company_id"] == co["id"]]
        if not best:
            outcome = (co_calls[-1].get("outcome", "failed") if co_calls else "not_called")
            rows.append({"company": co, "outcome": outcome, "score": -1001, "calls": co_calls})
            continue
        rows.append({
            "company": co,
            "outcome": "quote" if trusted else "quote_unverified",
            "initial_total": initial["total"] if initial else None,
            "negotiated_total": negotiated["total"] if negotiated else None,
            "final_total": best["total"],
            "saved_in_negotiation": round(trusted_initial["total"] - trusted_negotiated["total"], 2)
                if trusted_initial and trusted_negotiated else 0,
            "trusted": trusted,
            "binding": best.get("binding", False),
            "line_items": best.get("line_items", []),
            "red_flags": best.get("red_flags", []),
            "evidence": [
                {"quote_id": q["id"], "phase": q["phase"],
                 "verbatim": q.get("verbatim_evidence", ""),
                 "conversation_id": q.get("conversation_id", ""),
                 "call_id": q.get("call_id", ""),
                 "verified_in_transcript": q.get("evidence_verified", False),
                 "grounding_verified": q.get("grounding_verified", False),
                 "grounding_validation": calls_by_id.get(q.get("call_id", ""), {}).get(
                     "grounding_validation", {}),
                 "kind": q.get("evidence_kind", "unverified"),
                 "audio_url": (f"/api/jobs/{job_id}/calls/{q['call_id']}/audio"
                               if q.get("call_id") and
                               calls_by_id.get(q["call_id"], {}).get("audio_path") else "")}
                for q in quotes if q.get("verbatim_evidence")
            ],
            "score": _score(best, bench) if trusted else -1000,
            "calls": co_calls,
        })

    rows.sort(key=lambda r: r["score"], reverse=True)
    winner = next((r for r in rows if r.get("trusted") and r["score"] >= 0), None)

    return {
        "job": job,
        "benchmark": bench,
        "market_evidence": pack["meta"].get("evidence", []),
        "ranking": rows,
        "recommendation": _recommendation(winner, rows, bench) if winner else
            "No evidence-verified, grounded quote is safe to recommend. See per-call outcomes.",
    }


def _recommendation(w: dict, rows: list, bench: dict) -> str:
    name = w["company"]["name"]
    parts = [f"Book {name} at ${w['final_total']:,.0f}"
             + (" (binding quote)" if w["binding"] else " (get it in writing before booking)") + "."]
    if w.get("saved_in_negotiation"):
        verified = any(e.get("verified_in_transcript") and e.get("grounding_verified")
                       for e in w.get("evidence", [])
                       if e.get("phase") == "negotiated")
        parts.append(f"Negotiation moved their price down ${w['saved_in_negotiation']:,.0f} "
                     f"from the initial ${w['initial_total']:,.0f}"
                     + (" — verified transcript evidence is attached." if verified else
                        " — review the unverified transcript evidence before relying on it.") )
    parts.append(f"Fair-market range for this job is ${bench['fair_low']:,}–${bench['fair_high']:,} "
                 f"(median ${bench['median']:,}).")
    cheaper = [r for r in rows if r.get("final_total") and r["final_total"] < w["final_total"]]
    for r in cheaper:
        flags = ", ".join(f["label"].split(" — ")[0] for f in r.get("red_flags", []))
        parts.append(f"{r['company']['name']} quoted less (${r['final_total']:,.0f}) but was flagged: {flags}.")
    return " ".join(parts)
