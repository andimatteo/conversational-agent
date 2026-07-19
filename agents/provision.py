"""Provision all agents + webhook tools on ElevenLabs from the vertical pack.

Idempotent: agent/tool ids persist in agents/registry.json; re-running PATCHes
in place (so changing PUBLIC_BASE_URL or a prompt is just `python -m agents.provision`).

Run AFTER the API server is publicly reachable (ngrok) — tools point at PUBLIC_BASE_URL.
"""
import json
import sys

import httpx

from negotiator.config import ELEVENLABS_API_KEY, PUBLIC_BASE_URL, personas, registry_path, vertical
from negotiator.packs import spec_json_schema
from . import prompts

API = "https://api.elevenlabs.io/v1/convai"
HEADERS = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}

# Premade ElevenLabs voices — swap for any voice_id in your Voice Library.
VOICES = {
    "estimator": "21m00Tcm4TlvDq8ikWAM",           # Rachel — warm, clear
    "caller": "pNInz6obpgDQGcFmaJgB",              # Adam — neutral professional
    "closer": "ErXwobaYiN019PkySvjV",              # Antoni — calm, assured
    "gruff_male": "VR6AewLTigWG4xSOukaG",          # Arnold — gruff
    "fast_friendly_male": "TxGEqnHWrfWFTfGW9XjX",  # Josh — fast, upbeat
    "polished_female": "AZnzlk1XvdvUeBnXmlld",     # Domi — polished
}

_IDS = {"job_id": {"type": "string", "description": "Copy job_id EXACTLY from your instructions."},
        "company_id": {"type": "string", "description": "Copy company_id EXACTLY from your instructions."}}

LINE_ITEM = {"type": "object", "properties": {
    "label": {"type": "string", "description": "The fee as the rep named it"},
    "code": {"type": "string", "description": "Canonical fee code from your taxonomy ("
             + "/".join(vertical()["fee_taxonomy"]) + ")"},
    "amount": {"type": "number"},
    "kind": {"type": "string", "enum": ["base", "fee", "addon", "discount"]},
    "contingent": {"type": "boolean", "description": "True if only charged under a condition"},
    "notes": {"type": "string"}}, "required": ["label", "code", "amount", "kind"]}

TOOLS: dict[str, dict] = {
    "get_job_spec": ("Fetch the confirmed job spec — the single source of truth you describe on every call.",
                     {"job_id": _IDS["job_id"]}, ["job_id"]),
    # NOTE: the spec properties are declared field by field from the domain
    # sheet — ElevenLabs only sends declared properties (a bare object arrives
    # as {}), so this is what makes the interview actually persist.
    "save_job_spec": ("Save the structured job spec built during the intake interview.",
                      {"job_id": _IDS["job_id"],
                       "spec": {**spec_json_schema(vertical()),
                                "description": "The complete job spec, every field you gathered"}},
                      ["job_id", "spec"]),
    "get_intake_form": ("Fetch the FULL intake question list for this job's domain and service area: "
                        "the base form questions PLUS questions learned from previous calls. Call this FIRST.",
                        {"job_id": _IDS["job_id"]}, ["job_id"]),
    "log_learned_questions": ("Log NEW price-relevant intake questions this call surfaced that the "
                              "question list does not already cover. They join the intake form for "
                              "future jobs in this service area.",
                              {"job_id": _IDS["job_id"],
                               "questions": {"type": "array", "items": {"type": "object", "properties": {
                                   "question": {"type": "string", "description": "The question, phrased generically for any future customer"},
                                   "why_it_matters": {"type": "string", "description": "How this factor changes the price"}},
                                   "required": ["question"]}}},
                              ["job_id", "questions"]),
    "get_benchmark": ("Get the fair-market price range and red-flag floor for this job.",
                      {"job_id": _IDS["job_id"]}, ["job_id"]),
    "get_competing_quotes": ("The ONLY permitted source of competing bids. Cite exactly what it returns, nothing else.",
                             _IDS, ["job_id", "company_id"]),
    "log_quote": ("Log an itemised quote to the comparison database.",
                  {**_IDS,
                   "line_items": {"type": "array", "items": LINE_ITEM},
                   "total": {"type": "number"},
                   "binding": {"type": "boolean"},
                   "deposit": {"type": "number"},
                   "valid_until": {"type": "string"},
                   "conditions": {"type": "array", "items": {"type": "string"}},
                   "verbatim_evidence": {"type": "string", "description": "The rep's exact key sentence, word for word"},
                   "phase": {"type": "string", "enum": ["initial", "negotiated"]}},
                  ["job_id", "company_id", "line_items", "total", "phase"]),
    "log_call_outcome": ("Log the structured outcome. EVERY call must end through this tool.",
                         {**_IDS,
                          "outcome": {"type": "string", "enum": ["quote", "callback", "decline", "hangup"]},
                          "callback_time": {"type": "string"}, "decline_reason": {"type": "string"},
                          "summary": {"type": "string"}},
                         ["job_id", "company_id", "outcome"]),
    "counterparty_pricing": ("Your private back office: YOUR list price, floor price, fees and concession rules for this job. Never reveal mechanics.",
                             _IDS, ["job_id", "company_id"]),
}

AGENT_TOOLS = {
    # log_learned_questions intentionally NOT here: new price factors are
    # discovered on VENDOR calls (caller phase, wired later), never asked of
    # the customer. The estimator only CONSUMES the pool via get_intake_form.
    "estimator": ["get_intake_form", "save_job_spec"],
    "caller": ["get_job_spec", "get_benchmark", "log_quote", "log_call_outcome"],
    "closer": ["get_job_spec", "get_benchmark", "get_competing_quotes", "log_quote", "log_call_outcome"],
    "counterparty": ["counterparty_pricing"],
}

