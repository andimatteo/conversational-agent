from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from negotiator import auth, db
from negotiator.packs import load_pack

from .service import DiscoveryService

router = APIRouter(prefix="/api/jobs/{job_id}/call-list", tags=["call list"])


class DiscoverCallListIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=2, max_length=80)
    query: str = Field(default="", max_length=120)
    target_per_provider: int = Field(default=250, ge=1, le=1000)


def get_discovery_service() -> DiscoveryService:
    return DiscoveryService()


def _owned_job(job_id: str, user: dict) -> dict:
    job = db.get("jobs", job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404, "job not found")
    return job


@router.get("")
def get_call_list(job_id: str, user: dict = Depends(auth.current_user)):
    job = _owned_job(job_id, user)
    return job.get("call_list", {
        "generated_at": "", "query": "", "state": {},
        "target_per_provider": 0,
        "required_sources": ["google_places", "yelp", "openstreetmap"],
        "complete": False, "saved": False, "provider_status": {},
        "raw_results": 0, "total": 0, "items": [],
    })


@router.post("/discover")
def discover_call_list(body: DiscoverCallListIn, job_id: str,
                       user: dict = Depends(auth.current_user),
                       service: DiscoveryService = Depends(get_discovery_service)):
    job = _owned_job(job_id, user)
    pack = load_pack(job["vertical"], job.get("area_code", ""))
    query = body.query.strip() or pack["meta"]["counterparty_noun"]
    try:
        result = service.discover(query, body.state, body.target_per_provider)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Could not resolve the search area: {str(exc)[:200]}")

    # Keep the call list separate from companies/quotes until a real call is scheduled.
    # This prevents uncalled discovery results from appearing as declines in the report.
    result["saved"] = False
    if result["complete"]:
        result["saved"] = True
        job["call_list"] = result
        db.put("jobs", job_id, job)
    return result
