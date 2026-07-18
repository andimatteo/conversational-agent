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

    rows = []
    for co in companies:
        quotes = sorted(db.where("quotes", job_id=job_id, company_id=co["id"]),
                        key=lambda q: q.get("created_at", ""))
        initial = next((q for q in quotes if q["phase"] == "initial"), None)
        negotiated = next((q for q in reversed(quotes) if q["phase"] == "negotiated"), None)
        best = negotiated or initial
        co_calls = [c for c in calls if c["company_id"] == co["id"]]
        if not best:
            outcome = (co_calls[-1].get("outcome", "decline") if co_calls else "decline")
            rows.append({"company": co, "outcome": outcome, "score": -1, "calls": co_calls})
            continue
        rows.append({
            "company": co,
            "outcome": "quote",
            "initial_total": initial["total"] if initial else None,
            "negotiated_total": negotiated["total"] if negotiated else None,
            "final_total": best["total"],
            "saved_in_negotiation": round(initial["total"] - negotiated["total"], 2)
                if initial and negotiated else 0,
            "binding": best.get("binding", False),
            "line_items": best.get("line_items", []),
            "red_flags": best.get("red_flags", []),
            "evidence": [
                {"phase": q["phase"], "verbatim": q.get("verbatim_evidence", ""),
                 "conversation_id": q.get("conversation_id", "")}
                for q in quotes if q.get("verbatim_evidence")
            ],
            "score": _score(best, bench),
            "calls": co_calls,
        })

    rows.sort(key=lambda r: r["score"], reverse=True)
    winner = next((r for r in rows if r["score"] >= 0), None)

    return {
        "job": job,
        "benchmark": bench,
        "market_evidence": pack["meta"].get("evidence", []),
        "ranking": rows,
        "recommendation": _recommendation(winner, rows, bench) if winner else
            "No usable quotes were gathered. See per-call outcomes.",
    }


def _recommendation(w: dict, rows: list, bench: dict) -> str:
    name = w["company"]["name"]
    parts = [f"Book {name} at ${w['final_total']:,.0f}"
             + (" (binding quote)" if w["binding"] else " (get it in writing before booking)") + "."]
    if w.get("saved_in_negotiation"):
        parts.append(f"Negotiation moved their price down ${w['saved_in_negotiation']:,.0f} "
                     f"from the initial ${w['initial_total']:,.0f} — transcript evidence attached.")
    parts.append(f"Fair-market range for this job is ${bench['fair_low']:,}–${bench['fair_high']:,} "
                 f"(median ${bench['median']:,}).")
    cheaper = [r for r in rows if r.get("final_total") and r["final_total"] < w["final_total"]]
    for r in cheaper:
        flags = ", ".join(f["label"].split(" — ")[0] for f in r.get("red_flags", []))
        parts.append(f"{r['company']['name']} quoted less (${r['final_total']:,.0f}) but was flagged: {flags}.")
    return " ".join(parts)
