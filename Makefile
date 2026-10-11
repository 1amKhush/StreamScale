COMPOSE = docker compose --env-file .env -f deploy/local/compose.yaml
RUN = uv run --frozen --env-file .env
RUN_DIR ?= artifacts/demo

.PHONY: install up bootstrap smoke demo test integration faults check down logs k8s-tools k8s-up k8s-verify k8s-ui k8s-status k8s-logs k8s-addons scaling experiments experiments-fresh experiment-audit analysis

install:
	uv sync --frozen

up:
	$(COMPOSE) up -d --wait --wait-timeout 180

bootstrap:
	$(RUN) streamscale topics
	$(RUN) streamscale migrate

smoke:
	$(RUN) streamscale smoke

demo:
	$(RUN) streamscale generate --output "$(RUN_DIR)" --seed 42 --orders 2 --profile fixtures
	$(RUN) streamscale publish --run-dir "$(RUN_DIR)" --no-wait
	$(RUN) streamscale consume --run-dir "$(RUN_DIR)"
	$(RUN) streamscale inspect --run-dir "$(RUN_DIR)"

test:
	uv run --frozen pytest

integration:
	$(RUN) pytest -m integration

faults:
	$(RUN) pytest -m integration tests/integration/test_faults.py

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --tail 80

k8s-tools:
	uv run --frozen python deploy/k8s/install_tools.py

.tools/bin/kind .tools/bin/kubectl .tools/bin/helm: deploy/k8s/install_tools.py
	uv run --frozen python deploy/k8s/install_tools.py

k8s-up: .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	uv run --frozen python deploy/k8s/manage.py up

k8s-verify:
	uv run --frozen python deploy/k8s/verify.py

k8s-ui:
	./streamscale.sh ui

k8s-status:
	uv run --frozen python deploy/k8s/manage.py status

k8s-logs:
	uv run --frozen python deploy/k8s/manage.py logs

k8s-addons: .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	uv run --frozen python deploy/k8s/addons.py

scaling: .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	uv run --frozen python deploy/k8s/scaling.py

experiments: .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	uv run --frozen python -m experiments.run --resume
	uv run --frozen python -m experiments.evidence

experiments-fresh: .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	uv run --frozen python -m experiments.run --fresh
	uv run --frozen python -m experiments.evidence

experiment-audit:
	uv run --frozen python -m experiments.evidence

analysis:
	uv run --project experiments/analysis --frozen python -m experiments.analyze

# Interactive console (M9+): separately locked Python and Node dependencies.
CONSOLE = uv run --project console --frozen
CONSOLE_ARGS ?=

.PHONY: console-install console-web console-test console-deploy console-live console-check console-serve

console-install:
	uv sync --project console --frozen
	cd console/web && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci

console-web: console-install
	cd console/web && npm run build

console-test: console-install
	$(CONSOLE) pytest console/tests
	cd console/web && npm run typecheck && npm test

console-deploy: console-install .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	$(CONSOLE) python -m streamscale_console deploy

console-live:
	$(CONSOLE) pytest console/tests/live -m live

console-check: console-install .tools/bin/kind .tools/bin/kubectl .tools/bin/helm
	$(CONSOLE) python -m streamscale_console check

console-serve:
	$(CONSOLE) python -m streamscale_console serve $(CONSOLE_ARGS)
