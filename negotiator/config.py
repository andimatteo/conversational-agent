"""Environment + vertical pack loading. The vertical YAML is the single
source of truth for schema, benchmarks, red flags and negotiation levers."""
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
VERTICAL = os.getenv("VERTICAL", "moving")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Safe-by-default global switch.  In debug mode the scheduler keeps the real
# Google Places company identity, but it never dials a phone, opens an
# ElevenLabs conversation, or writes audio.  It only produces an explicitly
# labelled synthetic transcript and structured result.
DEBUG_CALLS = _env_bool("DEBUG_CALLS", True)

# Defense in depth for real businesses: disabling debug is not by itself an
# authorisation to dial the discovered market. The operator must explicitly
# enable vendor telephony as a second server-side deployment decision. The
# allow-listed human demo endpoint is separate and remains an explicit click.
LIVE_VENDOR_CALLS_ENABLED = _env_bool("LIVE_VENDOR_CALLS_ENABLED", False)

# The only destination accepted by the explicit live-demo endpoint.  Keeping
# this server-side prevents the authenticated UI from becoming an arbitrary
# outbound dialler.  The personal value belongs in .env, never in git.
DEMO_PHONE_NUMBER = os.getenv("DEMO_PHONE_NUMBER", "").strip()
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "").strip()
AGENT_TOOL_SECRET = os.getenv("AGENT_TOOL_SECRET", "").strip()

CALL_BATCH_TIMEOUT_SECS = int(os.getenv("CALL_BATCH_TIMEOUT_SECS", "900"))
CALL_POLL_INTERVAL_SECS = float(os.getenv("CALL_POLL_INTERVAL_SECS", "2"))
CALL_RUN_LEASE_SECS = int(os.getenv("CALL_RUN_LEASE_SECS", "2100"))
# Synthetic demo conversations are persisted turn-by-turn so the Calls panel
# can render genuine progressive state even though no counterparty phone/audio
# is created.  At the default pace a whole concurrent debug batch remains
# comfortably below one minute; tests explicitly set this to zero.
DEBUG_TRANSCRIPT_TURN_DELAY_SECS = min(
    2.0, max(0.0, float(os.getenv("DEBUG_TRANSCRIPT_TURN_DELAY_SECS", "0.25")))
)
# Product safety invariant: configuration may lower this, never raise it above
# the user's hard cap of two callbacks per vendor/job.
MAX_VENDOR_RECALLS = min(2, max(0, int(os.getenv("MAX_VENDOR_RECALLS", "2"))))

# Overridable so tests run on a throwaway dir instead of polluting the real
# DB (learned questions, jobs) with test artifacts.
DATA_DIR = Path(os.getenv("NEGOTIATOR_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "negotiator.db"
RECORDINGS_DIR = DATA_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


@lru_cache
def vertical() -> dict:
    """The process-default pack (VERTICAL env). Job-scoped code should prefer
    packs.load_pack(job['vertical'], job['area_code']) so one server can hold
    jobs from several domains/areas at once."""
    from .packs import load_pack  # local import: packs imports config for ROOT
    return load_pack(VERTICAL)


@lru_cache
def personas(vertical_name: str | None = None) -> list[dict]:
    """Counterparty personas for a domain: agents/personas/<vertical>.yaml.
    The three negotiation styles keep stable ids (stonewaller/lowballer/
    upseller) across domains; character, company and fees are per-domain."""
    path = ROOT / "agents" / "personas" / f"{vertical_name or VERTICAL}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)["personas"]


def registry_path() -> Path:
    return ROOT / "agents" / "registry.json"