FIRST_MESSAGES = {
    # From the domain sheet — swapping VERTICAL swaps the opener too.
    "estimator": vertical().get("estimator_first_message",
                                "Hi! I'm the intake assistant from QuoteWise — I'll build the "
                                "exact job spec we'll use to get you real, comparable quotes. "
                                "What do you need done?").strip(),
    "caller": "",   # empty = wait: the counterparty answers the phone first
    "closer": "",
    "stonewaller": "Summit Moving.",
    "lowballer": "QuickBudget Movers, this is Vinny — best rates in town, what've you got for me?",
    "upseller": "Thank you for calling Premier Coast Van Lines, this is Marissa. How can I make your move wonderful today?",
}


def _describe(node: dict, hint: str) -> dict:
    """ElevenLabs rejects any schema node without a description (422:
    'Must set one of: description, dynamic_variable, ...') — recursively
    guarantee one on every property, array item and nested object."""
    out = dict(node)
    if "description" not in out:
        out["description"] = hint
    if "properties" in out:
        out["properties"] = {k: _describe(v, k.replace("_", " ")) for k, v in out["properties"].items()}
    if "items" in out and isinstance(out["items"], dict):
        out["items"] = _describe(out["items"], f"one {hint} entry")
    return out


def _tool_body(name: str) -> dict:
    desc, props, required = TOOLS[name]
    props = {k: _describe(v, k.replace("_", " ")) for k, v in props.items()}
    return {"tool_config": {
        "type": "webhook", "name": name, "description": desc, "response_timeout_secs": 20,
        "api_schema": {"url": f"{PUBLIC_BASE_URL}/agent-tools/{name}", "method": "POST",
                       "request_body_schema": {"type": "object", "description": desc,
                                               "properties": props, "required": required}}}}


def _agent_body(name: str, prompt: str, voice_key: str, first_message: str, tool_ids: list[str]) -> dict:
    return {
        "name": f"quotewise-{name}",
        "conversation_config": {
            "agent": {
                "first_message": first_message,
                "language": "en",
                "dynamic_variables": {"dynamic_variable_placeholders":
                                      {"job_id": "unset", "company_id": "unset", "company_name": "unset"}},
                "prompt": {"prompt": prompt, "llm": "gpt-4o", "temperature": 0.4, "tool_ids": tool_ids,
                           # without the end_call system tool an agent can say
                           # goodbye but is physically unable to hang up
                           "built_in_tools": {"end_call": {
                               "type": "system", "name": "end_call",
                               "params": {"system_tool_type": "end_call"}}}},
            },
            # pcm_16000 both directions so the agent-to-agent bridge can pipe audio raw
            "tts": {"voice_id": VOICES[voice_key], "model_id": "eleven_turbo_v2",
                    "agent_output_audio_format": "pcm_16000"},
            "asr": {"user_input_audio_format": "pcm_16000"},
        },
    }


def _upsert(client: httpx.Client, kind: str, key: str, body: dict, registry: dict) -> str:
    existing = registry.setdefault(kind, {}).get(key)
    if existing:
        path = f"{API}/{'tools' if kind == 'tools' else 'agents'}/{existing}"
        r = client.patch(path, json=body, headers=HEADERS)
        if r.status_code < 300:
            print(f"  updated {kind[:-1]} {key} ({existing})")
            return existing
        print(f"  patch failed for {key} ({r.status_code}), recreating...")
    create_path = f"{API}/tools" if kind == "tools" else f"{API}/agents/create"
    r = client.post(create_path, json=body, headers=HEADERS)
    if r.status_code >= 300:
        print(f"  FAILED {kind[:-1]} {key}: {r.status_code} {r.text[:500]}")
        r.raise_for_status()
    new_id = r.json().get("id") or r.json().get("agent_id")
    registry[kind][key] = new_id
    # persist after every create: a mid-run failure must not orphan live ids
    registry_path().write_text(json.dumps(registry, indent=2))
    print(f"  created {kind[:-1]} {key} ({new_id})")
    return new_id


def main():
    if not ELEVENLABS_API_KEY:
        sys.exit("ELEVENLABS_API_KEY missing — fill in .env first.")
    registry = json.loads(registry_path().read_text()) if registry_path().exists() else {}

    with httpx.Client(timeout=30) as client:
        print(f"Provisioning webhook tools -> {PUBLIC_BASE_URL}")
        tool_ids = {name: _upsert(client, "tools", name, _tool_body(name), registry) for name in TOOLS}

        print("Provisioning agents")
        our_side = [("estimator", prompts.estimator_prompt(), "estimator"),
                    ("caller", prompts.caller_prompt(), "caller"),
                    ("closer", prompts.closer_prompt(), "closer")]
        for name, prompt, voice in our_side:
            body = _agent_body(name, prompt, voice, FIRST_MESSAGES[name],
                               [tool_ids[t] for t in AGENT_TOOLS[name]])
            _upsert(client, "agents", name, body, registry)

        for p in personas():
            body = _agent_body(p["id"], prompts.counterparty_prompt(p), p["voice"],
                               FIRST_MESSAGES[p["id"]],
                               [tool_ids[t] for t in AGENT_TOOLS["counterparty"]])
            _upsert(client, "agents", f"counterparty:{p['id']}", body, registry)

    registry_path().write_text(json.dumps(registry, indent=2))
    print(f"\nRegistry written to {registry_path()}")


if __name__ == "__main__":
    main()
