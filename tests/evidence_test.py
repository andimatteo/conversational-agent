"""Unit tests for isolated post-call anti-hallucination validation.

Run with: ``.venv/bin/python -m tests.evidence_test``
"""
from copy import deepcopy
import json

from negotiator.evidence import validate_call_grounding, verify_quote_counterparty_evidence


def _call(*, evidence_verified=True, source_complete=True):
    competitor = {
        "quote_id": "q_comp",
        "call_id": "call_comp" if source_complete else "",
        "company_id": "co_comp",
        "company": "Alpha Moving",
        "total": 1850.0,
        "binding": True,
        "phase": "initial",
        "evidence_verified": evidence_verified,
    }
    return {
        "id": "call_new",
        "job_id": "job_1",
        "company_id": "co_vendor",
        "status": "completed",
        "ended_at": "2026-07-18T20:10:00Z",
        "knowledge_snapshot": {
            "knowledge_version": 2,
            "snapshot_created_at": "2026-07-18T20:00:00Z",
            "benchmark": {"fair_low": 1500, "median": 2000, "fair_high": 2600},
            "own_quote_history": [{
                "quote_id": "q_own",
                "call_id": "call_own",
                "company_id": "co_vendor",
                "company": "Current Vendor",
                "total": 2200.0,
                "deposit": 200.0,
                "line_items": [{"label": "Prior total", "amount": 2200.0}],
            }],
            "competing_quotes": [competitor],
            "allowed_competitive_claims": [{
                "quote_id": "q_comp", "company": "Alpha Moving",
                "total": 1850.0, "binding": True,
            }],
        },
        "transcript": [
            {"role": "agent", "text": (
                "I have a recorded competing quote from Alpha Moving for $1,850. "
                "Your prior total was $2,200; can you improve it?"
            )},
            {"role": "vendor", "text": (
                "From the prior $2,200, I can remove $250 and make the revised total $1,950.50. "
                "The deposit stays $200."
            )},
        ],
    }


def _logged_quote(**overrides):
    quote = {
        "id": "q_new",
        "call_id": "call_new",
        "job_id": "job_1",
        "company_id": "co_vendor",
        "phase": "negotiated",
        "negotiation_basis": "competing_quote",
        "leverage_quote_ids": ["q_comp"],
        "total": 1950.0,
        "deposit": 200.0,
        "line_items": [
            {"label": "Prior total", "amount": 2200.0},
            {"label": "Competitive discount", "amount": -250.0},
        ],
        "verbatim_evidence": (
            "From the prior $2,200, I can remove $250 and make the revised total $1,950.50."
        ),
    }
    quote.update(overrides)
    return quote


def _codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_grounded_call_is_valid_and_auditable():
    call = _call()
    quote = _logged_quote()
    original_call, original_quote = deepcopy(call), deepcopy(quote)

    result = validate_call_grounding(call, [quote])

    assert result["valid"] is True, result
    assert result["issues"] == []
    assert result["allowed"]["claim_ids"] == ["q_comp"]
    assert result["allowed"]["claims"][0]["evidence_verified"] is True
    assert result["allowed"]["claims"][0]["source"] == "completed_call_in_frozen_snapshot"
    assert result["used"]["leverage_quote_ids"] == ["q_comp"]
    assert result["used"]["observed_quote_ids"] == ["q_comp"]
    assert all(row["authorized"] for row in result["used"]["money"]), result
    assert result["used"]["competitor_mentions"][0]["authorized"] is True
    json.dumps(result)  # public contract must be persistence/API safe
    assert call == original_call and quote == original_quote, "validator mutated its inputs"


def test_unverified_or_incomplete_claim_cannot_be_leverage():
    for call in (_call(evidence_verified=False), _call(source_complete=False)):
        result = validate_call_grounding(call, [_logged_quote()])
        codes = _codes(result)
        assert result["valid"] is False
        assert "ineligible_competitive_claim" in codes
        assert "unauthorized_leverage_quote" in codes
        assert "unauthorized_competitor_reference" in codes
        assert result["allowed"]["claim_ids"] == []
        assert result["used"]["leverage_quote_ids"] == ["q_comp"]


def test_unknown_money_and_fake_competitor_are_reported():
    call = _call()
    call["transcript"] = [{
        "role": "agent",
        "text": "I have a quote from Imaginary Movers for $1,777; can you beat it?",
    }]
    result = validate_call_grounding(call, [_logged_quote(leverage_quote_ids=[])])
    codes = _codes(result)
    assert result["valid"] is False
    assert "unsupported_money_amount" in codes
    assert "unsupported_competitor_claim" in codes
    money = result["used"]["money"]
    assert money == [{
        "turn_index": 0,
        "role": "agent",
        "raw": "$1,777",
        "amount": 1777.0,
        "authorized": False,
        "authorized_by": [],
    }]


