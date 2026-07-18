"""Seed a demo job + the simulated market.

  python -m negotiator.seed                    # empty job (intake fills the spec)
  python -m negotiator.seed --with-sample-spec # Daniel's Rock Hill -> Charlotte move, pre-confirmed
"""
import argparse
import json

from . import db
from .benchmarks import market_range
from .config import personas, registry_path, vertical
from .models import Company, Job

# The brief's own scenario: the move with a documented $1,158-$6,506 spread.
SAMPLE_SPEC = {
    "vertical": "moving",
    "origin": {"city": "Rock Hill", "state": "SC", "floor": 2, "elevator": False,
               "stairs_flights": 1, "parking_distance_ft": 40},
    "destination": {"city": "Charlotte", "state": "NC", "floor": 3, "elevator": True,
                    "stairs_flights": 0, "parking_distance_ft": 90},
    "distance_miles": 45,
    "move_date": "2026-08-08",
    "date_flexible": True,
    "home_size": "2BR",
    "inventory": [
        {"item": "queen bed + frame", "qty": 1, "special": "none"},
        {"item": "sofa (3-seat)", "qty": 1, "special": "oversized"},
        {"item": "dining table + 4 chairs", "qty": 1, "special": "none"},
        {"item": "dresser", "qty": 2, "special": "none"},
        {"item": "desk + office chair", "qty": 1, "special": "none"},
        {"item": "TV 55-inch", "qty": 1, "special": "fragile"},
        {"item": "washer + dryer", "qty": 1, "special": "appliance"},
    ],
    "boxes_estimate": 35,
    "services": {"packing": False, "disassembly": True, "storage": False},
    "notes": "Elevator at destination must be reserved with building management.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-sample-spec", action="store_true")
    args = ap.parse_args()

    job = Job(id=db.new_id("job"), vertical=vertical()["meta"]["vertical"],
              area_code=vertical()["meta"].get("area_code", ""))
    if args.with_sample_spec:
        job.spec, job.spec_source, job.confirmed = SAMPLE_SPEC, "sample", True
    db.put("jobs", job.id, job.model_dump())

    agents = {}
    if registry_path().exists():
        agents = json.loads(registry_path().read_text()).get("agents", {})
    else:
        print("NOTE: agents/registry.json missing — companies seeded without agent ids; "
              "run `python -m agents.provision` then re-seed.")

    for p in personas():
        co = Company(id=db.new_id("co"), name=p["company_name"], persona=p["id"],
                     source="simulated", agent_id=agents.get(f"counterparty:{p['id']}", ""))
        db.put("companies", co.id, co.model_dump(), job_id=job.id)
        print(f"  {co.id}  {co.name:<28} [{p['style']}]")

    print(f"\nJob: {job.id}  (confirmed={job.confirmed})")
    if args.with_sample_spec:
        print(f"Benchmark for this job: {market_range(job.spec)}")
    print(f"\nNext: python -m simulation.run_calls --job {job.id} --phase quote")


if __name__ == "__main__":
    main()
