"""Offline tests for the vertical-pack job-spec validator.

Run with: ``.venv/bin/python -m tests.spec_validation_test``
"""

from copy import deepcopy

from negotiator.packs import load_pack
from negotiator.spec_validation import sanitize_extracted, validate_spec


MOVING_SPEC = {
    "origin": {"city": "Rock Hill", "state": "SC", "floor": 1,
               "elevator": False, "stairs_flights": 0, "parking_distance_ft": 25},
    "destination": {"city": "Charlotte", "state": "NC", "floor": 2,
                    "elevator": True, "stairs_flights": 0, "parking_distance_ft": 50},
    "distance_miles": 45,
    "move_date": "2026-08-15",
    "date_flexible": False,
    "home_size": "2BR",
    "inventory": [{"item": "sofa", "qty": 1, "special": "oversized"}],
    "boxes_estimate": 0,
    "services": {"packing": False, "disassembly": True, "storage": False},
    "notes": "Call before arrival",
}


def _contains(errors: list[str], fragment: str) -> bool:
    return any(fragment in error for error in errors)


def main():
    moving = load_pack("moving")
    plumbing = load_pack("plumbing")

    # Complete specs accept valid zero/False values and supported system fields.
    spec = {**deepcopy(MOVING_SPEC),
            "existing_quote": {"company": "Written Quote LLC", "total": 1574},
            "existing_quotes": [{"company": "Second Quote LLC", "total": 1800}]}
    assert validate_spec(spec, moving) == []

    # Required, top-level, scalar, date and enum errors are all reported together.
    bad = deepcopy(MOVING_SPEC)
    bad.pop("origin")
    bad.update({"distance_miles": True, "move_date": "2026-02-30",
                "home_size": "palace", "rogue": "do not persist"})
    errors = validate_spec(bad, moving)
    assert _contains(errors, "origin: required")
    assert _contains(errors, "rogue: unknown top-level")
    assert _contains(errors, "distance_miles: expected number")
    assert _contains(errors, "move_date: expected date")
    assert _contains(errors, "home_size: expected one of")

    # Nested object/list fields are strict, including bool-vs-int and enums.
    bad_nested = deepcopy(MOVING_SPEC)
    bad_nested["origin"]["floor"] = False
    bad_nested["origin"]["mystery"] = 1
    bad_nested["inventory"] = [
        {"item": "piano", "qty": "one", "special": "priceless", "extra": True},
        "not an object",
    ]
    errors = validate_spec(bad_nested, moving)
    for fragment in ("origin.floor: expected int", "origin.mystery: unknown field",
                     "inventory[0].qty: expected int", "inventory[0].special: expected one of",
                     "inventory[0].extra: unknown field", "inventory[1]: expected object"):
        assert _contains(errors, fragment), (fragment, errors)

    # A partial extraction is sanitized without producing missing-required errors.
    extracted = {
        "area_code": "28202",
        "job_type": "water_heater",
        "problem_description": 42,
        "property_type": "castle",
        "property_age_years": True,
        "urgency": "this_week",
        "access": {"floor": 2, "crawlspace": "no", "slab_foundation": False,
                   "mystery_access": "drop me"},
        "fixtures_affected": [
            {"fixture": "water heater", "issue": "leaking", "extra": "drop me"},
            "bad item",
            {"fixture": "sink", "issue": 123},
        ],
        "existing_quote": {"company": "FastFlow", "total": 2350},
        "existing_quotes": [{"company": "Budget Rooter", "total": 1100}, "bad quote"],
        "notes": "Visible rust in photo",
        "insights": ["not an authorized spec key"],
        "rogue": "drop me",
    }
    untouched = deepcopy(extracted)
    clean, errors = sanitize_extracted(extracted, plumbing)
    assert extracted == untouched, "sanitization must not mutate model/parser output"
    assert clean == {
        "area_code": "28202",
        "job_type": "water_heater",
        "urgency": "this_week",
        "access": {"floor": 2, "slab_foundation": False},
        "fixtures_affected": [
            {"fixture": "water heater", "issue": "leaking"},
            {"fixture": "sink"},
        ],
        "existing_quote": {"company": "FastFlow", "total": 2350},
        "existing_quotes": [{"company": "Budget Rooter", "total": 1100}],
        "notes": "Visible rust in photo",
    }
    for fragment in ("problem_description: expected str", "property_type: expected one of",
                     "property_age_years: expected int", "access.crawlspace: expected bool",
                     "access.mystery_access: unknown field", "fixtures_affected[0].extra: unknown field",
                     "fixtures_affected[1]: expected object", "fixtures_affected[2].issue: expected str",
                     "existing_quotes[1]: expected object", "insights: unknown top-level",
                     "rogue: unknown top-level"):
        assert _contains(errors, fragment), (fragment, errors)
    assert not _contains(errors, "required field"), errors

    # Wrong root/system shapes and malformed schema inputs fail safely.
    clean, errors = sanitize_extracted(["not", "an", "object"], plumbing)
    assert clean == {} and _contains(errors, "extracted: expected object")
    errors = validate_spec({**MOVING_SPEC, "existing_quote": "not an object"}, moving)
    assert _contains(errors, "existing_quote: expected object")
    assert validate_spec({}, {"spec_schema": {"required": [], "fields": []}}) == [
        "pack.spec_schema.fields: expected object"
    ]

    print("SPEC VALIDATION TEST PASSED")


if __name__ == "__main__":
    main()
