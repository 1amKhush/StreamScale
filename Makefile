COMPOSE = docker compose --env-file .env -f deploy/local/compose.yaml
RUN = uv run --frozen --env-file .env

.PHONY: install up bootstrap smoke test integration check down logs

install:
	uv sync --frozen

up:
	$(COMPOSE) up -d --wait --wait-timeout 180

bootstrap:
	$(RUN) streamscale topics
	$(RUN) streamscale migrate

smoke:
	$(RUN) streamscale smoke

test:
	uv run --frozen pytest

integration:
	$(RUN) pytest -m integration

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --tail 80
