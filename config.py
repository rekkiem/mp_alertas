"""
config.py — Configuración global mediante variables de entorno.
Todas las settings se leen desde .env o el entorno del sistema.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── API Mercado Público ─────────────────────────────────────────────────
    TICKET_MERCADO_PUBLICO: str = ""
    API_BASE_URL: str = "https://api.mercadopublico.cl/servicios/v1"
    API_RATE_PER_SECOND: int = 5
    API_RATE_PER_DAY: int = 10_000

    # ── Base de datos ───────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./mercadopublico.db"

    # ── SMTP / Correo ───────────────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True

    # ── Dashboard ───────────────────────────────────────────────────────────
    DASHBOARD_USER: str = "admin"
    DASHBOARD_PASS: str = "admin"
    SECRET_KEY: str = "cambia-esto-en-produccion"

    # ── Admin / Notificaciones ──────────────────────────────────────────────
    ADMIN_EMAIL: Optional[str] = None

    # ── Scheduler ──────────────────────────────────────────────────────────
    SCHEDULER_HOUR: int = 6
    SCHEDULER_MINUTE: int = 0
    TIMEZONE: str = "America/Santiago"

    # ── Alertas ────────────────────────────────────────────────────────────
    ALERT_DEDUP_DAYS: int = 30
    APP_BASE_URL: str = "http://localhost:5000"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
