.PHONY: build up down logs migration gen_migration down_migration

build:
	docker compose up -d --build

logs:
	docker compose logs bot

migration:
	docker compose run --rm migrate


gen_migration:
	docker compose run --rm migrate uv run --no-dev alembic \
		-c telegram_alert/migrations/alembic.ini revision --autogenerate

down_migration:
	docker compose run --rm migrate uv run --no-dev alembic \
		-c telegram_alert/migrations/alembic.ini downgrade -1
