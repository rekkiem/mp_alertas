"""
app/dashboard/__init__.py — Flask application factory.
"""
from __future__ import annotations

import base64
import functools
import logging
from typing import Optional

from flask import Flask, request, Response

from config import settings

logger = logging.getLogger(__name__)


def _check_auth(username: str, password: str) -> bool:
    return username == settings.DASHBOARD_USER and password == settings.DASHBOARD_PASS


def _requires_auth(f):
    """Decorador de Basic Auth para proteger rutas del dashboard."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not _check_auth(auth.username, auth.password):
            return Response(
                "Acceso denegado. Ingresa tus credenciales.",
                401,
                {"WWW-Authenticate": 'Basic realm="MP Alertas"'},
            )
        return f(*args, **kwargs)
    return decorated


def _format_number(value):
    """Filtro Jinja2: formatea número con separadores de miles."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value or "—"


def create_app() -> Flask:
    """Crea y configura la aplicación Flask."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = settings.SECRET_KEY

    # Registrar filtros Jinja2
    @app.template_filter("monto_fmt")
    def monto_fmt(v):
        try:
            v = int(v)
            if v >= 1_000_000_000:
                return f"${v/1_000_000_000:.2f}B"
            if v >= 1_000_000:
                return f"${v/1_000_000:.2f}M"
            if v >= 1_000:
                return f"${v/1_000:.0f}K"
            return f"${v:,}"
        except (TypeError, ValueError):
            return "N/D"

    @app.template_filter("tipo_badge")
    def tipo_badge(v):
        m = {
            "licitacion": ("🏛", "badge-licitacion", "Licitación"),
            "orden_compra": ("📋", "badge-oc", "Orden de Compra"),
            "compra_agil": ("⚡", "badge-agil", "Compra Ágil"),
        }
        return m.get(v, ("📌", "badge-default", v))

    # Registrar blueprint de rutas
    from app.dashboard.routes import bp
    app.register_blueprint(bp)

    # Proteger todo el blueprint con Basic Auth si está habilitado
    # (se aplica en cada ruta individualmente para granularidad)

    app.jinja_env.filters['format_number'] = _format_number
    return app
