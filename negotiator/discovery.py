"""Market discovery: where the call list would come from in the real world.
Tavily search -> business names/phones. In the demo the *callable* companies
are the simulated personas; this module proves the real-world pipeline."""
import re

from tavily import TavilyClient

from .config import TAVILY_API_KEY, vertical

PHONE_RE = re.compile(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")


def discover(city: str, state: str, limit: int = 8) -> list[dict]:
    noun = vertical()["meta"]["counterparty_noun"]
    client = TavilyClient(api_key=TAVILY_API_KEY)
    res = client.search(
        query=f"best {noun} companies in {city}, {state} phone number reviews",
        max_results=limit,
        include_answer=False,
    )
    out = []
    for r in res.get("results", []):
        phones = PHONE_RE.findall(r.get("content", ""))
        out.append({
            "name": r.get("title", "").split("|")[0].split("-")[0].strip()[:60],
            "url": r.get("url", ""),
            "phone": phones[0] if phones else "",
            "snippet": r.get("content", "")[:200],
        })
    return out
