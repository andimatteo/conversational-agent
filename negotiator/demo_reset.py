"""Prepare a fresh, auditable live-demo job without erasing history.

Usage::

    python -m negotiator.demo_reset
    python -m negotiator.demo_reset --live-vendor "Acme Plumbing"
    python -m negotiator.demo_reset --wipe-learnings

Every reset creates a new unconfirmed demo job. Previous demo jobs are archived
logically; their calls, quotes, batches, run claims, recall reservations,
recordings and uploads remain untouched. This is important both for evidence
and for ensuring a reset is never a hidden destructive retry mechanism.

The new job contains no cached market. After the user reviews the spec, the
launch endpoint calls Google Places live, promotes every callable result and
selects one identity for the human role-play. This module never reads a phone
destination from CLI input and never starts discovery or telephony.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone

from . import db
from .config import DEBUG_CALLS, DEMO_PHONE_NUMBER, ELEVENLABS_PHONE_NUMBER_ID
from .models import Job
from .packs import load_pack
from .seed import DEMO_EMAIL, demo_user


TEMPLATE_ID = "plumbing-water-heater-charlotte-v1"
DEMO_VERTICAL = "plumbing"
DEMO_AREA_CODE = "28202"
DISCOVERY_STATE = "North Carolina"
DISCOVERY_TARGET = 25

SYNTHETIC_LABEL = (
    "Synthetic transcript only — the Google business was not contacted and no audio exists."
)
LIVE_ROLEPLAY_LABEL = (
    "Live human role-play through the allow-listed demo phone — the role-player is not "
    "the selected Google business."
)

DEMO_SPEC = {
    "vertical": DEMO_VERTICAL,
    "area_code": DEMO_AREA_CODE,
    "job_type": "water_heater",
    "problem_description": (
        "40-gal natural-gas tank water heater leaking from the tank base "
        "with intermittent hot water"
    ),
    "property_type": "house",
    "property_age_years": 28,
    "urgency": "this_week",
    "water_shutoff_known": True,
    "access": {
        "floor": 0,
        "crawlspace": False,
        "slab_foundation": True,
        "tight_access": False,
    },
    "fixtures_affected": [{"fixture": "water heater", "issue": "leaking"}],
    "pipe_material": "copper",
    "prior_repair_attempted": False,
    "photos_available": False,
    "notes": "Weekday appointments are preferred.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _demo_jobs(user_id: str) -> list[dict]:
    """Newest-first jobs produced by this demo reset command."""
    rows = [
        job for job in db.where("jobs")
        if job.get("user_id") == user_id
        and (job.get("spec_source") == "demo" or job.get("demo_mode"))
    ]
    return sorted(rows, key=lambda job: job.get("created_at", ""), reverse=True)


def _saved_call_list(user_id: str) -> tuple[dict | None, str]:
    """Return the newest reusable saved market scan and its source job id."""
    jobs = sorted(
        (job for job in db.where("jobs") if job.get("user_id") == user_id),
        key=lambda job: job.get("created_at", ""),
        reverse=True,
    )
    for job in jobs:
        call_list = job.get("call_list")
        if isinstance(call_list, dict) and call_list.get("items"):
            return deepcopy(call_list), job.get("id", "")
    return None, ""


def _discover_call_list(pack: dict) -> dict:
    from market_discovery.service import DiscoveryService

    result = DiscoveryService().discover(
        pack["meta"]["counterparty_noun"], DISCOVERY_STATE, DISCOVERY_TARGET
    )
    result["saved"] = bool(result.get("items"))
    return result


def _google_key(company: dict) -> tuple[str, str, str]:
    """Stable selection order that does not depend on generated company ids."""
    return (
        str(company.get("name", "")).casefold(),
        str(company.get("external_ids", {}).get("google_places", "")).casefold(),
        str(company.get("phone", "")),
    )


def _select_live_vendor(companies: list[dict], query: str = "") -> dict:
    """Select one real Google identity, optionally by a human-friendly query.

    Exact name/id/phone/place-id matches win. A partial name/address match is
    accepted only when unambiguous, avoiding a surprising live role-play
    identity when two discovered businesses have similar names.
    """
    candidates = sorted(
        (
            company for company in companies
            if company.get("source") == "google_places"
            and company.get("phone")
            and company.get("external_ids", {}).get("google_places")
        ),
        key=_google_key,
    )
    if not candidates:
        raise LookupError(
            "No callable Google Places vendor is available from the fresh launch discovery."
        )
    needle = (query or "").strip().casefold()
    if not needle:
        return candidates[0]

    def exact_values(company: dict) -> set[str]:
        return {
            str(company.get("id", "")).casefold(),
            str(company.get("name", "")).casefold(),
            str(company.get("phone", "")).casefold(),
            str(company.get("external_ids", {}).get("google_places", "")).casefold(),
        }

    exact = [company for company in candidates if needle in exact_values(company)]
    if exact:
        return exact[0]

    partial = [
        company for company in candidates
        if needle in str(company.get("name", "")).casefold()
        or needle in str(company.get("address", "")).casefold()
    ]
    if not partial:
        available = ", ".join(company["name"] for company in candidates[:8])
        raise LookupError(
            f"No Google vendor matches {query!r}. Available examples: {available}"
        )
    if len(partial) > 1:
        matches = ", ".join(company["name"] for company in partial[:8])
        raise LookupError(
            f"Live-vendor query {query!r} is ambiguous. Use a full name; matches: {matches}"
        )
    return partial[0]


def _active_work(job_id: str) -> bool:
    """Fail closed rather than archive a job whose outbound state can change."""
    calls = db.where("calls", job_id=job_id)
    if any(
        call.get("status") in {"queued", "calling"}
        or (call.get("started_at") and not call.get("ended_at"))
        or (call.get("external_state_uncertain")
            and not call.get("external_state_resolved_at"))
        for call in calls
    ):
        return True
    runs = db.where("call_runs", job_id=job_id)
    if any(run.get("status") in {"queued", "running"} for run in runs):
        return True

    # The durable claim is the authoritative cross-process lock. Instantiating
    # this store is safe even before any run has created its schema.
    from .runclaims import RunClaimStore

    return RunClaimStore().active_for_job(job_id) is not None


def _archive_previous(jobs: list[dict], new_job_id: str, archived_at: str) -> list[str]:
    archived = []
    for job in jobs:
        if job.get("id") == new_job_id or job.get("archived"):
            continue
        job["archived"] = True
        job["archived_at"] = archived_at
        job["archive_reason"] = "superseded_by_demo_reset"
        job["superseded_by_job_id"] = new_job_id
        if isinstance(job.get("demo_mode"), dict):
            job["demo_mode"] = {
                **job["demo_mode"],
                "active": False,
                "archived_at": archived_at,
                "superseded_by_job_id": new_job_id,
            }
        db.put("jobs", job["id"], job)
        archived.append(job["id"])
    return archived


def _wipe_learnings() -> int:
    """The one intentionally destructive option; never implied by reset."""
    with db.conn() as connection:
        cursor = connection.execute(
            "DELETE FROM learned_questions WHERE vertical=? AND area_code=?",
            (DEMO_VERTICAL, DEMO_AREA_CODE),
        )
        return max(0, int(cursor.rowcount))


def reset(
    rediscover: bool = False,
    wipe_learnings: bool = False,
    live_vendor: str = "",
) -> dict:
    user = demo_user()
    pack = load_pack(DEMO_VERTICAL, DEMO_AREA_CODE)
    old_demo_jobs = _demo_jobs(user["id"])

    active = [job["id"] for job in old_demo_jobs if not job.get("archived")
              and _active_work(job["id"])]
    if active:
        raise RuntimeError(
            "Cannot reset while a previous demo still has active or leased calls: "
            + ", ".join(active)
        )

    from .spec_validation import validate_spec

    errors = validate_spec(DEMO_SPEC, pack)
    if errors:
        raise RuntimeError(f"Demo spec no longer matches the plumbing pack: {errors}")

    prepared_at = _now()
    session_id = db.new_id("demo")
    job = Job(
        id=db.new_id("job"),
        vertical=DEMO_VERTICAL,
        area_code=DEMO_AREA_CODE,
        user_id=user["id"],
        spec_source="demo",
        # Intake must be demonstrated end-to-end: the PDF populates most of
        # this initially empty scope, then browser voice fills the remaining
        # fields before explicit user review and confirmation. DEMO_SPEC above
        # is the validated template contract, never hidden prefill.
        spec={},
        confirmed=False,
    )
    record = job.model_dump()
    record.update({
        "knowledge_version": 0,
        "follow_up_plan": [],
        "archived": False,
        "demo_mode": {
            "active": False,
            "roleplay": True,
            "session_id": session_id,
            "status": "preparing",
            "template": {
                "id": TEMPLATE_ID,
                "vertical": DEMO_VERTICAL,
                "area_code": DEMO_AREA_CODE,
                "prepared_at": prepared_at,
                "call_list_source": "fresh_google_places_at_launch",
                "source_job_id": "",
            },
        },
    })
    db.put("jobs", job.id, record)

    record = db.get("jobs", job.id) or record
    record["demo_mode"] = {
        **record["demo_mode"],
        "active": True,
        "status": "awaiting_intake_and_confirmation",
        "roleplay": True,
        "auto_negotiate": True,
        "workflow_stage": "awaiting_documents_voice_and_confirmation",
        "live_company_id": "",
        "live_company_name": "Pending fresh Google Places discovery",
        "live_company_google_place_id": "",
        "selection_query": (live_vendor or "").strip(),
        "selection_strategy": "query" if (live_vendor or "").strip() else "deterministic_first",
        "discovery": {
            "provider": "google_places",
            "state": DISCOVERY_STATE,
            "query": pack["meta"]["counterparty_noun"],
            "target": DISCOVERY_TARGET,
            "required_at_launch": True,
            "status": "pending_user_review",
        },
        "labels": {
            "synthetic": SYNTHETIC_LABEL,
            "live": LIVE_ROLEPLAY_LABEL,
        },
        "truthful_description": (
            "A fresh Google Places API call after review supplies the market identities. "
            "The selected identity is represented "
            "by an authorised human role-player at the configured demo phone. Other rows are "
            "synthetic transcript-only while DEBUG_CALLS=true; never describe those businesses "
            "as contacted."
        ),
    }
    db.put("jobs", job.id, record)

    # Archive only after the replacement is fully prepared. No related row or
    # artifact is deleted, and recall/run claim tables are deliberately untouched.
    archived = _archive_previous(old_demo_jobs, job.id, _now())
    wiped = _wipe_learnings() if wipe_learnings else 0

    return {
        "job_id": job.id,
        "archived_previous": archived,
        # Retain the old response key for scripts while making its non-destructive
        # meaning explicit in the new key above.
        "deleted_previous": [],
        "vendors": 0,
        "call_list_items": 0,
        "call_list_source": "pending_fresh_google_places_at_launch",
        "live_company_id": "",
        "live_company_name": "Pending fresh Google Places discovery",
        "live_label": LIVE_ROLEPLAY_LABEL,
        "synthetic_label": SYNTHETIC_LABEL,
        "debug_calls": DEBUG_CALLS,
        "demo_phone_ready": bool(DEMO_PHONE_NUMBER and ELEVENLABS_PHONE_NUMBER_ID),
        "auto_negotiate": True,
        "confirmed": False,
        "discovery_deferred": True,
        "learnings_wiped": wiped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a fresh resettable demo without deleting prior evidence"
    )
    parser.add_argument(
        "--rediscover", action="store_true",
        help="deprecated: discovery always runs live after the user review"
    )
    parser.add_argument(
        "--live-vendor",
        default="",
        metavar="NAME",
        help="Google vendor name (or unique substring) represented by the live role-player",
    )
    parser.add_argument(
        "--wipe-learnings",
        action="store_true",
        help="explicitly delete learned questions for this demo area",
    )
    args = parser.parse_args()

    result = reset(args.rediscover, args.wipe_learnings, args.live_vendor)
    print(f"archived previous demo jobs: {result['archived_previous'] or '(none)'}")
    print(f"demo job: {result['job_id']}  (awaiting user confirmation, owner {DEMO_EMAIL})")
    print("Google Places discovery: pending the final review action (no cached list reused)")
    print(f"live role-play identity: {result['live_company_name']}")
    print(f"LIVE LABEL: {result['live_label']}")
    print(f"SYNTHETIC LABEL: {result['synthetic_label']}")
    print(
        f"DEBUG_CALLS={str(result['debug_calls']).lower()}  "
        f"demo phone ready: {result['demo_phone_ready']}"
    )
    if args.wipe_learnings:
        print(f"learned questions explicitly removed: {result['learnings_wiped']}")
    print(f"""
RUNBOOK:
  1. upload the demo PDF and complete the short browser voice intake
  2. review, authorize and launch: POST /api/jobs/{result['job_id']}/launch
     {{"authorize_demo_calls":true,"idempotency_key":"fresh-uuid"}}
     This calls Google Places live, promotes every callable result, selects the
     role-play identity, then starts the quote batches automatically.
  3. watch batch knowledge  : GET  /api/jobs/{result['job_id']}/call-queue
  4. inspect exact evidence : GET  /api/jobs/{result['job_id']}/calls
  5. ranked evidence report : GET  /api/jobs/{result['job_id']}/report

Only grounded offers from the frozen batch snapshot may be cited. A debug-generated
offer must always be described aloud as simulated demo-market data; it is never
evidence that the named Google business was called or agreed to that price.""")


if __name__ == "__main__":
    main()
