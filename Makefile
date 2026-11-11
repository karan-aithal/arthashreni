# ArthaShreni — Linux only (ADR-0018). Outside PYTHONPATH (e.g. ROS) is cleared.
unexport PYTHONPATH
.PHONY: install hooks migrate user run verify verify-fast backup restore-test

install:            ## install locked dependencies and git hooks
	uv sync --frozen
	git config core.hooksPath .githooks

migrate:            ## apply database migrations (as artha_owner)
	uv run alembic upgrade head

user:               ## create the local login user
	uv run arthashreni user init

run:                ## start the API on 127.0.0.1:8000
	uv run arthashreni serve

verify:             ## full acceptance check (G1)
	uv run python scripts/verify.py

verify-fast:        ## pre-commit subset
	uv run python scripts/verify.py --fast

backup:             ## encrypted backup to data/backups
	uv run arthashreni backup create

restore-test:       ## dump, restore into scratch DB, compare, drop
	uv run arthashreni backup restore-test
