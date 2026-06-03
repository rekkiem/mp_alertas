#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# start.sh — Inicio local del MVP MP Alertas (sin Docker)
# Uso: ./deploy/local/start.sh
# ═══════════════════════════════════════════════════════════════
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       MP Alertas — Inicio Local          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Pre-requisitos ─────────────────────────────────────────────
command -v python3 &>/dev/null || error "Python 3 no encontrado"
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYTHON_VER detectado"

# ── .env ──────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    warn ".env no encontrado. Copiando desde .env.example..."
    cp .env.example .env
    warn "⚠️  Edita .env con tus valores reales antes de usar en producción"
fi

# ── Entorno virtual ────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    info "Creando entorno virtual..."
    python3 -m venv .venv
fi

source .venv/bin/activate
info "Entorno virtual activado: $VIRTUAL_ENV"

# ── Dependencias ───────────────────────────────────────────────
info "Instalando dependencias..."
pip install -r requirements.txt -q

# ── Logs dir ───────────────────────────────────────────────────
mkdir -p logs

# ── Tests (opcional, saltar con --skip-tests) ──────────────────
if [[ "$*" != *"--skip-tests"* ]]; then
    info "Ejecutando suite de tests..."
    python test_mvp.py 2>&1 | tail -10
fi

# ── Demo data ──────────────────────────────────────────────────
if [[ "$*" == *"--demo"* ]]; then
    info "Cargando datos de demostración e iniciando servidor..."
    python main.py --demo
    exit 0   # --demo ya arranca el servidor; no continuar
fi

# ── Inicializar BD ─────────────────────────────────────────────
info "Inicializando base de datos..."
python main.py --init-db

# ── Arrancar ───────────────────────────────────────────────────
info "Arrancando servidor + scheduler..."
echo ""
echo "  Dashboard: http://localhost:5000"
echo "  Usuario:   \$DASHBOARD_USER (ver .env)"
echo "  Contraseña:\$DASHBOARD_PASS (ver .env)"
echo ""
echo "  Ctrl+C para detener"
echo ""

python main.py
