.PHONY: up down test attack seed

# One command for a stranger: bring up Supabase, seed demo tenants, run the app.
up:
	npx supabase start
	$(MAKE) seed
	docker compose up --build -d
	@echo "web  http://localhost:3000"
	@echo "api  http://localhost:8000"

down:
	docker compose down
	npx supabase stop

seed:
	set -a; . ./.env; set +a; cd apps/api && . .venv/bin/activate && python ../../scripts/seed_users.py

# Runs both suites the CI gate runs: the API tests and the database-layer RLS tests.
test:
	set -a; . ./.env; set +a; . apps/api/.venv/bin/activate; python -m pytest -q apps/api/tests tests/rls
	cd apps/web && npm test

# Reproduce the Week 1 cross-tenant attack run and capture the evidence.
attack:
	mkdir -p evidence/week1
	set -a; . ./.env; set +a; cd apps/api && . .venv/bin/activate && pytest tests/test_attacks.py -v 2>&1 | tee ../../evidence/week1/attack-run.txt
