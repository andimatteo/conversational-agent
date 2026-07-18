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
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
VERTICAL = os.getenv("VERTICAL", "moving")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "negotiator.db"
RECORDINGS_DIR = DATA_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)


@lru_cache
def vertical() -> dict:
    """The process-default pack (VERTICAL env). Job-scoped code should prefer
    packs.load_pack(job['vertical'], job['area_code']) so one server can hold
    jobs from several domains/areas at once."""
    from .packs import load_pack  # local import: packs imports config for ROOT
    return load_pack(VERTICAL)


@lru_cache
def personas() -> list[dict]:
    with open(ROOT / "agents" / "personas.yaml") as f:
        return yaml.safe_load(f)["personas"]


def registry_path() -> Path:
    return ROOT / "agents" / "registry.json"
