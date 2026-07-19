"""Offline test for deterministic post-call learned questions.

Run with::

    .venv/bin/python -m tests.learnings_test
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor


os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="negotiator-learnings-test-"))

from negotiator import db
from negotiator.learnings import persist_questions, questions_from_call


def main():
    job = {
        "id": db.new_id("job"),
        "vertical": "plumbing",
        "area_code": "28202",
        "spec": {},
        "discovered_questions": [],
    }
    db.put("jobs", job["id"], job)

    permit = "Will this job require a city permit?"
    first = persist_questions(
        job,
        [
            {"question": permit, "why_it_matters": "Permit handling changes fees and timing."},
            {"question": "  WILL this job require a city permit  "},
        ],
        source_call_id="call_1",
        company_id="co_1",
    )
    assert [q["question"] for q in first["added"]] == [permit]
    rows = db.where("learned_questions", vertical="plumbing", area_code="28202")
    assert len(rows) == 1 and rows[0]["times_seen"] == 1
    assert rows[0]["source_call_ids"] == ["call_1"] and rows[0]["company_ids"] == ["co_1"]

    # A completion retry is idempotent; another call is a new observation.
    replay = persist_questions(job, [permit], source_call_id="call_1", company_id="co_1")
    assert replay["added"] == [] and replay["updated"][0]["times_seen"] == 1
    second = persist_questions(job, [permit], source_call_id="call_2", company_id="co_2")
    assert second["updated"][0]["times_seen"] == 2
    rows = db.where("learned_questions", vertical="plumbing", area_code="28202")
    assert rows[0]["source_call_ids"] == ["call_1", "call_2"]
    assert rows[0]["company_ids"] == ["co_1", "co_2"]
    assert len(job["discovered_questions"]) == 1, "known questions must not be re-added to the job"

    access = "Is there restricted access to the work area?"
    third = persist_questions(job, [access], source_call_id="call_3", company_id="co_2")
    assert [q["question"] for q in third["added"]] == [access]
    stored_job = db.get("jobs", job["id"])
    assert [q["question"] for q in stored_job["discovered_questions"]] == [permit, access]

    # Sibling calls completing together must not overwrite one another's job
    # projection while they update the same domain/area pool.
    concurrent = [
        ("Does the site have a restricted service window?", "call_parallel_1", "co_3"),
        ("Must old equipment be hauled away?", "call_parallel_2", "co_4"),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(persist_questions, db.get("jobs", job["id"]), [question], call_id, co_id)
                   for question, call_id, co_id in concurrent]
        [future.result() for future in futures]
    projected = {q["question"] for q in db.get("jobs", job["id"])["discovered_questions"]}
    assert {question for question, _, _ in concurrent} <= projected

    # Pools are isolated by area even when the normalized question is identical.
    other_job = {**job, "id": db.new_id("job"), "area_code": "99999", "discovered_questions": []}
    db.put("jobs", other_job["id"], other_job)
    isolated = persist_questions(other_job, [permit], source_call_id="call_4", company_id="co_3")
    assert len(isolated["added"]) == 1

    pack = {"fee_taxonomy": {"emergency": "Emergency / after-hours surcharge",
                              "permit": "Permit and inspection"}}
    quote = {
        "line_items": [
            {"code": "emergency", "label": "After-hours fee", "amount": 100,
             "contingent": True},
        ],
        "conditions": ["Customer supplies parts; permit is not included."],
    }
    call = {"transcript": [
        {"role": "agent", "text": "Do you charge for every possible fee?"},
        {"role": "user", "text": "There is an extra disposal charge if access is tight."},
    ]}
    extracted = questions_from_call(job, call, quote, pack)
    text = " ".join(q["question"].casefold() for q in extracted)
    assert "urgently" in text or "after hours" in text
    assert "parts or materials" in text
    assert "permits" in text
    assert "disposal" in text and "access" in text
    assert len({q["question"].casefold() for q in extracted}) == len(extracted)

    fallback = questions_from_call(job, {"transcript": []}, {}, pack={})
    assert len(fallback) == 1 and "access constraints" in fallback[0]["question"].casefold()

    print("LEARNINGS TEST PASSED")


if __name__ == "__main__":
    main()
