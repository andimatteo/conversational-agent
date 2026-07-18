"""Benchmark pricing + red-flag engine, driven entirely by the vertical pack.

Three consumers:
  1. red-flag evaluation on every logged quote,
  2. the negotiator's get_benchmark tool ("is this quote sane?"),
  3. the simulated counterparties' hidden ground-truth pricing.
"""
from .config import vertical
from .models import QuoteIn


def base_estimate(spec: dict) -> float:
    b = vertical()["benchmark"]
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


def market_range(spec: dict) -> dict:
    base = base_estimate(spec)
    spread = vertical()["benchmark"]["market_spread"]
    return {
        "base_estimate": base,
        "fair_low": round(base * spread["low"]),
        "median": round(base * spread["median"]),
        "fair_high": round(base * spread["high"]),
        "red_flag_floor": round(base * spread["median"] * 0.70),  # the 30%-below-market line
    }


def evaluate_red_flags(quote: QuoteIn, spec: dict) -> list[dict]:
    """Rules come from the vertical pack; conditions are evaluated here explicitly
    (no eval() of YAML strings — the YAML `rule` field is documentation)."""
    median = market_range(spec)["median"]
    flags_cfg = {f["id"]: f for f in vertical()["red_flags"]}
    conditions_text = " ".join(quote.conditions).lower()

    hits = []
    def hit(fid):
        f = flags_cfg[fid]
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


def counterparty_pricing(persona: dict, spec: dict) -> dict:
    """Hidden ground truth for a simulated company: its back-office estimate.
    Served ONLY to that counterparty agent via its own webhook tool."""
    med = market_range(spec)["median"]
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