def test_known_competitor_must_be_declared_and_snapshot_must_be_frozen():
    call = _call()
    call["knowledge_snapshot"].pop("snapshot_created_at")
    result = validate_call_grounding(call, [_logged_quote(leverage_quote_ids=[],
                                                           negotiation_basis="standing_offer")])
    codes = _codes(result)
    assert "missing_frozen_snapshot" in codes
    assert "ineligible_competitive_claim" in codes
    assert "undeclared_competitor_reference" not in codes  # ineligible is the stronger finding
    assert "unauthorized_competitor_reference" in codes


def test_foreign_quote_cannot_authorize_transcript_amounts():
    call = _call()
    call["transcript"] = [{"role": "vendor", "message": "The new total is $9,999."}]
    foreign = _logged_quote(call_id="call_someone_else", total=9999.0,
                            line_items=[{"label": "Fake", "amount": 9999.0}])
    result = validate_call_grounding(call, [foreign])
    codes = _codes(result)
    assert "uncorrelated_logged_quote" in codes
    assert "unsupported_money_amount" in codes
    assert result["used"]["money"][0]["authorized"] is False


def test_raw_spec_quote_is_not_automatic_leverage():
    call = _call()
    call["knowledge_snapshot"]["spec"] = {
        "budget": 2500,
        "existing_quotes": [{"company": "Unverified Co", "total": 1777}],
    }
    call["transcript"] = [{
        "role": "agent",
        "text": "I have a quote from Unverified Co for $1,777; can you beat it?",
    }]
    result = validate_call_grounding(call, [_logged_quote(
        negotiation_basis="standing_offer", leverage_quote_ids=[])])
    assert "unsupported_money_amount" in _codes(result)
    assert "unsupported_competitor_claim" in _codes(result)


def test_agent_cannot_self_authorize_an_invented_quote():
    call = _call()
    call["transcript"] = [
        {"role": "agent", "text": "Your itemised all-in total is $2,000."},
        {"role": "vendor", "text": "I did not provide or confirm a price."},
    ]
    invented = _logged_quote(
        total=2000.0,
        deposit=0.0,
        line_items=[{"label": "Invented base", "amount": 2000.0}],
        negotiation_basis="standing_offer",
        leverage_quote_ids=[],
        verbatim_evidence="Your itemised all-in total is $2,000.",
    )
    evidence = verify_quote_counterparty_evidence(call, invented)
    result = validate_call_grounding(call, [invented])
    assert evidence["valid"] is False
    assert "missing_counterparty_verbatim_evidence" in {
        issue["code"] for issue in evidence["issues"]
    }
    assert "unsupported_logged_quote_field" in _codes(result)
    assert result["valid"] is False


def test_synthetic_leverage_requires_same_turn_demo_disclosure():
    call = _call()
    call["knowledge_snapshot"]["demo_roleplay"] = True
    call["knowledge_snapshot"]["competing_quotes"][0]["evidence_kind"] = "debug_generated"
    call["knowledge_snapshot"]["allowed_competitive_claims"][0][
        "evidence_kind"] = "debug_generated"
    undisclosed = validate_call_grounding(call, [_logged_quote()])
    assert "undisclosed_simulated_claim" in _codes(undisclosed)

    call["transcript"][0]["text"] = (
        "In this recorded role-play, I have a simulated demo-market offer labelled "
        "Alpha Moving at $1,850. Your prior total was $2,200; can you improve it?"
    )
    disclosed = validate_call_grounding(call, [_logged_quote()])
    assert disclosed["valid"] is True, disclosed

    call["transcript"][0]["text"] = "Can you match $1,850?"
    amount_only = validate_call_grounding(call, [_logged_quote()])
    assert "undisclosed_simulated_claim" in _codes(amount_only)


def main():
    test_grounded_call_is_valid_and_auditable()
    test_unverified_or_incomplete_claim_cannot_be_leverage()
    test_unknown_money_and_fake_competitor_are_reported()
    test_known_competitor_must_be_declared_and_snapshot_must_be_frozen()
    test_foreign_quote_cannot_authorize_transcript_amounts()
    test_raw_spec_quote_is_not_automatic_leverage()
    test_agent_cannot_self_authorize_an_invented_quote()
    test_synthetic_leverage_requires_same_turn_demo_disclosure()
    print("EVIDENCE TEST PASSED: frozen claims, verified leverage, money and competitor checks")


if __name__ == "__main__":
    main()
