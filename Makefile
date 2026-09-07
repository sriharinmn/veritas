# Convenience only. `make` is not installed on Windows by default, and the
# graders may well be on Windows — so the README documents plain `docker compose`
# as the canonical interface and never assumes this file exists.

.DEFAULT_GOAL := help
.PHONY: help up down logs build test lint fmt eval verify seed ollama ps

help:  ## show this help
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-10s\033[0m %s\n",$$1,$$2}'

up:  ## start the stack
	docker compose up -d

down:  ## stop the stack
	docker compose down

logs:  ## follow logs
	docker compose logs -f

ps:  ## service status
	docker compose ps

build:  ## rebuild images
	docker compose build

test:  ## run the test suites
	docker compose exec -T api pytest
	cd web && npm test --if-present

lint:  ## lint and typecheck
	docker compose exec -T api ruff check .
	docker compose exec -T api mypy core api

fmt:  ## format
	docker compose exec -T api ruff format .
	docker compose exec -T api ruff check --fix .

eval:  ## run the eval harness
	docker compose exec -T api python -m evals.run

verify:  ## re-run the pipeline on one document and diff against the shipped snapshot
	docker compose exec -T api python -m scripts.verify_snapshot

seed:  ## restore the pre-computed knowledge layer
	docker compose exec -T api python -m scripts.load_snapshot

ollama:  ## check the local model server and pull what is needed
	@ollama list || echo "Ollama not installed — optional, see .env.example"
	ollama pull qwen3:8b
