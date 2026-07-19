import re

from .models import Business

PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s().-]*)?\(?([2-9]\d{2})\)?[\s.-]*"
    r"([2-9]\d{2})[\s.-]*(\d{4})(?!\d)"
)


def normalize_us_phone(value: str) -> str:
    """Return the first valid-looking US number as E.164."""
    match = PHONE.search(value or "")
    return "+1" + "".join(match.groups()) if match else ""


def _text_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def merge_businesses(rows: list[Business]) -> list[Business]:
    """Merge provider records. A phone number is the canonical identity."""
    merged: dict[str, Business] = {}
    for row in rows:
        row.phone = normalize_us_phone(row.phone)
        if not row.phone or not _text_key(row.name):
            continue
        if not row.sources:
            row.sources = [row.source]
        if row.source and row.source_id:
            row.source_ids[row.source] = row.source_id
        existing = merged.get(row.phone)
        if not existing:
            merged[row.phone] = row
            continue
        existing.sources = sorted(set(existing.sources + row.sources))
        existing.source_ids.update(row.source_ids)
        existing.categories = sorted(set(existing.categories + row.categories))
        for field_name in ("address", "city", "state", "url", "latitude", "longitude"):
            if not getattr(existing, field_name) and getattr(row, field_name):
                setattr(existing, field_name, getattr(row, field_name))
        if (row.review_count or 0) > (existing.review_count or 0):
            existing.review_count = row.review_count
        if (row.rating or 0) > (existing.rating or 0):
            existing.rating = row.rating
    return list(merged.values())
