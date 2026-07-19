"""Offline tests for deterministic transcript-only debug calls.

Run with: ``.venv/bin/python -m tests.debugcalls_test``
"""
from copy import deepcopy

from negotiator.benchmarks import market_range
from negotiator.debugcalls import generate_debug_result
from negotiator.packs import load_pack


SPEC = {
    "area_code": "28202",
    "job_type": "water_heater",
    "problem_description": "40-gallon heater leaking at the tank base",
    "property_type": "house",
    "urgency": "this_week",
    "access": {"floor": 1, "tight_access": False},
    "pipe_material": "copper",
}


def _company(identifier, name, persona):
    return {
        "id": identifier,
        "name": name,
        "phone": "+17045550100",
        "source": "google_places",
        "source_ids": {"google_places": f"place-{identifier}"},
        "persona": persona,
    }


def _dialogue(result):
    return "\n".join(turn["text"] for turn in result["transcript"])


def _assert_quote_integrity(result, job, company):
    assert set(result) == {"transcript", "quote", "outcome", "learned_questions", "validation"}
    assert result["validation"]["valid"], result["validation"]
    assert result["quote"]["job_id"] == job["id"]
    assert result["quote"]["company_id"] == company["id"]
    assert round(sum(li["amount"] for li in result["quote"]["line_items"]), 2) == result["quote"]["total"]
    assert result["quote"]["verbatim_evidence"] in _dialogue(result)
    metadata = result["validation"]["metadata"]
    assert metadata["debug"] is True and metadata["simulated"] is True
    assert metadata["network_used"] is False and metadata["audio_generated"] is False
    assert metadata["elevenlabs_used"] is False
    assert metadata["vendor"]["id"] == company["id"]
    assert metadata["vendor"]["name"] == company["name"]
    assert company["name"] in _dialogue(result)
    # Debug/simulation disclosure belongs in metadata, never in the actual call.
    assert "debug" not in _dialogue(result).casefold()
    assert "simulat" not in _dialogue(result).casefold()


def main():
    pack = load_pack("plumbing", "28202")
    job = {"id": "job_debug", "vertical": "plumbing", "area_code": "28202", "spec": SPEC}
    benchmark = market_range(SPEC, pack)
    base_context = {
        "spec": deepcopy(SPEC),
        "benchmark": benchmark,
        "competing_quotes": [],
        "own_quote_history": [],
        "learned_question_candidates": [{
            "question": "Is an expansion tank already installed?",
            "why_it_matters": "Its presence can change parts and labor.",
        }],
    }

    # Three explicit styles create distinct, credible and repeatable outcomes
    # while preserving the original Google vendor records byte-for-byte.
    totals, transcripts = set(), set()
    for index, style in enumerate(("stonewaller", "lowballer", "upseller"), 1):
        company = _company(f"co_{index}", f"Real Google Vendor {index}", style)
        company_before, context_before = deepcopy(company), deepcopy(base_context)
        first = generate_debug_result(job, company, "quote", base_context, pack)
        second = generate_debug_result(job, company, "quote", base_context, pack)
        assert first == second, "debug generation must be fully deterministic"
        assert company == company_before and base_context == context_before, "inputs were mutated"
        _assert_quote_integrity(first, job, company)
        assert first["validation"]["metadata"]["style"] == style
        assert first["learned_questions"][0]["question"] == "Is an expansion tank already installed?"
        totals.add(first["quote"]["total"])
        transcripts.add(_dialogue(first))
    assert len(totals) == 3 and len(transcripts) == 3
    print("quote mode OK: deterministic, three styles, real vendor unchanged, no network/audio")

    # A lower competing quote and the vendor's own recorded quote ground both
    # the competitive claim and a measurable concession.
    vendor = _company("co_live", "Queen City Plumbing", "upseller")
    grounded_context = {
        "spec": deepcopy(SPEC),
        "benchmark": benchmark,
        "own_quote_history": [{
            "quote_id": "q-own", "company_id": vendor["id"], "total": 3000,
            "binding": True, "deposit": 300,
            "line_items": [{"label": "Prior total", "code": "base", "amount": 3000,
                            "kind": "base", "contingent": False, "notes": ""}],
            "conditions": ["Same confirmed scope"], "created_at": "2026-07-18T10:00:00Z",
        }],
        "competing_quotes": [
            {"quote_id": "q-higher", "company": "Higher Bid Co", "total": 3200, "binding": True},
            {"quote_id": "q-comp", "company": "Grounded Competitor", "total": 2400, "binding": True},
        ],
    }
    negotiated = generate_debug_result(job, vendor, "negotiate", grounded_context, pack)
    _assert_quote_integrity(negotiated, job, vendor)
    assert negotiated["quote"]["phase"] == "negotiated"
    assert negotiated["quote"]["total"] < 3000
    assert "Grounded Competitor" in _dialogue(negotiated) and "$2,400" in _dialogue(negotiated)
    assert "Higher Bid Co" not in _dialogue(negotiated), "unused quotes must not leak into dialogue"
    grounding = negotiated["validation"]["grounding"]
    assert grounding["concession_grounded"] is True and grounding["concession_amount"] > 0
    assert grounding["used_competing_quotes"] == [{"id": "q-comp", "total": 2400.0,
                                                     "company": "Grounded Competitor"}]
    assert grounding["used_own_quotes"][0]["id"] == "q-own"
    print("negotiate mode OK: exact snapshot citation + grounded concession")

    # With only an own quote, the agent may confirm it but must not manufacture
    # a competitor or a price movement.
    no_leverage_context = deepcopy(grounded_context)
    no_leverage_context["competing_quotes"] = []
    held = generate_debug_result(job, vendor, "negotiate", no_leverage_context, pack)
    _assert_quote_integrity(held, job, vendor)
    assert held["quote"]["total"] == 3000
    assert held["validation"]["grounding"]["concession_grounded"] is False
    assert held["validation"]["grounding"]["concession_amount"] == 0
    assert "Grounded Competitor" not in _dialogue(held)
    print("no-leverage mode OK: standing offer held, no fabricated concession")

    # Without an own quote there is no safe negotiation anchor: return a
    # structured callback, never a benchmark-derived or invented offer.
    missing_history = generate_debug_result(
        job, vendor, "negotiate", {"benchmark": benchmark, "competing_quotes": grounded_context["competing_quotes"]}, pack)
    assert missing_history["quote"] is None
    assert missing_history["outcome"]["outcome"] == "callback"
    assert missing_history["validation"]["valid"] is True
    assert missing_history["validation"]["grounding"]["concession_grounded"] is False
    assert "Grounded Competitor" not in _dialogue(missing_history)
    print("missing-history mode OK: structured callback, no invented standing quote")

    try:
        generate_debug_result(job, vendor, "bogus", {}, pack)
        raise AssertionError("invalid kind was accepted")
    except ValueError:
        pass

    print("\nDEBUG CALLS TEST PASSED")


if __name__ == "__main__":
    main()
