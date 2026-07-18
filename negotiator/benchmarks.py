"""Benchmark pricing + red-flag engine, driven entirely by the domain pack.

Two benchmark models:
  rate_card  — generic (benchmark.job_types): callout + hours*rate + parts,
               then declarative spec-matched multipliers. This is the model
               AI-generated sheets emit; plumbing uses it.
  crew model — the original moving-specific model (benchmark.crew_by_home_size).

Three consumers:
  1. red-flag evaluation on every logged quote,
  2. the negotiator's get_benchmark tool ("is this quote sane?"),
  3. the simulated counterparties' hidden ground-truth pricing.

Every function takes the pack explicitly (falls back to the process-default
vertical() for older call sites).
"""
from .config import vertical
from .models import QuoteIn


def _spec_value(spec: dict, dotted: str):
    """Resolve 'access.slab_foundation' against the spec dict."""
    node = spec
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _rate_card_estimate(spec: dict, b: dict) -> float:
    jt = b["job_types"].get(spec.get("job_type") or "other") \
        or b["job_types"].get("other") or next(iter(b["job_types"].values()))
    est = b.get("callout_fee_usd", 0) + jt["hours"] * b["hourly_rate_usd"] + jt.get("parts_usd", 0)
    for m in b.get("modifiers", []):
        v = _spec_value(spec, m["field"])
        if "equals" in m:
            match = v == m["equals"]
        elif "gte" in m:
            match = (v or 0) >= m["gte"]
        else:
            match = bool(v)
        if match:
            est *= m["multiplier"]
    return round(est, 2)


def _moving_crew_estimate(spec: dict, b: dict) -> float:
    crew = b["crew_by_home_size"].get(spec.get("home_size", "2BR"), b["crew_by_home_size"]["2BR"])
    est = crew["movers"] * crew["hours"] * b["per_mover_hourly_usd"]
    est += b["truck_fee_usd"] + b["per_mile_usd"] * float(spec.get("distance_miles", 0))

    m = b["modifiers"]
    services = spec.get("services", {})
    if services.get("packing"):
        est *= m["packing"]
    if services.get("disassembly"):
        est *= m["disassembly"]
    if services.get("storage"):
        est *= m["storage"]
    flights = (spec.get("origin", {}).get("stairs_flights", 0) or 0) + (
        spec.get("destination", {}).get("stairs_flights", 0) or 0)
    est *= m["per_stairs_flight"] ** flights
    if any(i.get("special") == "piano" for i in spec.get("inventory", [])):
        est *= m["piano"]
    if max(spec.get("origin", {}).get("parking_distance_ft", 0) or 0,
           spec.get("destination", {}).get("parking_distance_ft", 0) or 0) > 75:
        est *= m["long_carry_over_75ft"]
    return round(est, 2)


def base_estimate(spec: dict, pack: dict | None = None) -> float:
    b = (pack or vertical())["benchmark"]
    return _rate_card_estimate(spec, b) if "job_types" in b else _moving_crew_estimate(spec, b)


def market_range(spec: dict, pack: dict | None = None) -> dict:
    pack = pack or vertical()
    base = base_estimate(spec, pack)
    spread = pack["benchmark"]["market_spread"]
    return {
        "base_estimate": base,
        "fair_low": round(base * spread["low"]),
        "median": round(base * spread["median"]),
        "fair_high": round(base * spread["high"]),
        "red_flag_floor": round(base * spread["median"] * 0.70),  # the 30%-below-market line
    }


def evaluate_red_flags(quote: QuoteIn, spec: dict, pack: dict | None = None) -> list[dict]:
    """Rules come from the pack; conditions are evaluated here explicitly
    (no eval() of YAML strings — the YAML `rule` field is documentation)."""
    pack = pack or vertical()
    median = market_range(spec, pack)["median"]
    flags_cfg = {f["id"]: f for f in pack["red_flags"]}
    conditions_text = " ".join(quote.conditions).lower()

    hits = []
    def hit(fid):
        f = flags_cfg.get(fid)
        if f:
            hits.append({"id": fid, "severity": f["severity"], "label": f["label"]})

    if quote.total < 0.70 * median:
        hit("too_low")
    if not quote.binding:
        hit("non_binding")
    if quote.deposit > 0.25 * quote.total:
        hit("big_deposit")
    if len(quote.line_items) < 2:
        hit("no_itemization")
    if "today" in conditions_text:
        hit("pressure_expiry")
    return hits


def counterparty_pricing(persona: dict, spec: dict, pack: dict | None = None) -> dict:
    """Hidden ground truth for a simulated company: its back-office estimate.
    Served ONLY to that counterparty agent via its own webhook tool."""
    med = market_range(spec, pack)["median"]
    pol = persona["policy"]
    return {
        "list_price": round(med * pol["anchor_multiplier"]),
        "floor_price": round(med * pol["floor_multiplier"]),
        "hidden_fees": [
            {"code": f["code"], "amount": round(med * f["multiplier"]), "reveal_if": f["reveal_if"]}
            for f in pol.get("hidden_fees", [])
        ],
        "auto_bundle": pol.get("auto_bundle", []),
        "concessions": pol.get("concessions", []),
        "never": pol.get("never", ""),
        "quote_gate": pol.get("quote_gate", ""),
    }
