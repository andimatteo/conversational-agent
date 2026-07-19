"""Document intake: photos, PDFs and text files -> extra structured data for
the SAME job spec the voice interview fills. One schema, many doors.

A document can be another company's quote (becomes negotiation leverage), a
system/equipment spec sheet, or a photo of the problem. Whatever the call
didn't capture, a document can add; on conflicts the interview answer wins
(merge policy lives in server._merge_document).
"""
import base64
import json

import yaml
from fastapi import HTTPException
from openai import OpenAI

from .config import OPENAI_API_KEY

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}


def _system_prompt(pack: dict, current_spec: dict) -> str:
    return (
        f"You are an intake parser for a {pack['meta']['display_name']} quote-comparison service.\n"
        "Extract structured data about the JOB from the user's document. Output ONLY JSON with:\n"
        "1. any fields from this job spec schema you can GENUINELY read in the document "
        "(YAML description; omit fields the document says nothing about):\n\n"
        + yaml.safe_dump(pack["spec_schema"], sort_keys=False)
        + "\n2. 'existing_quote': {company, total, line_items:[{label, amount}]} ONLY if the "
          "document is a price quote/estimate from a company — it becomes negotiation leverage.\n"
        "3. 'insights': a list of short strings — other price-relevant facts that don't fit the "
        "schema (equipment model/age, access constraints, prior repairs, warranty terms).\n\n"
        "Data already gathered on the intake call is below. COMPLEMENT it with what the "
        "document adds — and when the document clearly shows a DIFFERENT or more precise "
        "value for a field already on file (a written quote, spec sheet or photo beats a "
        "verbal guess), include the corrected value: every change is shown to the user for "
        "review before any company is called. Omit fields where the document agrees with "
        "or says nothing about the current value:\n"
        + json.dumps(current_spec)[:4000]
        + "\nNever invent anything not visible/stated in the document."
    )


def parse_document(filename: str, content: bytes, pack: dict, current_spec: dict) -> dict:
    """Returns extracted spec fields (+ optional existing_quote, insights)."""
    if not OPENAI_API_KEY:
        raise HTTPException(503, "OPENAI_API_KEY missing — document parsing is disabled.")
    client = OpenAI(api_key=OPENAI_API_KEY)
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in IMAGE_TYPES:
        user_content = [
            {"type": "text", "text": "Extract job data from this photo/document."},
            {"type": "image_url", "image_url": {
                "url": f"data:{IMAGE_TYPES[ext]};base64,{base64.b64encode(content).decode()}"}},
        ]
    elif ext == ".pdf":
        user_content = [
            {"type": "text", "text": "Extract job data from this PDF (it may be a quote, "
                                     "an equipment/system spec sheet, or an inspection report)."},
            {"type": "file", "file": {
                "filename": filename,
                "file_data": f"data:application/pdf;base64,{base64.b64encode(content).decode()}"}},
        ]
    elif ext in TEXT_TYPES or not ext:
        user_content = [{"type": "text", "text": "Extract job data from this document:\n\n"
                         + content.decode("utf-8", errors="replace")[:20000]}]
    else:
        raise HTTPException(415, f"Unsupported file type '{ext}'. "
                                 f"Accepted: pdf, {', '.join(sorted(IMAGE_TYPES))}, txt/md.")

    resp = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _system_prompt(pack, current_spec)},
                  {"role": "user", "content": user_content}],
    )
    data = json.loads(resp.choices[0].message.content)
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}
