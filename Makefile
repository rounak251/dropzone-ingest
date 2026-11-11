# Every target runs from the project root. uv manages the Python environment.
UV := env -u VIRTUAL_ENV uv run
export DZ_DATABASE_URL ?= postgresql://dropzone:dropzone@127.0.0.1:55432/dropzone
N ?= 50
ROWS ?= 500000
QUICK ?=
FOOTPRINT_LABEL ?= idle

.PHONY: up down reset test e2e drill throughput hygiene loc footprint mutants

up:
	docker compose up -d --wait

down:
	docker compose down -v

reset:
	$(UV) python scripts/reset_db.py

test:
	$(UV) pytest -q

# Ingest three fixture drops into an empty database, reconcile, and diff the report against the golden file.
e2e: reset
	$(UV) dropzone ingest fixtures/e2e/day1 fixtures/e2e/day2 fixtures/e2e/day3
	mkdir -p results/e2e
	$(UV) dropzone reconcile --out-dir results/e2e
	diff -u tests/golden/report.md results/e2e/report.md
	$(UV) dropzone verify --dump results/e2e/dump.txt

drill:
	$(UV) python scripts/drill.py --runs $(N)

# Smoke mode: make throughput QUICK=1 caps the row count. Final numbers are measured later.
throughput:
	$(UV) python scripts/throughput.py --rows $(ROWS) $(if $(QUICK),--quick,)

# Plants each blind bug in a copy of the tree and checks that the tests fail. Takes about 2 minutes.
mutants:
	$(UV) python scripts/mutants.py

hygiene:
	bash scripts/check_imports.sh

loc:
	bash scripts/loc.sh

# Appends one labelled section per run. Delete docs/footprint.txt to start again.
footprint:
	test -n "$$(docker compose ps -q)" || { echo "no compose stack running: run docker compose up -d" >&2; exit 1; }
	{ echo "# $(FOOTPRINT_LABEL): docker stats --no-stream, captured $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; docker stats --no-stream $$(docker compose ps -q); echo; echo "# Docker VM memory (bytes):"; docker info --format "{{.MemTotal}}"; echo; } >> docs/footprint.txt
