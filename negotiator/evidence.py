"""Post-call grounding checks over one immutable call snapshot.

This module deliberately has no database, network or configuration imports.  A
caller supplies the terminal call and the quote rows logged *by that call*.
Only facts already embedded in ``call["knowledge_snapshot"]`` can authorize a
competitive claim.  Newly stated vendor prices are authorized separately by a
structured quote logged during the call.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


_TERMINAL_SOURCE_STATES = {"completed", "complete", "done", "succeeded", "success"}
_AGENT_ROLES = {"agent", "assistant", "negotiator"}
_COUNTERPARTY_ROLES = {"user", "vendor", "counterparty", "callee", "business"}
_GENERIC_COMPETITOR_CLAIM = re.compile(
    r"\b(?:"
    r"(?:i|we)\s+(?:also\s+)?have\s+(?:an?\s+)?(?:recorded\s+)?"
    r"(?:competing|competitor|other)\s+(?:quote|bid|offer)|"
    r"(?:quote|bid|offer)\s+from|"
    r"(?:another|other)\s+(?:vendor|company)\s+(?:quoted|offered)"
    r")\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"(?<![\w])(?:"
    r"(?P<prefix>-?\s*(?:US\$|\$|USD\s*))\s*(?P<pnum>\d[\d,]*(?:\.\d+)?)\s*(?P<pk>[kK])?"
    r"|"
    r"(?P<snum>-?\d[\d,]*(?:\.\d+)?)\s*(?P<sk>[kK])?\s*"
    r"(?P<suffix>USD|US\s+dollars?|dollars?|bucks?)"
    r")",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"[^\w]+", re.UNICODE)


def validate_call_grounding(
    call: Mapping[str, Any],
    logged_quotes: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    *,
    money_tolerance: float = 1.0,
) -> dict:
    """Validate transcript claims against the call's frozen knowledge.

    Args:
        call: Terminal call record.  Its ``knowledge_snapshot`` is the sole
            source of pre-call facts.
        logged_quotes: Structured quotes emitted during this exact call.  These
            authorize new vendor totals, fees and concessions heard on the call.
        money_tolerance: Maximum absolute dollar difference accepted when
            matching a spoken amount.  It defaults to one dollar; no relative
            rounding is performed.

    Returns:
        A JSON-serializable mapping with ``valid``, ``issues``, ``allowed`` and
        ``used`` keys.  ``valid`` is false whenever any issue is present.
    """
    issues: list[dict] = []
    if not isinstance(call, Mapping):
        return _invalid_input("call must be a mapping")
    if not _finite_number(money_tolerance) or float(money_tolerance) < 0:
        return _invalid_input("money_tolerance must be a finite non-negative number")
    tolerance = float(money_tolerance)

    snapshot = call.get("knowledge_snapshot")
    if not isinstance(snapshot, Mapping):
        return _invalid_input("call.knowledge_snapshot must be a mapping")
    frozen_at = snapshot.get("snapshot_created_at") or snapshot.get("created_at")
    is_frozen = isinstance(frozen_at, str) and bool(frozen_at.strip())
    if not is_frozen:
        _issue(
            issues,
            "missing_frozen_snapshot",
            "The call snapshot has no creation timestamp, so its immutable provenance cannot be verified.",
        )

    allowed_rows = _mapping_list(
        snapshot.get("allowed_competitive_claims", snapshot.get("allowed_claims", []))
    )
    competitor_rows = _mapping_list(snapshot.get("competing_quotes", []))
    competitor_by_id = _unique_by_quote_id(competitor_rows)

    eligible_claims: dict[str, dict] = {}
    rejected_claims: list[dict] = []
    for allowed_row in allowed_rows:
        quote_id = _quote_id(allowed_row)
        if not quote_id:
            _issue(issues, "allowed_claim_missing_id", "A competitive claim has no quote_id.")
            continue
        detail = competitor_by_id.get(quote_id, allowed_row)
        rejection = _claim_rejection(allowed_row, detail, is_frozen)
        if rejection:
            rejected_claims.append({"quote_id": quote_id, "reason": rejection})
            _issue(
                issues,
                "ineligible_competitive_claim",
                f"Competitive quote {quote_id} is not spendable: {rejection}.",
                quote_id=quote_id,
                reason=rejection,
            )
            continue
        eligible_claims[quote_id] = {
            "quote_id": quote_id,
            "company": _company_name(detail) or _company_name(allowed_row),
            "total": float(detail["total"]),
            "binding": bool(detail.get("binding", allowed_row.get("binding", False))),
            "evidence_verified": True,
            "source": _completion_source(detail, is_frozen),
        }

    amount_sources: list[dict] = []
    for claim in eligible_claims.values():
        _add_amount(
            amount_sources,
            claim["total"],
            "competitive_claim",
            claim["quote_id"],
            "total",
        )
    for index, own in enumerate(_mapping_list(snapshot.get("own_quote_history", []))):
        source_id = _quote_id(own) or f"own_history:{index}"
        _add_quote_amounts(amount_sources, own, "own_history", source_id)
    benchmark = snapshot.get("benchmark")
    if isinstance(benchmark, Mapping):
        for field, value in benchmark.items():
            _add_amount(amount_sources, value, "benchmark", "benchmark", str(field))
    # Dollar-valued facts supplied by the customer are also legitimate.  Keep
    # their exact field path in the audit trail; no inferred/rounded value is
    # introduced.
    spec = snapshot.get("spec")
    if isinstance(spec, Mapping):
        for field, value in _numeric_paths(spec):
            _add_amount(amount_sources, value, "job_spec", "spec", field)

    turns = _transcript_turns(call.get("transcript"))
    if not turns:
        _issue(issues, "missing_transcript", "The completed call has no transcript to validate.")

    quotes = _coerce_quotes(logged_quotes)
    correlated_quotes: list[Mapping[str, Any]] = []
    quote_evidence: list[dict] = []
    current_call_id = str(call.get("id") or "")
    for index, quote in enumerate(quotes):
        mismatch = _quote_mismatch(call, quote, current_call_id)
        if mismatch:
            _issue(
                issues,
                "uncorrelated_logged_quote",
                f"Logged quote {str(quote.get('id') or index)} is not correlated to this call: {mismatch}.",
                quote_id=str(quote.get("id") or ""),
                reason=mismatch,
            )
            continue
        correlated_quotes.append(quote)
        evidence = verify_quote_counterparty_evidence(
            call, quote, money_tolerance=tolerance
        )
        quote_evidence.append(evidence)
        if evidence["valid"]:
            _add_quote_amounts(
                amount_sources,
                quote,
                "counterparty_confirmed_quote",
                str(quote.get("id") or f"logged:{index}"),
            )
        else:
            for reason in evidence["issues"]:
                _issue(
                    issues,
                    reason["code"],
                    reason["message"],
                    quote_id=str(quote.get("id") or ""),
                    **{key: value for key, value in reason.items()
                       if key not in {"code", "message"}},
                )

    declared_leverage: set[str] = set()
    for quote in correlated_quotes:
        raw_ids = quote.get("leverage_quote_ids") or []
        if not isinstance(raw_ids, list):
            _issue(
                issues,
                "invalid_leverage_ids",
                "leverage_quote_ids must be a list of frozen quote ids.",
                quote_id=str(quote.get("id") or ""),
            )
            continue
        declared_leverage.update(str(value) for value in raw_ids if str(value).strip())
        if quote.get("phase") == "initial" and raw_ids:
            _issue(
                issues,
                "initial_quote_used_leverage",
                "An initial quote cannot declare competitive leverage.",
                quote_id=str(quote.get("id") or ""),
            )
        if quote.get("negotiation_basis") == "competing_quote" and not raw_ids:
            _issue(
                issues,
                "missing_leverage_id",
                "A competing-quote negotiation must identify the exact frozen quote used.",
                quote_id=str(quote.get("id") or ""),
            )
    for quote_id in sorted(declared_leverage - set(eligible_claims)):
        _issue(
            issues,
            "unauthorized_leverage_quote",
            f"Leverage quote {quote_id} is not an eligible frozen competitive claim.",
            quote_id=quote_id,
        )

    money_used: list[dict] = []
    mentions: list[dict] = []
    observed_claim_ids: set[str] = set()
    all_competitors = _all_competitors(allowed_rows, competitor_rows)
    for turn_index, role, text in turns:
        normalized_text = _normalize(text)
        turn_money = []
        for raw, amount in _extract_money(text):
            matches = _amount_matches(amount, amount_sources, tolerance)
            money_record = {
                "turn_index": turn_index,
                "role": role,
                "raw": raw,
                "amount": amount,
                "authorized": bool(matches),
                "authorized_by": matches,
            }
            money_used.append(money_record)
            turn_money.append(money_record)
            if not matches:
                _issue(
                    issues,
                    "unsupported_money_amount",
                    f"Spoken amount {raw} is absent from the frozen claims, own history, benchmark and logged quote.",
                    turn_index=turn_index,
                    role=role,
                    raw=raw,
                    amount=amount,
                )

        turn_claim_ids: set[str] = set()
        for competitor in all_competitors:
            quote_id = competitor["quote_id"]
            company = competitor["company"]
            name_hit = bool(company and len(_normalize(company)) >= 4
                            and _normalize(company) in normalized_text)
            id_hit = bool(quote_id and quote_id.casefold() in text.casefold())
            if not (name_hit or id_hit):
                continue
            eligible = quote_id in eligible_claims
            declared = quote_id in declared_leverage
            if eligible:
                observed_claim_ids.add(quote_id)
                turn_claim_ids.add(quote_id)
            mention = {
                "turn_index": turn_index,
                "role": role,
                "quote_id": quote_id,
                "company": company,
                "matched_by": "company" if name_hit else "quote_id",
                "eligible": eligible,
                "declared": declared,
                "authorized": eligible and (role not in _AGENT_ROLES or declared),
            }
            mentions.append(mention)
            if not eligible:
                _issue(
                    issues,
                    "unauthorized_competitor_reference",
                    f"Transcript references competitor quote {quote_id}, which is not an eligible frozen claim.",
                    turn_index=turn_index,
                    role=role,
                    quote_id=quote_id,
                )
            elif role in _AGENT_ROLES and not declared:
                _issue(
                    issues,
                    "undeclared_competitor_reference",
                    f"The agent referenced {quote_id} without logging it in leverage_quote_ids.",
                    turn_index=turn_index,
                    role=role,
                    quote_id=quote_id,
                )

        if role in _AGENT_ROLES and _GENERIC_COMPETITOR_CLAIM.search(text):
            grounded_in_turn = any(
                quote_id in declared_leverage for quote_id in turn_claim_ids
            ) or _turn_money_matches_declared_claim(turn_money, declared_leverage, eligible_claims, tolerance)
            if not grounded_in_turn:
                _issue(
                    issues,
                    "unsupported_competitor_claim",
                    "The agent asserted a competing quote without an exact, declared frozen company/quote/total reference.",
                    turn_index=turn_index,
                    role=role,
                )

    allowed_money = _serialize_amount_sources(amount_sources)
    return {
        "valid": not issues,
        "issues": issues,
        "allowed": {
            "snapshot_created_at": str(frozen_at or ""),
            "claim_ids": sorted(eligible_claims),
            "claims": [eligible_claims[key] for key in sorted(eligible_claims)],
            "rejected_claims": rejected_claims,
            "money": allowed_money,
        },
        "used": {
            "leverage_quote_ids": sorted(declared_leverage),
            "observed_quote_ids": sorted(observed_claim_ids),
            "competitor_mentions": mentions,
            "money": money_used,
            "quote_evidence": quote_evidence,
        },
    }


def verify_quote_counterparty_evidence(
    call: Mapping[str, Any], quote: Mapping[str, Any], *, money_tolerance: float = 1.0
) -> dict:
    """Require new quote facts to come from the other side of the line.

    A tool call made by the negotiator is not evidence of what a vendor said.
    The exact evidence sentence, total, non-zero deposit, and every non-zero
    itemised amount must therefore occur in an explicit counterparty turn.
    This deliberately fails closed when transcript roles are missing/unknown.
    """
    tolerance = float(money_tolerance)
    turns = [turn for turn in _transcript_turns(call.get("transcript"))
             if turn[1] in _COUNTERPARTY_ROLES]
    issues: list[dict] = []
    if not turns:
        issues.append({
            "code": "missing_counterparty_turn",
            "message": "No explicit vendor/counterparty transcript turn can support the logged quote.",
        })
    counterparty_text = " ".join(text for _, _, text in turns)
    verbatim = str(quote.get("verbatim_evidence") or "").strip()
    verbatim_found = bool(verbatim and verbatim.casefold() in counterparty_text.casefold())
    if not verbatim_found:
        issues.append({
            "code": "missing_counterparty_verbatim_evidence",
            "message": "verbatim_evidence is absent from the vendor/counterparty turns.",
        })

    heard = []
    for turn_index, role, text in turns:
        for raw, amount in _extract_money(text):
            heard.append({"turn_index": turn_index, "role": role,
                          "raw": raw, "amount": amount,
                          "source_type": "counterparty_turn"})

    prior_amounts: list[dict] = []
    snapshot = call.get("knowledge_snapshot")
    if isinstance(snapshot, Mapping):
        for index, prior in enumerate(_mapping_list(snapshot.get("own_quote_history", []))):
            _add_quote_amounts(prior_amounts, prior, "verified_own_history",
                               _quote_id(prior) or f"own:{index}")

    fields: list[tuple[str, Any, bool]] = [("total", quote.get("total"), True)]
    fields.append(("deposit", quote.get("deposit"), bool(quote.get("deposit"))))
    for index, item in enumerate(_mapping_list(quote.get("line_items", []))):
        amount = item.get("amount")
        fields.append((f"line_items[{index}].amount", amount,
                       bool(_finite_number(amount) and float(amount) != 0)))

    support = []
    for field, value, required in fields:
        if not required:
            continue
        if _finite_number(value):
            expected = abs(float(value))
            matches = [row for row in heard
                       if abs(float(row["amount"]) - expected) <= tolerance]
            # On a recall, a previously verified itemisation can be carried
            # forward when the vendor restates the new/standing total. This is
            # auditable DB history, not a self-authored agent claim.
            if field.startswith("line_items["):
                matches.extend(
                    {"source_type": row["source_type"],
                     "source_id": row["source_id"], "field": row["field"],
                     "amount": row["amount"]}
                    for row in prior_amounts
                    if abs(float(row["amount"]) - expected) <= tolerance
                )
        else:
            matches = []
        support.append({"field": field, "value": value, "matches": matches,
                        "supported": bool(matches)})
        if not matches:
            issues.append({
                "code": "unsupported_logged_quote_field",
                "message": f"The logged {field} amount is absent from vendor/counterparty turns.",
                "field": field,
                "value": value,
            })
    return {
        "valid": not issues,
        "quote_id": str(quote.get("id") or ""),
        "counterparty_roles": sorted({role for _, role, _ in turns}),
        "verbatim_found": verbatim_found,
        "field_support": support,
        "issues": issues,
    }


def _invalid_input(message: str) -> dict:
    return {
        "valid": False,
        "issues": [{"code": "invalid_input", "message": message}],
        "allowed": {"snapshot_created_at": "", "claim_ids": [], "claims": [],
                    "rejected_claims": [], "money": []},
        "used": {"leverage_quote_ids": [], "observed_quote_ids": [],
                 "competitor_mentions": [], "money": [], "quote_evidence": []},
    }


def _issue(issues: list[dict], code: str, message: str, **details: Any) -> None:
    issues.append({"code": code, "message": message, **details})


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _coerce_quotes(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _quote_id(row: Mapping[str, Any]) -> str:
    return str(row.get("quote_id") or row.get("id") or "").strip()


def _company_name(row: Mapping[str, Any]) -> str:
    company = row.get("company") or row.get("company_name") or ""
    if isinstance(company, Mapping):
        company = company.get("name") or ""
    return str(company).strip()


def _unique_by_quote_id(rows: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        quote_id = _quote_id(row)
        if quote_id and quote_id not in result:
            result[quote_id] = row
    return result


def _claim_rejection(allowed: Mapping[str, Any], detail: Mapping[str, Any],
                     is_frozen: bool) -> str:
    if not _finite_number(allowed.get("total")) or not _finite_number(detail.get("total")):
        return "missing finite total"
    if abs(float(allowed["total"]) - float(detail["total"])) > 0.01:
        return "allowed total does not match the frozen quote detail"
    allowed_company = _company_name(allowed)
    detail_company = _company_name(detail)
    if allowed_company and detail_company and _normalize(allowed_company) != _normalize(detail_company):
        return "allowed company does not match the frozen quote detail"
    if detail.get("evidence_verified") is not True:
        return "transcript evidence is not verified"
    if not _source_completed(detail, is_frozen):
        return "source quote/call is not completed"
    return ""


def _source_completed(row: Mapping[str, Any], is_frozen: bool) -> bool:
    for field in ("call_completed", "quote_completed", "completed"):
        if field in row:
            return row.get(field) is True
    state = row.get("call_status") or row.get("quote_status") or row.get("status")
    if state is not None:
        return str(state).casefold() in _TERMINAL_SOURCE_STATES
    if row.get("ended_at"):
        return True
    if row.get("phase") == "document" or _quote_id(row).startswith("document:"):
        return True
    # knowledge.create_snapshot selects only quotes whose source call ended;
    # preserving call_id in that timestamped snapshot is its completion proof.
    return bool(is_frozen and row.get("call_id"))


def _completion_source(row: Mapping[str, Any], is_frozen: bool) -> str:
    if row.get("phase") == "document" or _quote_id(row).startswith("document:"):
        return "verified_document"
    if row.get("ended_at") or any(row.get(k) is True for k in
                                  ("call_completed", "quote_completed", "completed")):
        return "explicit_completed_source"
    if row.get("call_status") or row.get("quote_status") or row.get("status"):
        return "terminal_source_status"
    if is_frozen and row.get("call_id"):
        return "completed_call_in_frozen_snapshot"
    return "unknown"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _add_amount(target: list[dict], value: Any, source_type: str,
                source_id: str, field: str) -> None:
    if not _finite_number(value):
        return
    amount = abs(float(value))
    target.append({"amount": amount, "source_type": source_type,
                   "source_id": source_id, "field": field})


def _add_quote_amounts(target: list[dict], quote: Mapping[str, Any],
                       source_type: str, source_id: str) -> None:
    for field in ("total", "deposit"):
        _add_amount(target, quote.get(field), source_type, source_id, field)
    for index, item in enumerate(_mapping_list(quote.get("line_items", []))):
        _add_amount(target, item.get("amount"), source_type, source_id,
                    f"line_items[{index}].amount")


def _quote_mismatch(call: Mapping[str, Any], quote: Mapping[str, Any],
                    current_call_id: str) -> str:
    quote_call_id = str(quote.get("call_id") or "")
    if current_call_id and quote_call_id != current_call_id:
        return "call_id differs" if quote_call_id else "call_id is missing"
    for field in ("job_id", "company_id"):
        expected = str(call.get(field) or "")
        actual = str(quote.get(field) or "")
        if expected and actual != expected:
            return f"{field} differs" if actual else f"{field} is missing"
    return ""


def _transcript_turns(value: Any) -> list[tuple[int, str, str]]:
    if not isinstance(value, list):
        return []
    turns = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            continue
        text = row.get("text") if isinstance(row.get("text"), str) else row.get("message")
        if not isinstance(text, str) or not text.strip():
            continue
        turns.append((index, str(row.get("role") or "unknown").casefold(), text))
    return turns


def _extract_money(text: str) -> list[tuple[str, float]]:
    amounts = []
    for match in _MONEY_RE.finditer(text):
        number = match.group("pnum") or match.group("snum")
        multiplier = 1000.0 if (match.group("pk") or match.group("sk")) else 1.0
        try:
            value = float(number.replace(",", "")) * multiplier
        except (AttributeError, ValueError):
            continue
        prefix = match.group("prefix") or ""
        if "-" in prefix or number.startswith("-"):
            value = -abs(value)
        amounts.append((match.group(0).strip(), abs(value)))
    return amounts


def _amount_matches(amount: float, sources: list[dict], tolerance: float) -> list[dict]:
    return [
        {"source_type": row["source_type"], "source_id": row["source_id"],
         "field": row["field"]}
        for row in sources if abs(float(row["amount"]) - amount) <= tolerance
    ]


def _serialize_amount_sources(rows: list[dict]) -> list[dict]:
    grouped: dict[float, list[dict]] = {}
    for row in rows:
        amount = round(float(row["amount"]), 4)
        source = {key: row[key] for key in ("source_type", "source_id", "field")}
        if source not in grouped.setdefault(amount, []):
            grouped[amount].append(source)
    return [{"amount": amount, "sources": grouped[amount]} for amount in sorted(grouped)]


def _all_competitors(allowed_rows: list[Mapping[str, Any]],
                     detail_rows: list[Mapping[str, Any]]) -> list[dict]:
    result: dict[str, dict] = {}
    for row in [*detail_rows, *allowed_rows]:
        quote_id = _quote_id(row)
        if not quote_id:
            continue
        company = _company_name(row)
        if quote_id not in result or (company and not result[quote_id]["company"]):
            result[quote_id] = {"quote_id": quote_id, "company": company}
    return [result[key] for key in sorted(result)]


def _turn_money_matches_declared_claim(turn_money: list[dict], declared: set[str],
                                       eligible: dict[str, dict], tolerance: float) -> bool:
    for record in turn_money:
        for quote_id in declared & set(eligible):
            if abs(float(record["amount"]) - float(eligible[quote_id]["total"])) <= tolerance:
                return True
    return False


def _normalize(value: str) -> str:
    return " ".join(part for part in _SPACE_RE.sub(" ", value.casefold()).split() if part)


def _numeric_paths(value: Any, prefix: str = "") -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not prefix and str(key) in {"existing_quote", "existing_quotes"}:
                # Prior bids are authorized only through exact frozen claim
                # records, never merely because raw document data lives in spec.
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_numeric_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_numeric_paths(child, f"{prefix}[{index}]"))
    elif _finite_number(value):
        rows.append((prefix or "value", float(value)))
    return rows
