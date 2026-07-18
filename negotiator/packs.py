"""Domain packs — the "fogli di specifica".

One YAML sheet per (vertical, area_code) lives in verticals/. A sheet fully
defines a domain: who the estimator is, the base intake form questions, the
spec schema, the price benchmark, fee taxonomy, red flags and negotiation
levers. Swapping domain = swapping sheet, zero code changes.

Sheets are also AI-writable (see packgen.py), so every load path validates.
"""
from pathlib import Path

import yaml

from .config import ROOT

PACKS_DIR = ROOT / "verticals"

REQUIRED_TOP = ["meta", "spec_schema", "estimator_questions", "benchmark",
                "fee_taxonomy", "red_flags", "negotiation_levers", "conversation_policy"]
REQUIRED_META = ["vertical", "display_name", "counterparty_noun", "job_noun"]


def validate_pack(pack) -> list[str]:
    """Return a list of human-readable problems; empty list == valid.
    Used on every load AND as the contract the AI generator must satisfy."""
    if not isinstance(pack, dict):
        return ["pack is not a YAML mapping"]
    errs = []
    for k in REQUIRED_TOP:
        if k not in pack:
            errs.append(f"missing top-level section: {k}")
    meta = pack.get("meta", {})
    for k in REQUIRED_META:
        if not meta.get(k):
            errs.append(f"meta.{k} is missing or empty")

    schema = pack.get("spec_schema", {})
    req, fields = schema.get("required"), schema.get("fields")
    if not isinstance(req, list) or not req:
        errs.append("spec_schema.required must be a non-empty list")
    if not isinstance(fields, dict) or not fields:
        errs.append("spec_schema.fields must be a non-empty mapping")
    elif isinstance(req, list):
        for f in req:
            if f not in fields:
                errs.append(f"spec_schema.required field '{f}' not defined in spec_schema.fields")

    qs = pack.get("estimator_questions")
    if not isinstance(qs, list) or not qs or not all(isinstance(q, str) for q in qs):
        errs.append("estimator_questions must be a non-empty list of strings")

    b = pack.get("benchmark", {})
    if isinstance(b, dict):
        spread = b.get("market_spread", {})
        if not all(k in spread for k in ("low", "median", "high")):
            errs.append("benchmark.market_spread needs low/median/high")
        elif not spread["low"] <= spread["median"] <= spread["high"]:
            errs.append("benchmark.market_spread must satisfy low <= median <= high")
        if "job_types" in b:  # generic rate-card model
            if not b.get("hourly_rate_usd"):
                errs.append("rate-card benchmark needs hourly_rate_usd")
            for name, jt in (b.get("job_types") or {}).items():
                if not isinstance(jt, dict) or "hours" not in jt:
                    errs.append(f"benchmark.job_types.{name} needs at least 'hours'")
            for m in b.get("modifiers", []):
                if "field" not in m or "multiplier" not in m:
                    errs.append("every benchmark modifier needs 'field' and 'multiplier'")
        elif "crew_by_home_size" not in b:
            errs.append("benchmark must be a rate-card (job_types) or a moving-style crew model")
    else:
        errs.append("benchmark must be a mapping")

    if not isinstance(pack.get("fee_taxonomy"), dict) or not pack.get("fee_taxonomy"):
        errs.append("fee_taxonomy must be a non-empty mapping of code -> label")
    for i, f in enumerate(pack.get("red_flags", []) or []):
        if not all(k in f for k in ("id", "severity", "label")):
            errs.append(f"red_flags[{i}] needs id/severity/label")
    for i, l in enumerate(pack.get("negotiation_levers", []) or []):
        if not all(k in l for k in ("id", "play")):
            errs.append(f"negotiation_levers[{i}] needs id/play")
    pol = pack.get("conversation_policy", {})
    for k in ("disclosure_line", "robot_question_response", "hard_rules"):
        if not pol.get(k):
            errs.append(f"conversation_policy.{k} is missing or empty")
    return errs


def _scan() -> list[dict]:
    """All parseable sheets on disk (unvalidated), each with meta present."""
    packs = []
    for path in sorted(PACKS_DIR.glob("*.yaml")):
        try:
            pack = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(pack, dict) and isinstance(pack.get("meta"), dict):
            pack["meta"].setdefault("area_code", "")
            pack["_file"] = path.name
            packs.append(pack)
    return packs


def list_packs() -> list[dict]:
    return [{"vertical": p["meta"]["vertical"], "area_code": p["meta"]["area_code"],
             "display_name": p["meta"].get("display_name", ""), "file": p["_file"],
             "valid": not validate_pack(p)} for p in _scan()]


def load_pack(vertical_name: str, area_code: str = "") -> dict:
    """Exact (vertical, area) sheet if it exists, else the domain's base sheet
    (area_code == ""), else any sheet of that domain. No caching: AI-generated
    sheets must be pickable the moment they land on disk."""
    candidates = [p for p in _scan() if p["meta"]["vertical"] == vertical_name]
    if not candidates:
        raise FileNotFoundError(f"no pack for vertical '{vertical_name}' in {PACKS_DIR}")
    pack = (next((p for p in candidates if p["meta"]["area_code"] == (area_code or "")), None)
            or next((p for p in candidates if p["meta"]["area_code"] == ""), None)
            or candidates[0])
    errs = validate_pack(pack)
    if errs:
        raise ValueError(f"invalid pack {pack['_file']}: " + "; ".join(errs))
    return pack


def save_pack(pack: dict, force: bool = False) -> Path:
    """Persist a (validated) sheet as verticals/<vertical>[-<area>].yaml."""
    errs = validate_pack(pack)
    if errs:
        raise ValueError("refusing to save invalid pack: " + "; ".join(errs))
    meta = pack["meta"]
    slug = meta["vertical"] + (f"-{meta['area_code']}" if meta.get("area_code") else "")
    path = PACKS_DIR / f"{slug}.yaml"
    if path.exists() and not force:
        raise FileExistsError(f"{path.name} already exists (use force to overwrite)")
    clean = {k: v for k, v in pack.items() if not k.startswith("_")}
    path.write_text(yaml.safe_dump(clean, sort_keys=False, allow_unicode=True, width=100))
    return path
