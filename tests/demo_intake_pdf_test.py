"""Offline contract test for the live-demo PDF and document parser.

It regenerates the tracked fixture, validates its PDF cross-reference table and
human-readable text, then replaces the OpenAI client with an in-memory capture
to verify the exact live-tested Chat Completions FileContentPart. No network is used.

  .venv/bin/python -m tests.demo_intake_pdf_test
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace

import negotiator.docparse as docparse
from negotiator.packs import load_pack
from negotiator.spec_validation import validate_spec


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "assets" / "demo" / "water_heater_intake.pdf"
GENERATOR = ROOT / "scripts" / "generate_demo_intake_pdf.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_demo_intake_pdf", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_xref(pdf: bytes) -> None:
    assert pdf.startswith(b"%PDF-1.4\n") and pdf.rstrip().endswith(b"%%EOF")
    match = re.search(rb"startxref\s+(\d+)\s+%%EOF", pdf)
    assert match, "missing startxref"
    xref_at = int(match.group(1))
    assert pdf[xref_at:xref_at + 4] == b"xref", "startxref points to the wrong location"
    block = pdf[xref_at:].split(b"trailer", 1)[0].splitlines()
    count = int(block[1].split()[1])
    entries = block[2:2 + count]
    assert len(entries) == count and entries[0].endswith(b"65535 f ")
    for object_number, entry in enumerate(entries[1:], start=1):
        offset = int(entry[:10])
        assert pdf[offset:].startswith(f"{object_number} 0 obj\n".encode())


def _literal_text(pdf: bytes) -> str:
    """Extract the uncompressed literal strings emitted by our tiny writer."""
    strings = re.findall(rb"\((.*?(?<!\\)(?:\\\\)*)\) Tj", pdf)
    decoded = []
    for value in strings:
        text = value.decode("ascii")
        text = text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
        decoded.append(text)
    return "\n".join(decoded)


def main() -> None:
    generator = _load_generator()
    generated = generator.build_pdf()
    tracked = ASSET.read_bytes()
    assert generated == tracked, "tracked PDF is stale; rerun scripts/generate_demo_intake_pdf.py"

    _validate_xref(tracked)
    text = _literal_text(tracked)
    for expected in (
        "Residential water-heater replacement",
        "area_code: 28202",
        "job_type: water_heater",
        "slab_foundation=true",
        "Urgency: is service needed this week",
        "main water shutoff location known",
        "not a vendor quote",
    ):
        assert expected in text, expected
    text_lines = [line.strip().lower() for line in text.splitlines()]
    assert not any(line.startswith("urgency:") for line in text_lines), \
        "the structured facts must leave urgency unanswered"
    assert not any(line.startswith("water_shutoff_known:") for line in text_lines), \
        "the structured facts must leave shutoff knowledge unanswered"

    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"area_code":"28202","job_type":"water_heater",'
                '"problem_description":"40 gallon gas tank leaking at base",'
                '"property_type":"house",'
                '"access":{"floor":0,"crawlspace":false,'
                '"slab_foundation":true,"tight_access":false}}'
            )))])

    class FakeOpenAI:
        def __init__(self, *, api_key):
            assert api_key == "offline-contract-test"
            self.chat = SimpleNamespace(completions=FakeCompletions())

    original_client = docparse.OpenAI
    original_key = docparse.OPENAI_API_KEY
    try:
        docparse.OpenAI = FakeOpenAI
        docparse.OPENAI_API_KEY = "offline-contract-test"
        result = docparse.parse_document(
            ASSET.name, tracked, load_pack("plumbing", "28202"), current_spec={}
        )
    finally:
        docparse.OpenAI = original_client
        docparse.OPENAI_API_KEY = original_key

    assert result["job_type"] == "water_heater" and "urgency" not in result
    pack = load_pack("plumbing", "28202")
    completed_after_brief_call = {
        **result,
        "urgency": "this_week",
        "water_shutoff_known": True,
        "notes": "Normal-hours weekday access works for the homeowner.",
    }
    assert validate_spec(completed_after_brief_call, pack) == [], \
        "the short voice follow-up must be enough to make the shared spec confirmable"
    messages = captured["messages"]
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    parts = messages[1]["content"]
    file_parts = [part for part in parts if part.get("type") == "file"]
    assert len(file_parts) == 1
    assert file_parts[0] == {
        "type": "file",
        "file": {
            "filename": ASSET.name,
            "file_data": (
                "data:application/pdf;base64,"
                + base64.b64encode(tracked).decode("ascii")
            ),
        },
    }
    assert file_parts[0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert captured["response_format"] == {"type": "json_object"}
    print("DEMO INTAKE PDF TEST PASSED: valid PDF, expected scope, live file-data contract")


if __name__ == "__main__":
    main()
