# boba_joints task runner.  `just` (or `just --list`) shows all recipes.
set dotenv-load := true

default:
    @just --list

# --- database -------------------------------------------------------------

# Start the PostGIS container and wait until it is healthy (postgis ready)
db-up:
    docker compose up -d --wait
    @echo "postgres ready on localhost:5433"

# Stop the container, keep the data volume
db-down:
    docker compose down

# Stop the container AND delete all data
db-nuke:
    docker compose down -v

# psql shell inside the container
db-shell:
    docker compose exec db psql -U boba -d boba

# --- migrations ---------------------------------------------------------

# Apply all migrations
migrate:
    uv run alembic upgrade head

# Load the NYC borough polygons (one-time; needed by analyze)
seed:
    uv run python -m boba.seed

# Autogenerate a migration from model changes:  just revision "add foo"
revision message:
    uv run alembic revision --autogenerate -m "{{message}}"

# Show migration head(s) and what the DB is currently at
migrate-status:
    uv run alembic heads
    uv run alembic current

# Roll back one migration
migrate-down:
    uv run alembic downgrade -1

# Fail if models have drifted from migrations
migrate-check:
    uv run alembic check

# Run post-pipeline data invariants (boba/checks.py)
check:
    uv run python -m boba.checks

# --- quality ---------------------------------------------------------

# Lint + format-check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-fix lint + format
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Run the test suite (needs the db container up for migration/checks tests)
test:
    uv run pytest -q

# Build + serve the dashboard in a container at http://localhost:8501
dashboard:
    docker compose up -d --build dashboard
    @echo "dashboard: http://localhost:8501"

# Serve the dashboard from the host instead (no container build)
dashboard-local:
    uv run --group dashboard streamlit run dashboard/app.py

# --- pipeline ---------------------------------------------------------

# Overture: download NYC `place` extract, load boba candidates
ingest-overture:
    uv run python -m boba.ingest.overture

# DOHMH: download NYC restaurant inspections, load boba candidates + history
ingest-dohmh:
    uv run python -m boba.ingest.dohmh

# Link Overture places <-> DOHMH establishments (feeds Yelp linking + analyze)
match:
    uv run python -m boba.match

# Yelp: discover NYC bubbletea shops (primary source) + link to Overture/DOHMH
# (needs YELP_API_KEY; search calls capped, discovery cached)
ingest-yelp:
    uv run python -m boba.ingest.yelp

# Merge sources into boba_shops + write data/boba_status.csv + year summary
analyze:
    uv run python -m boba.analyze

# Whole pipeline from a cold start
all: db-up migrate seed ingest-overture ingest-dohmh match ingest-yelp analyze check
