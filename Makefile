.PHONY: up down setup test attack seed eval eval-baseline eval-indirect eval-tools redteam-week6 redteam-week7

# Prepare a fresh clone: start Supabase, fill .env with its local keys, and build
# the API virtualenv. Idempotent, safe to re-run. Needs Docker, Node (npx), Python 3.
setup:
	npx supabase start
	python3 scripts/bootstrap_env.py
	cd apps/api && python3 -m venv .venv && . .venv/bin/activate && python -m pip install -q -e ".[dev]"

# One command for a stranger: bootstrap, seed demo tenants, run the app.
up:
	$(MAKE) setup
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

# Direct-injection eval against the shipped prompt; report is the release evidence.
# (Week 3 corpus, re-run each release; week3's original v3 report stays untouched.)
eval:
	mkdir -p evidence/week6
	set -a; . ./.env; set +a; PROMPTFOO_PYTHON=apps/api/.venv/bin/python npx promptfoo@0.120.27 eval -c evals/promptfooconfig.yaml --no-cache --no-share \
		-o evidence/week6/promptfoo-report-direct.json 2>&1 | tee evidence/week6/eval-run-direct.txt

# Week 6 indirect-injection eval: poisoned documents through the real RAG assembly.
eval-indirect:
	mkdir -p evidence/week6
	set -a; . ./.env; set +a; PROMPTFOO_PYTHON=apps/api/.venv/bin/python npx promptfoo@0.120.27 eval -c evals/promptfooconfig.indirect.yaml --no-cache --no-share \
		-o evidence/week6/promptfoo-report-indirect.json 2>&1 | tee evidence/week6/eval-run-indirect.txt

# Week 7 tool-injection eval: can a document steer the model into proposing an
# action? Runs through the real assembly + propose() with the tool registry offered.
eval-tools:
	mkdir -p evidence/week7
	set -a; . ./.env; set +a; PROMPTFOO_PYTHON=apps/api/.venv/bin/python npx promptfoo@0.120.27 eval -c evals/promptfooconfig.tools.yaml --no-cache --no-share \
		-o evidence/week7/promptfoo-report-tools.json 2>&1 | tee evidence/week7/eval-run-tools.txt

# Week 6 live red team: the malicious-document corpus against the running pipeline.
# REDTEAM_RUNS=10 for recorded evidence; both defenses (prompt + sanitizer) in path.
redteam-week6:
	set -a; . ./.env; set +a; cd apps/api && . .venv/bin/activate && python ../../evidence/week6/redteam_week6.py

# Week 7 live red team: secure tool calling against the running pipeline + DB.
# Scores artifacts (note rows, 429s), not prose. REDTEAM_RUNS=10 for recorded evidence.
redteam-week7:
	set -a; . ./.env; set +a; cd apps/api && . .venv/bin/activate && python ../../evidence/week7/redteam_week7.py

# Same corpus against the Week 2 prompt (chat v1) for the before/after comparison.
eval-baseline:
	mkdir -p evidence/week3
	set -a; . ./.env; set +a; PROMPTFOO_PYTHON=apps/api/.venv/bin/python npx promptfoo@0.120.27 eval -c evals/promptfooconfig.baseline.yaml --no-cache --no-share \
		-o evidence/week3/promptfoo-report-baseline-v1.json 2>&1 | tee evidence/week3/eval-run-baseline-v1.txt
