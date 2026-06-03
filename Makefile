# ═══════════════════════════════════════════════════════════════
# Makefile — Comandos rápidos para MP Alertas
# Uso: make <target>
# ═══════════════════════════════════════════════════════════════

.DEFAULT_GOAL := help
.PHONY: help install test lint run run-once demo docker-build \
        docker-up docker-down docker-logs deploy-aws clean

PYTHON := python3
VENV   := .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

# ── Colores ────────────────────────────────────────────────────
GREEN  := \033[0;32m
YELLOW := \033[1;33m
NC     := \033[0m

help: ## Muestra esta ayuda
	@echo ""
	@echo "  MP Alertas — Comandos disponibles"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

# ── Setup ──────────────────────────────────────────────────────
install: ## Crea venv e instala dependencias
	@echo "$(GREEN)Instalando dependencias...$(NC)"
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	@[ -f .env ] || (cp .env.example .env && echo "$(YELLOW)⚠  .env creado desde .env.example. Edítalo antes de continuar.$(NC)")
	@echo "$(GREEN)✅ Listo. Ejecuta: make run$(NC)"

# ── Calidad ────────────────────────────────────────────────────
test: ## Ejecuta la suite de 84 tests
	@echo "$(GREEN)Ejecutando tests...$(NC)"
	DATABASE_URL=sqlite:// \
	TICKET_MERCADO_PUBLICO=TEST \
	DASHBOARD_USER=admin DASHBOARD_PASS=testpass \
	SECRET_KEY=test SMTP_USE_TLS=false \
	API_RATE_PER_SECOND=100 API_RATE_PER_DAY=999999 \
	$(PY) test_mvp.py

lint: ## Revisa estilo con flake8
	$(VENV)/bin/flake8 app/ main.py config.py \
	  --max-line-length=120 --select=E9,F63,F7,F82

# ── Ejecución local ────────────────────────────────────────────
run: ## Inicia servidor + scheduler (modo normal)
	$(PY) main.py

run-once: ## Ejecuta un ciclo inmediato de todas las reglas
	$(PY) main.py --once

demo: ## Carga datos de ejemplo y arranca el servidor
	$(PY) main.py --demo

init-db: ## Inicializa/migra la base de datos
	$(PY) main.py --init-db

# ── Docker local ───────────────────────────────────────────────
docker-build: ## Build de la imagen Docker
	docker build -t mp-alertas:local --target runtime .

docker-up: ## Levanta con docker compose (SQLite)
	docker compose up -d
	@echo "$(GREEN)Dashboard: http://localhost:5000$(NC)"

docker-up-pg: ## Levanta con docker compose + PostgreSQL
	docker compose --profile postgres up -d

docker-down: ## Detiene y elimina contenedores
	docker compose --profile postgres down

docker-logs: ## Muestra logs en tiempo real
	docker compose logs -f app

docker-shell: ## Abre shell en el contenedor en ejecución
	docker compose exec app bash

# ── AWS ────────────────────────────────────────────────────────
deploy-aws: test ## Deploy completo a AWS ECS Fargate
	bash deploy/aws/deploy.sh

deploy-aws-build: ## Solo build + push de imagen a ECR
	bash deploy/aws/deploy.sh --build-only

deploy-aws-stack: ## Solo actualizar CloudFormation
	bash deploy/aws/deploy.sh --stack-only

rollback-aws: ## Rollback al deployment anterior en AWS
	bash deploy/aws/deploy.sh --rollback

aws-logs: ## Tail de logs en CloudWatch
	aws logs tail /ecs/mp-alertas --follow --region us-east-1

# ── Limpieza ───────────────────────────────────────────────────
clean: ## Elimina archivos temporales y caché
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -f mercadopublico.db logs/*.log 2>/dev/null || true
	@echo "$(GREEN)✅ Limpieza completada$(NC)"

clean-all: clean ## Elimina también el venv y la BD
	rm -rf $(VENV) 2>/dev/null || true
	@echo "$(YELLOW)⚠  Venv eliminado. Ejecuta 'make install' para reinstalar.$(NC)"
