.PHONY: install
install:
	@poetry install

.PHONY: run
run:
	@poetry run python main.py

.PHONY: test
test:
	@poetry run python -m pytest -vv

.PHONY: test-integration
test-integration:
	@poetry run python -m pytest -vv -m integration

.PHONY: test-all
test-all:
	@poetry run python -m pytest -vv -m ""

.PHONY: lint
lint:
	@poetry run ruff check .
	@poetry run ruff format --check .

.PHONY: format
format:
	@poetry run ruff check --fix .
	@poetry run ruff format .

.PHONY: simulator
simulator:
	@docker compose up -d bank_simulator
