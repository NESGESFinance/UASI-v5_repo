# =============================================================================
# UASI v5 Enterprise Production — Makefile
# Uso: make <target>      (make help para ver la lista)
# =============================================================================
SHELL        := /bin/bash
PYTHON       ?= python3
VENV         ?= .venv
PY           := $(VENV)/bin/python
PIP          := $(VENV)/bin/pip
APP          := uasi_v5.main:app
HOST         ?= 0.0.0.0
PORT         ?= 8000
WORKERS      ?= 4
COMPOSE      := docker compose -f docker/docker-compose.yml --env-file .env
IMAGE        ?= uasi-v5:5.1.0

export PYTHONPATH := $(CURDIR)/src

# Carga .env si existe (las variables quedan disponibles para los comandos)
ifneq (,$(wildcard .env))
include .env
export
endif

.DEFAULT_GOAL := help
.PHONY: help venv install dev test test-ots lint format run run-prod docker-build docker-up docker-down docker-logs anchor-btc verify-ots secrets clean

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(PY):
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

venv: $(PY) ## Crea el entorno virtual .venv

install: $(PY) ## Instala dependencias de producción y desarrollo
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e . --no-deps

dev: ## Arranca en modo desarrollo (uvicorn con recarga)
	$(PY) -m uvicorn $(APP) --host $(HOST) --port $(PORT) --reload --reload-dir src

run: dev ## Alias de dev

test: ## Ejecuta pytest sin red
	UASI_TEST_OTS_RED=0 $(PY) -m pytest

test-ots: ## Ejecuta pytest con anclaje real en OpenTimestamps
	UASI_TEST_OTS_RED=1 $(PY) -m pytest

lint: ## ruff + mypy
	$(PY) -m ruff check src tests
	$(PY) -m mypy

format: ## Formatea con black (+ orden de imports con ruff)
	$(PY) -m ruff check --select I --fix src tests
	$(PY) -m black src tests

run-prod: ## gunicorn con 4 workers uvicorn
	$(VENV)/bin/gunicorn $(APP) -k uvicorn.workers.UvicornWorker -w $(WORKERS) \
		-b $(HOST):$(PORT) --timeout 300 --graceful-timeout 30 --access-logfile -

docker-build: ## Construye la imagen Docker
	docker build -f docker/Dockerfile -t $(IMAGE) .

docker-up: ## Levanta uasi-api + redis con docker compose
	@test -f .env || (echo "Falta .env: cp .env.example .env && ./scripts/generar_secretos.sh" && exit 1)
	$(COMPOSE) up -d --build

docker-down: ## Detiene los contenedores
	$(COMPOSE) down

docker-logs: ## Sigue los logs de la API
	$(COMPOSE) logs -f uasi-api

anchor-btc: ## Ancla la raíz de Merkle de hoy en Bitcoin (FECHA=YYYY-MM-DD opcional)
	./scripts/anclar_ledger_bitcoin.sh $(FECHA)

verify-ots: ## Verifica la prueba .ots de una fecha (FECHA=YYYY-MM-DD)
	./scripts/verificar_ots.sh $(FECHA)

secrets: ## Genera SECRET_JWT_KEY y UASI_MODEL_HMAC_KEY en .env
	./scripts/generar_secretos.sh --write

clean: ## Borra cachés
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
