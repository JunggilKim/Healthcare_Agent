.PHONY: bootstrap lint typecheck test test-offline frontend-build docker-build demo-offline live-local eval build-snapshot verify-release deploy smoke-prod

bootstrap:
	uv sync --python 3.12
	npm install

lint:
	uv run ruff check backend tests scripts
	uv run ruff format --check backend tests scripts
	npm run lint

typecheck:
	uv run mypy
	npm run typecheck

test:
	uv run pytest
	npm test -- --run

test-offline: test

frontend-build:
	npm run build

docker-build:
	docker build -t trial-opt:local .

demo-offline: frontend-build
	APP_ENV=local STORE_BACKEND=local DEFAULT_RUNTIME_MODE=snapshot uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8080

live-local:
	APP_ENV=local STORE_BACKEND=local DEFAULT_RUNTIME_MODE=live uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8080

eval:
	uv run python scripts/evaluate.py --suite all --config config/eval.yaml

build-snapshot:
	uv run python scripts/build_demo_snapshot.py --cases S004,S008,S001 --mode live --manual-review-manifest data/demo/manual_review.yaml --output data/demo/current

verify-release:
	uv run python scripts/verify_release.py --strict

deploy:
	./scripts/deploy.sh

smoke-prod:
	./scripts/smoke_test_deployment.sh
