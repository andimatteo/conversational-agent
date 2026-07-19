"""Deterministic transcript-only calls for debug mode.

This module is deliberately pure: it does not import an ElevenLabs client, touch
the database, open an audio device, make a network request, or inspect mutable
global state.  The caller supplies a frozen job/company/context/domain-pack
snapshot and receives the complete result that an orchestrator can persist.

Debug calls use real discovered company records unchanged, while simulating only
the conversation and commercial response.  That distinction is explicit in the
returned validation metadata and is never injected into the dialogue itself.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .benchmarks import market_range


_STYLES = ("stonewaller", "lowballer", "upseller")
_STYLE_ALIASES = {
    "stonewall": "stonewaller",
    "stonewaller": "stonewaller",
    "tough": "stonewaller",
    "gruff": "stonewaller",
    "lowball": "lowballer",
    "lowballer": "lowballer",
    "budget": "lowballer",
    "upsell": "upseller",
    "upseller": "upseller",
    "premium": "upseller",
}


def generate_debug_result(job: dict, company: dict, kind: str,
                          context: dict, pack: dict) -> dict:
    """Generate one deterministic, grounded quote or negotiation result.

    ``context`` is treated as an immutable knowledge snapshot.  For negotiation
    it may contain ``competing_quotes`` and ``own_quote_history``; no commercial
    claim outside those collections is ever introduced into the dialogue.

    The returned mapping intentionally has the same five top-level keys for all
    outcomes: ``transcript``, ``quote``, ``outcome``, ``learned_questions`` and
    ``validation``.  A negotiation without a prior vendor quote returns
    ``quote=None`` and a structured callback outcome rather than fabricating a
    standing offer.
    """
    if kind not in ("quote", "negotiate"):
        raise ValueError("kind must be 'quote' or 'negotiate'")
    if not isinstance(job, dict) or not job.get("id"):
        raise ValueError("job must contain a non-empty id")
    if not isinstance(company, dict) or not company.get("id") or not company.get("name"):
        raise ValueError("company must contain non-empty id and name")
    if not isinstance(context, dict):
        raise ValueError("context must be a mapping")
    if not isinstance(pack, dict):
        raise ValueError("pack must be a mapping")

    # Defensive copies make accidental mutation inside this module impossible
    # and make the fingerprints describe exactly what this call was grounded in.
    frozen_context = deepcopy(context)
    context_spec = frozen_context.get("spec")
    frozen_spec = deepcopy(context_spec if isinstance(context_spec, dict)
                           else (job.get("spec") or {}))
    spec_source = "context" if isinstance(context_spec, dict) else "job"
    style = _style_for(company)
    benchmark, benchmark_source = _frozen_benchmark(frozen_spec, frozen_context, pack)
    learned, learned_line = _learned_questions(frozen_spec, frozen_context, pack)

    if kind == "quote":
        transcript, quote, outcome, grounding = _initial_quote(
            job, company, style, frozen_spec, benchmark, pack, learned_line)
    else:
        transcript, quote, outcome, grounding = _negotiation(
            job, company, style, frozen_spec, frozen_context, pack, learned_line)

    validation = _validate(
        job=job,
        company=company,
        kind=kind,
        style=style,
        spec=frozen_spec,
        context=frozen_context,
        transcript=transcript,
        quote=quote,
        outcome=outcome,
        grounding=grounding,
        benchmark=benchmark,
        benchmark_source=benchmark_source,
        spec_source=spec_source,
    )
    return {
        "transcript": transcript,
        "quote": quote,
        "outcome": outcome,
        "learned_questions": learned,
        "validation": validation,
    }


def _style_for(company: dict) -> str:
    explicit = " ".join(str(company.get(k, "")) for k in ("persona", "style")).casefold()
    for token, style in _STYLE_ALIASES.items():
        if token in explicit:
            return style
    identity = f"{company.get('id', '')}|{company.get('name', '')}|{company.get('phone', '')}"
    bucket = hashlib.sha256(identity.encode("utf-8")).digest()[0] % len(_STYLES)
    return _STYLES[bucket]


def _frozen_benchmark(spec: dict, context: dict, pack: dict) -> tuple[dict, str]:
    supplied = context.get("benchmark")
    required = ("fair_low", "median", "fair_high", "red_flag_floor")
    if isinstance(supplied, dict) and all(_positive_number(supplied.get(k)) for k in required):
        return deepcopy(supplied), "context"
    # This is still frozen and deterministic: market_range is a pure function of
    # the supplied spec and pack, and the result is embedded in validation.
    return market_range(spec, pack), "spec_and_pack"


def _initial_quote(job: dict, company: dict, style: str, spec: dict,
                   benchmark: dict, pack: dict, learned_line: str) -> tuple:
    median = float(benchmark["median"])
    codes = _fee_codes(pack)
    disclosure = _disclosure(pack)
    summary = _spec_summary(spec)
    vendor = company["name"]

    if style == "stonewaller":
        total = _money_round(median * 1.06)
        base = _money_round(total * 0.82)
        fee = _money_round(total * 0.10)
        remainder = _money_round(total - base - fee)
        items = [
            _line(pack, codes[0], base, "base"),
            _line(pack, codes[1], fee, "fee"),
            _line(pack, codes[2], remainder, "fee"),
        ]
        binding, deposit = True, 0.0
        conditions = ["Price holds only while the confirmed job specification remains unchanged."]
        opening = "I do not quote from vague descriptions. Give me the complete scope first."
        evidence = f"With that exact scope, our itemised, binding total is {_usd(total)}."
        detail = (f"That is {_usd(base)} for {_label(pack, codes[0])}, "
                  f"{_usd(fee)} for {_label(pack, codes[1])}, and "
                  f"{_usd(remainder)} for {_label(pack, codes[2])}.")
    elif style == "lowballer":
        anchor = _money_round(median * 0.62)
        fee = _money_round(median * 0.18)
        addon = _money_round(median * 0.12)
        total = _money_round(anchor + fee + addon)
        items = [
            _line(pack, codes[0], anchor, "base", label="Advertised starting price"),
            _line(pack, codes[1], fee, "fee"),
            _line(pack, codes[2], addon, "fee"),
        ]
        binding, deposit = False, _money_round(total * 0.20)
        conditions = ["Non-binding until the vendor verifies conditions on site."]
        opening = f"We can probably start around {_usd(anchor)} if it is straightforward."
        detail = (f"The starting price excludes {_label(pack, codes[1])} at {_usd(fee)} "
                  f"and {_label(pack, codes[2])} at {_usd(addon)}.")
        evidence = (f"The current all-in estimate is {_usd(total)}, with a {_usd(deposit)} "
                    "deposit, but it is non-binding until we verify the site.")
    else:  # upseller
        base = _money_round(median * 1.05)
        addon = _money_round(median * 0.23)
        fee = _money_round(median * 0.10)
        total = _money_round(base + addon + fee)
        items = [
            _line(pack, codes[0], base, "base", label="Full-service base"),
            _line(pack, codes[2], addon, "addon", label=f"Premium {_label(pack, codes[2])}"),
            _line(pack, codes[1], fee, "fee"),
        ]
        binding, deposit = True, _money_round(total * 0.15)
        conditions = ["Quote includes the listed premium package; removing it requires a revised quote."]
        opening = "For this scope I recommend our full-service package so nothing is left exposed."
        detail = (f"It includes {_usd(base)} for the core work, {_usd(addon)} for the premium package, "
                  f"and {_usd(fee)} for {_label(pack, codes[1])}.")
        evidence = f"The itemised, binding package total is {_usd(total)} with a {_usd(deposit)} deposit."

    transcript = [
        _turn("agent", disclosure),
        _turn("vendor", f"{vendor}, how can I help?"),
        _turn("agent", f"I need a comparable quote for this confirmed scope: {summary}"),
        _turn("vendor", opening),
        _turn("agent", "Please separate the base work from every fee and give me the real all-in total."),
        _turn("vendor", detail),
        _turn("agent", "Is that total binding, what deposit applies, and what could still change it?"),
        _turn("vendor", evidence),
    ]
    if learned_line:
        transcript.extend([
            _turn("agent", "Is there one customer detail that would make the next quote more precise?"),
            _turn("vendor", learned_line),
        ])
    transcript.append(_turn("agent", "Thank you. I have the itemised outcome recorded."))

    quote = {
        "job_id": job["id"],
        "company_id": company["id"],
        "line_items": items,
        "total": total,
        "binding": binding,
        "deposit": deposit,
        "valid_until": "",
        "conditions": conditions,
        "verbatim_evidence": evidence,
        "phase": "initial",
    }
    outcome = {
        "job_id": job["id"],
        "company_id": company["id"],
        "outcome": "quote",
        "callback_time": "",
        "decline_reason": "",
        "summary": f"Itemised {'binding quote' if binding else 'non-binding estimate'} of {_usd(total)}.",
    }
    grounding = {
        "used_competing_quotes": [],
        "used_own_quotes": [],
        "concession_grounded": False,
        "concession_amount": 0.0,
    }
    return transcript, quote, outcome, grounding


def _negotiation(job: dict, company: dict, style: str, spec: dict,
                 context: dict, pack: dict, learned_line: str) -> tuple:
    vendor = company["name"]
    disclosure = _disclosure(pack)
    own_history = _own_quotes(context.get("own_quote_history"), company["id"])
    competitors = _competing_quotes(context.get("competing_quotes"), company["id"])

    if not own_history:
        transcript = [
            _turn("agent", disclosure),
            _turn("vendor", f"{vendor}, how can I help?"),
            _turn("agent", "I am following up on the earlier quote for the confirmed job scope."),
            _turn("vendor", "I cannot locate a prior written figure, so I will not pretend we agreed to one."),
            _turn("agent", "Understood. Please review the scope and call back with an itemised figure."),
        ]
        outcome = {
            "job_id": job["id"], "company_id": company["id"], "outcome": "callback",
            "callback_time": "", "decline_reason": "",
            "summary": "Vendor could not verify a prior quote; no price claim or concession was made.",
        }
        grounding = {"used_competing_quotes": [], "used_own_quotes": [],
                     "concession_grounded": False, "concession_amount": 0.0}
        return transcript, None, outcome, grounding

    own = own_history[-1]
    own_total = own["total"]
    better = next((q for q in competitors if q["total"] < own_total), None)
    own_ref = _quote_ref(own)
    transcript = [
        _turn("agent", disclosure),
        _turn("vendor", f"{vendor}, how can I help?"),
        _turn("agent", f"I am following up on your recorded {_usd(own_total)} quote for the same confirmed scope."),
    ]

    concession = 0.0
    used_competitors = []
    if better:
        used_competitors = [_quote_ref(better)]
        transcript.append(_turn(
            "agent",
            f"I also have a recorded quote from {better['company']} for {_usd(better['total'])}. "
            "Can you improve your price without changing the scope?",
        ))
        gap = own_total - better["total"]
        fraction = {"stonewaller": 0.40, "lowballer": 0.20, "upseller": 1.0}[style]
        if style == "upseller":
            new_total = _money_round(better["total"] * 0.98)
        else:
            new_total = _money_round(own_total - gap * fraction)
        new_total = max(1.0, min(own_total, new_total))
        concession = _money_round(own_total - new_total)
    else:
        new_total = own_total

    if concession > 0:
        if style == "stonewaller":
            evidence = (f"From our prior {_usd(own_total)}, I cannot match it, but I can "
                        f"reduce our total by {_usd(concession)} to {_usd(new_total)} "
                        "for the unchanged scope.")
        elif style == "lowballer":
            evidence = (f"From our prior {_usd(own_total)}, I can remove {_usd(concession)} "
                        f"in fees and make the revised estimate {_usd(new_total)}.")
        else:
            evidence = (f"From our prior {_usd(own_total)}, because you have that recorded "
                        f"competing quote, I can reduce it by {_usd(concession)} and beat it "
                        f"with {_usd(new_total)} for the same scope.")
        transcript.append(_turn("vendor", evidence))
        transcript.append(_turn("agent", f"Please confirm the final total is {_usd(new_total)} and restate its terms."))
    else:
        evidence = f"Our recorded {_usd(own_total)} total remains our best offer for the unchanged scope."
        transcript.append(_turn("agent", "Can you improve the price or terms based on the information on file?"))
        transcript.append(_turn("vendor", evidence))

    binding = bool(own.get("binding", False))
    deposit = _money_round(float(own.get("deposit") or 0))
    terms = "binding" if binding else "non-binding"
    transcript.append(_turn("vendor", f"The {_usd(new_total)} total is {terms}; the deposit remains {_usd(deposit)}."))
    if learned_line:
        transcript.extend([
            _turn("agent", "What customer detail should be confirmed before another negotiation?"),
            _turn("vendor", learned_line),
        ])
    transcript.append(_turn("agent", "Thank you. I have recorded the final standing terms."))

    codes = _fee_codes(pack)
    if concession > 0:
        line_items = [
            _line(pack, codes[0], own_total, "base", label="Previously quoted total"),
            _line(pack, _discount_code(pack), -concession, "discount",
                  label=f"Grounded competitive adjustment vs {better['company']}"),
        ]
    else:
        line_items = _usable_line_items(own.get("line_items"), own_total, pack)
    quote = {
        "job_id": job["id"], "company_id": company["id"],
        "line_items": line_items, "total": new_total, "binding": binding,
        "deposit": deposit, "valid_until": str(own.get("valid_until") or ""),
        "conditions": list(own.get("conditions") or []),
        "verbatim_evidence": evidence, "phase": "negotiated",
    }
    outcome = {
        "job_id": job["id"], "company_id": company["id"], "outcome": "quote",
        "callback_time": "", "decline_reason": "",
        "summary": (f"Grounded concession of {_usd(concession)}; final total {_usd(new_total)}."
                    if concession else f"Standing offer confirmed at {_usd(new_total)}; no concession."),
    }
    grounding = {
        "used_competing_quotes": used_competitors,
        "used_own_quotes": [own_ref],
        "concession_grounded": concession > 0,
        "concession_amount": concession,
    }
    return transcript, quote, outcome, grounding


def _own_quotes(value: Any, company_id: str) -> list[dict]:
    rows = value if isinstance(value, list) else []
    valid = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict) or not _positive_number(raw.get("total")):
            continue
        if raw.get("company_id") and raw["company_id"] != company_id:
            continue
        row = deepcopy(raw)
        row["total"] = _money_round(float(row["total"]))
        row["_snapshot_index"] = index
        valid.append(row)
    valid.sort(key=lambda q: (str(q.get("created_at", "")), q["_snapshot_index"]))
    return valid


def _competing_quotes(value: Any, company_id: str) -> list[dict]:
    # Accept either the array returned by get_competing_quotes or its wrapper.
    if isinstance(value, dict):
        value = value.get("competing_quotes")
    rows = value if isinstance(value, list) else []
    valid = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict) or not _positive_number(raw.get("total")):
            continue
        if raw.get("company_id") == company_id:
            continue
        company = raw.get("company") or raw.get("company_name")
        if isinstance(company, dict):
            company = company.get("name")
        if not isinstance(company, str) or not company.strip():
            continue
        row = deepcopy(raw)
        row["company"] = company.strip()
        row["total"] = _money_round(float(row["total"]))
        row["_snapshot_index"] = index
        valid.append(row)
    valid.sort(key=lambda q: (q["total"], q["company"].casefold(), q["_snapshot_index"]))
    return valid


def _quote_ref(quote: dict) -> dict:
    ref = {"id": quote.get("id") or quote.get("quote_id") or "",
           "total": quote["total"]}
    if quote.get("company"):
        ref["company"] = quote["company"]
    if quote.get("company_id"):
        ref["company_id"] = quote["company_id"]
    return ref


def _learned_questions(spec: dict, context: dict, pack: dict) -> tuple[list[dict], str]:
    supplied = context.get("learned_question_candidates")
    if isinstance(supplied, list):
        for candidate in supplied:
            if isinstance(candidate, str) and candidate.strip():
                question, why = candidate.strip(), "This unresolved detail may change vendor pricing."
            elif isinstance(candidate, dict) and str(candidate.get("question", "")).strip():
                question = str(candidate["question"]).strip()
                why = str(candidate.get("why_it_matters") or
                          "This unresolved detail may change vendor pricing.").strip()
            else:
                continue
            evidence = f"The customer still needs to confirm this pricing detail: {question}"
            return ([{"question": question, "why_it_matters": why,
                      "verbatim_evidence": evidence}], evidence)

    fields = (pack.get("spec_schema") or {}).get("fields") or {}
    for field in fields:
        if field in ("vertical", "area_code", "notes") or _has_value(spec.get(field)):
            continue
        label = field.replace("_", " ")
        question = f"Can you confirm the {label} before the next vendor call?"
        why = f"The domain specification identifies {label} as a potentially price-relevant detail."
        evidence = f"The customer still needs to confirm {label}; that detail can change the final price."
        return ([{"question": question, "why_it_matters": why,
                  "verbatim_evidence": evidence}], evidence)
    return [], ""


def _validate(*, job: dict, company: dict, kind: str, style: str, spec: dict,
              context: dict, transcript: list[dict], quote: dict | None,
              outcome: dict, grounding: dict, benchmark: dict,
              benchmark_source: str, spec_source: str) -> dict:
    errors, warnings = [], []
    dialogue = "\n".join(turn["text"] for turn in transcript)
    if not transcript or any(set(t) != {"role", "text"} for t in transcript):
        errors.append("transcript must contain only role/text turns")
    if company["name"] not in dialogue:
        errors.append("real vendor name is missing from transcript")
    if outcome.get("job_id") != job["id"] or outcome.get("company_id") != company["id"]:
        errors.append("outcome is not linked to the supplied job and vendor")

    if quote is not None:
        if quote.get("job_id") != job["id"] or quote.get("company_id") != company["id"]:
            errors.append("quote is not linked to the supplied job and vendor")
        item_total = _money_round(sum(float(li.get("amount") or 0)
                                     for li in quote.get("line_items", [])))
        if abs(item_total - float(quote.get("total") or 0)) > 0.01:
            errors.append(f"line items sum to {item_total}, not quote total {quote.get('total')}")
        evidence = quote.get("verbatim_evidence", "")
        if not evidence or evidence not in dialogue:
            errors.append("verbatim quote evidence is absent from transcript")
    elif outcome.get("outcome") == "quote":
        errors.append("quote outcome has no structured quote")

    concession = float(grounding.get("concession_amount") or 0)
    if concession > 0 and not (grounding.get("used_own_quotes") and
                               grounding.get("used_competing_quotes") and
                               grounding.get("concession_grounded")):
        errors.append("a concession was produced without both grounded quote sources")
    if kind == "negotiate" and quote is None:
        warnings.append("no prior vendor quote in frozen context; negotiation ended as callback")
    if benchmark_source != "context":
        warnings.append("benchmark snapshot was derived from the supplied spec and pack")
    supplied_spec_hash = context.get("spec_hash")
    if supplied_spec_hash and supplied_spec_hash != _fingerprint(spec):
        errors.append("frozen context spec_hash does not match its spec")

    metadata = {
        "mode": "debug",
        "debug": True,
        "simulated": True,
        "network_used": False,
        "audio_generated": False,
        "elevenlabs_used": False,
        "vendor": {
            "id": company["id"],
            "name": company["name"],
            "source": company.get("source", ""),
            "source_ids": deepcopy(company.get("source_ids") or {}),
        },
        "kind": kind,
        "style": style,
        "spec_fingerprint": _fingerprint(spec),
        "context_fingerprint": _fingerprint(context),
        "benchmark_source": benchmark_source,
        "spec_source": spec_source,
        "benchmark": deepcopy(benchmark),
    }
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "metadata": metadata,
        "grounding": grounding,
    }


def _fee_codes(pack: dict) -> tuple[str, str, str]:
    taxonomy = list((pack.get("fee_taxonomy") or {"base": "Base work", "other": "Other"}).keys())
    base = "base" if "base" in taxonomy else taxonomy[0]
    non_base = [c for c in taxonomy if c not in (base, "deposit")]
    first = non_base[0] if non_base else base
    addon_candidates = [c for c in non_base if c not in (first, "other")]
    second = addon_candidates[0] if addon_candidates else (non_base[-1] if non_base else base)
    return base, first, second


def _discount_code(pack: dict) -> str:
    taxonomy = pack.get("fee_taxonomy") or {}
    return "discount" if "discount" in taxonomy else ("other" if "other" in taxonomy else _fee_codes(pack)[0])


def _line(pack: dict, code: str, amount: float, kind: str, label: str = "") -> dict:
    return {"label": label or _label(pack, code), "code": code,
            "amount": _money_round(amount), "kind": kind,
            "contingent": False, "notes": ""}


def _usable_line_items(value: Any, total: float, pack: dict) -> list[dict]:
    if isinstance(value, list) and value:
        items = deepcopy(value)
        if all(isinstance(li, dict) and isinstance(li.get("amount"), (int, float)) for li in items):
            if abs(sum(float(li["amount"]) for li in items) - total) <= 0.01:
                return items
    return [_line(pack, _fee_codes(pack)[0], total, "base", label="Confirmed standing total")]


def _label(pack: dict, code: str) -> str:
    return str((pack.get("fee_taxonomy") or {}).get(code) or code.replace("_", " ").title())


def _disclosure(pack: dict) -> str:
    policy = pack.get("conversation_policy") or {}
    return str(policy.get("disclosure_line") or
               "I'm an AI assistant calling on behalf of a customer with a confirmed job request.").strip()


def _spec_summary(spec: dict) -> str:
    parts = []
    for key in ("job_type", "problem_description", "home_size", "distance_miles",
                "move_date", "property_type", "urgency", "area_code"):
        if _has_value(spec.get(key)):
            parts.append(f"{key.replace('_', ' ')}={_compact(spec[key])}")
    for key in ("origin", "destination", "access", "services"):
        if isinstance(spec.get(key), dict) and spec[key]:
            nested = ", ".join(f"{k.replace('_', ' ')} {_compact(v)}"
                               for k, v in spec[key].items() if _has_value(v))
            if nested:
                parts.append(f"{key}: {nested}")
    if not parts:
        for key, value in spec.items():
            if _has_value(value) and not isinstance(value, (dict, list)):
                parts.append(f"{key.replace('_', ' ')}={_compact(value)}")
            if len(parts) == 6:
                break
    return "; ".join(parts) if parts else "the exact structured specification already on file"


def _compact(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _turn(role: str, text: str) -> dict:
    return {"role": role, "text": text}


def _usd(value: float) -> str:
    value = _money_round(value)
    return f"${value:,.0f}" if value.is_integer() else f"${value:,.2f}"


def _money_round(value: float) -> float:
    return round(float(value) + 1e-10, 2)


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
