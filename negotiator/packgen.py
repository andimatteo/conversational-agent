"""AI writer for domain sheets ("il foglio di configurazione scrivibile dall'AI").

Given a domain (+ optional area code), asks OpenAI to write a complete vertical
pack YAML using plumbing.yaml as the canonical example, validates it against the
same contract every load path enforces (packs.validate_pack), retries once with
the validation errors, and saves it to verticals/.

  python -m negotiator.packgen --vertical hvac --area 28203 --notes "heat pump heavy market"

Exposed to the product API as POST /api/verticals/generate.
"""
import argparse
import sys
from pathlib import Path

import yaml

from .config import OPENAI_API_KEY, ROOT
from .packs import load_pack, save_pack, validate_pack

_CONTRACT = """
Rules the YAML MUST satisfy (it is machine-validated):
- top-level sections: meta, spec_schema, estimator_questions, benchmark,
  fee_taxonomy, red_flags, negotiation_levers, conversation_policy
- meta: vertical (the requested slug), area_code (the requested area, as a string),
  display_name, counterparty_noun, job_noun, evidence (2-3 honest, hedged lines
  about price opacity in this trade — no invented citations)
- also include: estimator_persona (who the intake agent IS: a veteran professional
  estimator of THIS trade), estimator_first_message, estimator_probes
- spec_schema: required (non-empty list) + fields defining every required name
- estimator_questions: 8-12 conversational intake questions a professional
  estimator of this trade would ask (these are the base form questions)
- benchmark: MUST use the generic rate-card model: callout_fee_usd,
  hourly_rate_usd, job_types (each: hours, parts_usd), modifiers
  (each: field + multiplier, optionally equals/gte; field may use dotted paths
  into the spec), market_spread with low <= median <= high
- fee_taxonomy: mapping code -> label; include base and deposit and other
- red_flags: keep the five generic ids (too_low, non_binding, big_deposit,
  no_itemization, pressure_expiry) with trade-appropriate labels; each needs
  id, rule, severity, label
- negotiation_levers: each needs id + play, ordered by priority
- conversation_policy: disclosure_line, robot_question_response, hard_rules
  (keep the honesty rules from the example, adapted to the trade)
"""


def _example_yaml() -> str:
    return (ROOT / "verticals" / "plumbing.yaml").read_text()


def _ask_llm(vertical_name: str, area_code: str, notes: str, previous_errors: list[str]) -> dict:
    from openai import OpenAI  # lazy: needs OPENAI_API_KEY
    client = OpenAI(api_key=OPENAI_API_KEY)

    system = (
        "You write domain configuration sheets for 'The Negotiator', a service whose voice "
        "agents interview a customer, phone local companies for itemised quotes, and negotiate. "
        "A sheet fully retargets the product to a trade+area with zero code changes.\n"
        + _CONTRACT +
        "\nHere is the canonical example sheet (plumbing). Match its structure exactly:\n\n"
        + _example_yaml() +
        "\nOutput ONLY the YAML document. No markdown fences, no commentary."
    )
    user = (f"Write the sheet for domain '{vertical_name}'"
            + (f", service area code '{area_code}'" if area_code else "")
            + ". Calibrate rates/hours to plausible US figures for this trade."
            + (f"\nOperator notes: {notes}" if notes else ""))
    if previous_errors:
        user += ("\n\nYour previous attempt failed validation with these errors — fix ALL of them:\n"
                 + "\n".join(f"- {e}" for e in previous_errors))

    resp = client.chat.completions.create(
        model="gpt-4o", temperature=0.3,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return yaml.safe_load(text)


def generate_pack(vertical_name: str, area_code: str = "", notes: str = "",
                  force: bool = False, attempts: int = 2) -> tuple[Path, dict]:
    errors: list[str] = []
    pack: dict = {}
    for _ in range(attempts):
        pack = _ask_llm(vertical_name, area_code, notes, errors)
        if isinstance(pack, dict):  # the requested identity is not the LLM's call
            pack.setdefault("meta", {})
            pack["meta"]["vertical"] = vertical_name
            pack["meta"]["area_code"] = str(area_code or "")
        errors = validate_pack(pack)
        if not errors:
            break
    if errors:
        raise ValueError("generated pack failed validation: " + "; ".join(errors))
    path = save_pack(pack, force=force)
    return path, pack


def main():
    ap = argparse.ArgumentParser(description="AI-write a domain sheet")
    ap.add_argument("--vertical", required=True, help="domain slug, e.g. hvac")
    ap.add_argument("--area", default="", help="service area code, e.g. 28203")
    ap.add_argument("--notes", default="", help="operator hints for the writer")
    ap.add_argument("--force", action="store_true", help="overwrite an existing sheet")
    args = ap.parse_args()
    if not OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY missing — fill in .env first.")

    path, pack = generate_pack(args.vertical, args.area, args.notes, force=args.force)
    print(f"wrote {path}")
    print(f"  {pack['meta']['display_name']}  (vertical={pack['meta']['vertical']}, "
          f"area={pack['meta']['area_code'] or '-'})")
    print(f"  {len(pack['estimator_questions'])} base questions, "
          f"{len(pack['benchmark']['job_types'])} job types")
    # prove it round-trips through the same loader the server uses
    load_pack(pack["meta"]["vertical"], pack["meta"]["area_code"])
    print("  loads + validates OK")


if __name__ == "__main__":
    main()
