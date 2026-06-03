#!/usr/bin/env python3
"""
main.py — Punto de entrada único del sistema MP Alertas.

Modos de ejecución:
  python main.py               → Servidor web + scheduler en background
  python main.py --once        → Una sola pasada de todas las reglas activas
  python main.py --init-db     → Solo inicializa la BD y sale
  python main.py --demo        → Crea datos de demostración

Variables de entorno requeridas (ver .env.example):
  TICKET_MERCADO_PUBLICO, SMTP_*, DASHBOARD_USER, DASHBOARD_PASS
"""
import argparse
import logging
import os
import sys
import signal
import threading
from datetime import datetime

# ── Logging estructurado ──────────────────────────────────────────────────────
LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FMT,
    datefmt=LOG_DATE,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ── Imports de la app ─────────────────────────────────────────────────────────
from config import settings
from app.database import init_db
from app.scheduler import iniciar_scheduler, detener_scheduler, ejecutar_ciclo_completo
from app.dashboard import create_app


# ── Señales de apagado limpio ─────────────────────────────────────────────────
_shutdown_event = threading.Event()

def _signal_handler(sig, frame):
    logger.info("Señal %s recibida. Apagando…", sig)
    _shutdown_event.set()

signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── Modo --once ───────────────────────────────────────────────────────────────
def run_once():
    """Ejecuta una sola pasada de todas las reglas activas y sale."""
    logger.info("Modo --once: ejecutando ciclo completo…")
    init_db()
    try:
        stats = ejecutar_ciclo_completo()
        logger.info(
            "Ciclo completado: %d reglas evaluadas, %d alertas generadas.",
            stats.get("reglas_evaluadas", 0),
            stats.get("alertas_generadas", 0),
        )
        sys.exit(0)
    except Exception as e:
        logger.exception("Error en ciclo único: %s", e)
        sys.exit(1)


# ── Modo --demo ───────────────────────────────────────────────────────────────
def seed_demo():
    """Inserta datos de demostración en la BD."""
    from app.database import get_db
    from app.models import ReglaUsuario
    logger.info("Insertando datos de demostración…")
    init_db()
    with get_db() as db:
        if db.query(ReglaUsuario).count() > 0:
            logger.info("Ya existen reglas. Saltando seed.")
            return

        ejemplos = [
            ReglaUsuario(
                nombre_regla="Licitaciones TI en RM (>$1M)",
                activa=True,
                tipo_entidad="licitacion",
                filtros={
                    "region": 13,
                    "titulo_contains": "software",
                    "monto_min": 1_000_000,
                },
                email_destino="demo@example.com",
            ),
            ReglaUsuario(
                nombre_regla="Órdenes de Compra Región de Los Lagos",
                activa=True,
                tipo_entidad="orden_compra",
                filtros={
                    "region": 10,
                    "monto_min": 500_000,
                },
                email_destino="demo@example.com",
            ),
            ReglaUsuario(
                nombre_regla="Compra Ágil - Equipos computacionales",
                activa=False,
                tipo_entidad="compra_agil",
                filtros={
                    "titulo_contains": "notebook",
                    "monto_max": 5_000_000,
                },
                email_destino="demo@example.com, otro@example.com",
            ),
        ]
        for r in ejemplos:
            db.add(r)
        logger.info("Insertadas %d reglas de demostración.", len(ejemplos))


# ── Servidor principal (Flask + Scheduler) ────────────────────────────────────
def run_server():
    """
    Inicia:
      1. Scheduler APScheduler en hilo de fondo
      2. Servidor Flask (bloquea el hilo principal)
    """
    logger.info("=== MP Alertas v1.0 iniciando ===")
    logger.info("BD: %s", settings.DATABASE_URL)
    logger.info("Scheduler: %02d:%02d (%s)", settings.SCHEDULER_HOUR, settings.SCHEDULER_MINUTE, settings.TIMEZONE)

    # Inicializar BD
    init_db()
    logger.info("Base de datos inicializada.")

    # Iniciar worker de email en hilo de fondo
    from app.email_service import iniciar_worker
    iniciar_worker()
    logger.info("Worker de email iniciado.")

    # Crear y arrancar el scheduler
    scheduler = iniciar_scheduler()
    logger.info(
        "Scheduler iniciado. Próxima ejecución: %s",
        scheduler.get_jobs()[0].next_run_time if scheduler.get_jobs() else "N/A",
    )

    # Crear la app Flask
    flask_app = create_app()

    # Arrancar Flask en un hilo separado para no bloquear la señal de apagado
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)),
            debug=False,
            use_reloader=False,       # Importante: sin reloader en producción
        ),
        daemon=True,
        name="flask-server",
    )
    flask_thread.start()
    logger.info("Dashboard disponible en http://localhost:5000")

    # Esperar señal de apagado
    _shutdown_event.wait()

    logger.info("Deteniendo scheduler…")
    detener_scheduler()
    logger.info("Apagado limpio completado.")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    os.makedirs("logs", exist_ok=True)

    parser = argparse.ArgumentParser(
        description="MP Alertas — Monitor de Mercado Público con alertas por email"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecutar una sola pasada de todas las reglas activas y salir",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Inicializar la base de datos y salir",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Insertar datos de demostración y arrancar el servidor",
    )
    args = parser.parse_args()

    if args.init_db:
        init_db()
        logger.info("Base de datos inicializada correctamente.")
        sys.exit(0)

    if args.once:
        run_once()
        return  # never reached (sys.exit inside)

    if args.demo:
        seed_demo()

    run_server()


if __name__ == "__main__":
    main()
