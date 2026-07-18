"""Document intake: photos / existing quotes / inventory lists -> the SAME
structured job spec the voice interview produces. One schema, two doors."""
import base64
import json

import yaml
from openai import OpenAI

from .config import OPENAI_API_KEY, vertical

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _schema_prompt() -> str:
    v = vertical()
    return (
        f"You are an intake parser for a {v['meta']['display_name']} quote-comparison service.\n"
        "Extract a job spec from the user's document. Output ONLY JSON matching this schema "
        "(YAML description; omit nothing, use null for genuinely unknowable fields, apply stated defaults):\n\n"
        + yaml.safe_dump(v["spec_schema"])
        + "\nIf the document is an existing quote from a company, ALSO include a top-level key "
          "'existing_quote' with {company, total, line_items:[{label, amount}]} — it becomes "
          "negotiation leverage. Do not invent inventory that is not visible/stated."
    )


def parse_document(filename: str, content: bytes) -> dict:
    client = OpenAI(api_key=OPENAI_API_KEY)
    ext = "." + filename.rsplit(".", 1)[-1].lower()

    if ext in IMAGE_TYPES:
        user_content = [
            {"type": "text", "text": "Extract the job spec from this photo/document."},
            {"type": "image_url", "image_url": {
                "url": f"data:{IMAGE_TYPES[ext]};base64,{base64.b64encode(content).decode()}"}},
        ]
    else:  # .txt / .md / pasted PDFs-as-text
        user_content = [{"type": "text", "text": "Extract the job spec from this document:\n\n"
                         + content.decode("utf-8", errors="replace")[:20000]}]

    resp = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _schema_prompt()},
                  {"role": "user", "content": user_content}],
    )
    spec = json.loads(resp.choices[0].message.content)
    spec["vertical"] = vertical()["meta"]["vertical"]
    return spec
