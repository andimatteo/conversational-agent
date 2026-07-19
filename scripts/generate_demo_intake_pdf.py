#!/usr/bin/env python3
"""Generate the deterministic PDF used by the live plumbing intake demo.

The repository intentionally does not need a PDF library for this one-page
fixture.  Keeping the tiny writer here makes the demo asset reproducible in a
fresh checkout and, because streams are uncompressed, easy to audit.
"""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "assets" / "demo" / "water_heater_intake.pdf"


SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "QUOTEWISE LIVE DEMO - CUSTOMER SCOPE",
        (
            "Residential water-heater replacement",
            "Prepared for comparable vendor estimates - not a vendor quote",
        ),
    ),
    (
        "PROPERTY AND EXISTING EQUIPMENT",
        (
            "Service location / ZIP: Charlotte, North Carolina 28202",
            "Property: detached house, approximately 28 years old",
            "Equipment: 40 US gallon natural-gas tank water heater, installed in 2012",
            "Location: ground-floor garage on a slab foundation; standard doorway",
            "Observed issue: slow leak at the tank base and intermittent hot water",
            "Water lines: copper; existing gas connection and metal vent are visible",
        ),
    ),
    (
        "REQUESTED SCOPE",
        (
            "Disconnect, drain, remove, and haul away the existing tank",
            "Supply and install a like-for-like 40 gallon natural-gas tank heater",
            "Reconnect existing water, gas, and vent lines; test for leaks and startup",
            "Include drain pan and drain connection; add expansion tank only if code requires it",
            "Show labor, equipment, materials, permit, disposal, and warranty separately",
            "No fuel conversion, equipment relocation, or after-hours work is requested",
        ),
    ),
    (
        "SITE AND ACCESS",
        (
            "Floor: 0 (garage); crawlspace: no; slab foundation: yes; tight access: no",
            "Driveway parking is available next to the garage; work area will be cleared",
            "Only the water heater is affected; no prior repair has been attempted",
            "Photos are not included with this scope document",
        ),
    ),
    (
        "STRUCTURED INTAKE FACTS",
        (
            "area_code: 28202",
            "job_type: water_heater",
            "problem_description: 40 gallon gas tank leaking at base; intermittent hot water",
            "property_type: house",
            "property_age_years: 28",
            "access: floor=0, crawlspace=false, slab_foundation=true, tight_access=false",
            "fixtures_affected: water heater - tank leak and intermittent hot water",
            "pipe_material: copper",
            "prior_repair_attempted: false",
            "photos_available: false",
        ),
    ),
    (
        "CONFIRM IN THE BRIEF VOICE INTAKE",
        (
            "1. Urgency: is service needed this week, or is scheduling flexible?",
            "2. Is the main water shutoff location known, accessible, and operational?",
            "3. Which normal-hours weekday access window works best?",
        ),
    ),
)


def _pdf_escape(text: str) -> str:
    """Escape an ASCII string for a PDF literal string."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text(x: int, y: int, value: str, *, size: int = 9, color: str = "0.12 0.15 0.20") -> str:
    return (
        f"BT /F1 {size} Tf {color} rg 1 0 0 1 {x} {y} Tm "
        f"({_pdf_escape(value)}) Tj ET"
    )


def _page_stream() -> bytes:
    commands = [
        "0.96 0.98 1.00 rg 0 0 612 792 re f",
        "0.08 0.25 0.45 rg 0 726 612 66 re f",
        _text(42, 758, SECTIONS[0][0], size=17, color="1 1 1"),
        _text(42, 739, SECTIONS[0][1][0], size=11, color="0.88 0.94 1"),
        _text(353, 739, SECTIONS[0][1][1], size=7, color="0.88 0.94 1"),
    ]

    y = 701
    for heading, lines in SECTIONS[1:]:
        commands.append(_text(42, y, heading, size=10, color="0.08 0.25 0.45"))
        commands.append(f"0.57 0.72 0.86 RG 0.7 w 42 {y - 5} m 570 {y - 5} l S")
        y -= 18
        for line in lines:
            commands.append(_text(50, y, line, size=8))
            y -= 13
        y -= 7

    commands.extend(
        [
            "0.89 0.94 0.98 rg 42 22 528 24 re f",
            _text(
                52,
                31,
                "Intake note: all listed facts may be reused verbatim across every vendor call.",
                size=8,
                color="0.08 0.25 0.45",
            ),
        ]
    )
    return ("\n".join(commands) + "\n").encode("ascii")


def build_pdf() -> bytes:
    stream = _page_stream()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        (
            b"<< /Title (QuoteWise Demo - Water Heater Intake) "
            b"/Subject (Customer-supplied plumbing scope for live demo) "
            b"/Creator (scripts/generate_demo_intake_pdf.py) >>"
        ),
    ]

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(build_pdf())
    print(f"Wrote {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
