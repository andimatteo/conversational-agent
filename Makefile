.PHONY: test

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m tests.auth_test
	$(PYTHON) -m tests.estimator_test
	$(PYTHON) -m tests.documents_test
	$(PYTHON) -m tests.market_discovery_test
	$(PYTHON) -m tests.callqueue_test
	$(PYTHON) -m tests.smoke_test
	$(PYTHON) -m tests.spec_validation_test
	$(PYTHON) -m tests.learnings_test
	$(PYTHON) -m tests.debugcalls_test
	$(PYTHON) -m tests.batching_test
	$(PYTHON) -m tests.runclaims_test
	$(PYTHON) -m tests.evidence_test
	$(PYTHON) -m tests.provider_status_test
	$(PYTHON) -m tests.recall_limits_test
