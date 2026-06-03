"""
app/dashboard/routes.py — Todas las rutas Flask del dashboard.

Rutas HTML:
  GET  /                   → Listado de reglas
  GET  /reglas/nueva       → Formulario nueva regla
  POST /reglas/nueva       → Crear regla
  GET  /reglas/<id>/editar → Formulario editar
  POST /reglas/<id>/editar → Guardar cambios
  POST /reglas/<id>/toggle → Activar/desactivar
  POST /reglas/<id>/delete → Eliminar
  POST /reglas/<id>/run    → Ejecutar manualmente
  GET  /alertas            → Listado de alertas (paginado)
  GET  /alertas/<id>       → Detalle de alerta
  GET  /analytics          → Dashboard de análisis estratégico

API JSON interna:
  GET  /api/analytics/resumen
  GET  /api/analytics/categorias
  GET  /api/analytics/organismos
  GET  /api/analytics/regional
  GET  /api/analytics/temporal
  GET  /api/analytics/estados
  GET  /api/analytics/nichos
  GET  /api/analytics/estacionalidad
  GET  /api/status
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Blueprint, Response, abort, flash, jsonify,
    redirect, render_template, request, url_for,
)

from config import settings
from app.database import get_db
from app.models import ReglaUsuario, AlertaGenerada, LicitacionSnapshot
from app.filter_engine import validar_filtros, describe_filtros, FILTROS_VALIDOS
from app.analytics import analytics, REGIONES_CHILE
from app.scheduler import ejecutar_regla_manualmente

logger = logging.getLogger(__name__)
bp = Blueprint("dashboard", __name__)


# ── Auth helper ───────────────────────────────────────────────────────────────

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        ok = (
            auth
            and auth.username == settings.DASHBOARD_USER
            and auth.password == settings.DASHBOARD_PASS
        )
        if not ok:
            return Response(
                "Acceso denegado.",
                401,
                {"WWW-Authenticate": 'Basic realm="MP Alertas"'},
            )
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_filtros_form(form) -> dict:
    """Convierte el formulario HTML a un dict de filtros validado."""
    filtros = {}

    # Región
    region_val = form.get("region", "").strip()
    if region_val:
        try:
            filtros["region"] = int(region_val)
        except ValueError:
            pass

    # Montos
    monto_min = form.get("monto_min", "").strip().replace(".", "").replace(",", "")
    monto_max = form.get("monto_max", "").strip().replace(".", "").replace(",", "")
    if monto_min:
        try:
            filtros["monto_min"] = int(monto_min)
        except ValueError:
            pass
    if monto_max:
        try:
            filtros["monto_max"] = int(monto_max)
        except ValueError:
            pass

    # Texto en título
    titulo_inc = form.get("titulo_contains", "").strip()
    titulo_exc = form.get("titulo_not_contains", "").strip()
    if titulo_inc:
        filtros["titulo_contains"] = titulo_inc
    if titulo_exc:
        filtros["titulo_not_contains"] = titulo_exc

    # Descripción
    desc = form.get("descripcion_contains", "").strip()
    if desc:
        filtros["descripcion_contains"] = desc

    # Organismo
    org = form.get("organismo_contains", "").strip()
    if org:
        filtros["organismo_contains"] = org

    # Estado
    estado = form.get("estado", "").strip()
    if estado:
        filtros["estado"] = estado

    # Códigos de producto (separados por coma o salto de línea)
    codigos_raw = form.get("codigo_producto_in", "").strip()
    if codigos_raw:
        codigos = [c.strip() for c in codigos_raw.replace("\n", ",").split(",") if c.strip()]
        if codigos:
            filtros["codigo_producto_in"] = codigos

    # JSON avanzado (override si el usuario lo ingresó directamente)
    json_raw = form.get("filtros_json", "").strip()
    if json_raw:
        try:
            filtros_override = json.loads(json_raw)
            filtros.update(filtros_override)
        except json.JSONDecodeError:
            pass

    return filtros


# ── Reglas ────────────────────────────────────────────────────────────────────

@bp.route("/")
@requires_auth
def index():
    with get_db() as db:
        reglas = db.query(ReglaUsuario).order_by(ReglaUsuario.fecha_creacion.desc()).all()
        reglas_data = []
        for r in reglas:
            alertas_count = db.query(AlertaGenerada).filter_by(regla_id=r.id).count()
            reglas_data.append({
                "id": r.id,
                "nombre_regla": r.nombre_regla,
                "activa": r.activa,
                "tipo_entidad": r.tipo_entidad,
                "filtros": r.filtros,
                "filtros_desc": describe_filtros(r.filtros),
                "email_destino": r.email_destino,
                "fecha_creacion": r.fecha_creacion,
                "fecha_ultima_ejecucion": r.fecha_ultima_ejecucion,
                "alertas_count": alertas_count,
            })

    return render_template("index.html", reglas=reglas_data)


@bp.route("/reglas/nueva", methods=["GET", "POST"])
@requires_auth
def nueva_regla():
    if request.method == "POST":
        nombre = request.form.get("nombre_regla", "").strip()
        tipo = request.form.get("tipo_entidad", "licitacion")
        email = request.form.get("email_destino", "").strip()
        filtros = _parse_filtros_form(request.form)

        errores = []
        if not nombre:
            errores.append("El nombre de la regla es obligatorio.")
        if not email:
            errores.append("El email de destino es obligatorio.")
        if tipo not in ("licitacion", "orden_compra", "compra_agil"):
            errores.append("Tipo de entidad inválido.")

        errores_filtros = validar_filtros(filtros)
        errores.extend(errores_filtros)

        if errores:
            for e in errores:
                flash(e, "error")
            return render_template(
                "regla_form.html",
                regla=None,
                form_data=request.form,
                regiones=REGIONES_CHILE,
            )

        with get_db() as db:
            regla = ReglaUsuario(
                nombre_regla=nombre,
                tipo_entidad=tipo,
                email_destino=email,
                filtros=filtros,
                activa=True,
            )
            db.add(regla)

        flash(f"Regla «{nombre}» creada correctamente.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template(
        "regla_form.html", regla=None, form_data={}, regiones=REGIONES_CHILE
    )


@bp.route("/reglas/<int:regla_id>/editar", methods=["GET", "POST"])
@requires_auth
def editar_regla(regla_id: int):
    with get_db() as db:
        regla = db.query(ReglaUsuario).filter_by(id=regla_id).first()
        if not regla:
            abort(404)

        if request.method == "POST":
            filtros = _parse_filtros_form(request.form)
            errores = validar_filtros(filtros)

            if errores:
                for e in errores:
                    flash(e, "error")
                return render_template(
                    "regla_form.html",
                    regla={"id": regla_id, "nombre_regla": regla.nombre_regla},
                    form_data=request.form,
                    regiones=REGIONES_CHILE,
                )

            regla.nombre_regla = request.form.get("nombre_regla", regla.nombre_regla).strip()
            regla.tipo_entidad = request.form.get("tipo_entidad", regla.tipo_entidad)
            regla.email_destino = request.form.get("email_destino", regla.email_destino).strip()
            regla.filtros = filtros

            flash("Regla actualizada correctamente.", "success")
            return redirect(url_for("dashboard.index"))

        regla_data = {
            "id": regla.id,
            "nombre_regla": regla.nombre_regla,
            "tipo_entidad": regla.tipo_entidad,
            "email_destino": regla.email_destino,
            "filtros": regla.filtros,
        }
        return render_template(
            "regla_form.html",
            regla=regla_data,
            form_data=regla.filtros or {},
            regiones=REGIONES_CHILE,
        )


@bp.route("/reglas/<int:regla_id>/toggle", methods=["POST"])
@requires_auth
def toggle_regla(regla_id: int):
    with get_db() as db:
        regla = db.query(ReglaUsuario).filter_by(id=regla_id).first()
        if not regla:
            abort(404)
        regla.activa = not regla.activa
        estado = "activada" if regla.activa else "desactivada"
        flash(f"Regla «{regla.nombre_regla}» {estado}.", "info")
    return redirect(url_for("dashboard.index"))


@bp.route("/reglas/<int:regla_id>/delete", methods=["POST"])
@requires_auth
def eliminar_regla(regla_id: int):
    with get_db() as db:
        regla = db.query(ReglaUsuario).filter_by(id=regla_id).first()
        if not regla:
            abort(404)
        nombre = regla.nombre_regla
        db.delete(regla)
        flash(f"Regla «{nombre}» eliminada.", "warning")
    return redirect(url_for("dashboard.index"))


@bp.route("/reglas/<int:regla_id>/run", methods=["POST"])
@requires_auth
def ejecutar_regla(regla_id: int):
    """Ejecuta manualmente una sola regla (sin esperar el scheduler)."""
    resultado = ejecutar_regla_manualmente(regla_id)
    if "error" in resultado:
        flash(f"Error: {resultado['error']}", "error")
    else:
        flash(
            f"Ejecutado: {resultado['alertas_generadas']} alertas generadas "
            f"sobre {resultado['entidades_evaluadas']} entidades evaluadas.",
            "success",
        )
    return redirect(url_for("dashboard.index"))


# ── Alertas ───────────────────────────────────────────────────────────────────

@bp.route("/alertas")
@requires_auth
def alertas():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25
    tipo_filtro = request.args.get("tipo", "")
    regla_filtro = request.args.get("regla_id", 0, type=int)

    with get_db() as db:
        q = (
            db.query(AlertaGenerada)
            .join(ReglaUsuario, AlertaGenerada.regla_id == ReglaUsuario.id)
            .order_by(AlertaGenerada.fecha_alerta.desc())
        )
        if tipo_filtro:
            q = q.filter(ReglaUsuario.tipo_entidad == tipo_filtro)
        if regla_filtro:
            q = q.filter(AlertaGenerada.regla_id == regla_filtro)

        total = q.count()
        alertas_page = q.offset((page - 1) * per_page).limit(per_page).all()

        alertas_data = []
        for a in alertas_page:
            alertas_data.append({
                "id": a.id,
                "regla_id": a.regla_id,
                "regla_nombre": a.regla.nombre_regla if a.regla else "—",
                "tipo": a.datos_resumen.get("tipo", ""),
                "codigo": a.entidad_id,
                "titulo": a.datos_resumen.get("titulo", "Sin título"),
                "monto_clp": a.datos_resumen.get("monto_clp"),
                "organismo": a.datos_resumen.get("organismo"),
                "fecha_publicacion": a.datos_resumen.get("fecha_publicacion"),
                "link_detalle": a.datos_resumen.get("link_detalle", "#"),
                "fecha_alerta": a.fecha_alerta,
                "enviado_email": a.enviado_email,
            })

        # Para filtros del formulario
        reglas_select = db.query(ReglaUsuario).all()
        reglas_select_data = [{"id": r.id, "nombre": r.nombre_regla} for r in reglas_select]

    total_paginas = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "alertas.html",
        alertas=alertas_data,
        page=page,
        total=total,
        total_paginas=total_paginas,
        per_page=per_page,
        tipo_filtro=tipo_filtro,
        regla_filtro=regla_filtro,
        reglas_select=reglas_select_data,
    )


@bp.route("/alertas/<int:alerta_id>")
@requires_auth
def alerta_detalle(alerta_id: int):
    with get_db() as db:
        alerta = db.query(AlertaGenerada).filter_by(id=alerta_id).first()
        if not alerta:
            abort(404)
        alerta.mostrado_dashboard = True
        data = {
            "id": alerta.id,
            "regla_id": alerta.regla_id,
            "regla_nombre": alerta.regla.nombre_regla if alerta.regla else "—",
            "tipo": alerta.datos_resumen.get("tipo", ""),
            "codigo": alerta.entidad_id,
            "datos": alerta.datos_resumen,
            "fecha_alerta": alerta.fecha_alerta,
            "enviado_email": alerta.enviado_email,
        }
    return render_template("alerta_detalle.html", alerta=data)


# ── Analytics Dashboard ───────────────────────────────────────────────────────

@bp.route("/analytics")
@requires_auth
def analytics_dashboard():
    return render_template("analytics.html", regiones=REGIONES_CHILE)


# ── Analytics API (JSON) ──────────────────────────────────────────────────────

def _api_params() -> tuple[int, str | None]:
    """Extrae parámetros comunes de las rutas de analytics."""
    dias = request.args.get("dias", 90, type=int)
    tipo = request.args.get("tipo") or None
    return dias, tipo


@bp.route("/api/analytics/resumen")
@requires_auth
def api_resumen():
    dias, _ = _api_params()
    return jsonify(analytics.resumen_ejecutivo(dias=dias))


@bp.route("/api/analytics/categorias")
@requires_auth
def api_categorias():
    dias, tipo = _api_params()
    limit = request.args.get("limit", 15, type=int)
    return jsonify(analytics.top_categorias(limit=limit, tipo=tipo, dias=dias))


@bp.route("/api/analytics/organismos")
@requires_auth
def api_organismos():
    dias, tipo = _api_params()
    limit = request.args.get("limit", 15, type=int)
    return jsonify(analytics.top_organismos(limit=limit, tipo=tipo, dias=dias))


@bp.route("/api/analytics/regional")
@requires_auth
def api_regional():
    dias, tipo = _api_params()
    return jsonify(analytics.distribucion_regional(tipo=tipo, dias=dias))


@bp.route("/api/analytics/temporal")
@requires_auth
def api_temporal():
    dias, tipo = _api_params()
    meses = max(3, dias // 30)
    return jsonify(analytics.tendencia_temporal(tipo=tipo, meses=meses))


@bp.route("/api/analytics/diario")
@requires_auth
def api_diario():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(analytics.tendencia_diaria(dias=dias))


@bp.route("/api/analytics/estados")
@requires_auth
def api_estados():
    dias, tipo = _api_params()
    tipo_real = tipo or "orden_compra"
    return jsonify(analytics.analisis_estados(tipo=tipo_real, dias=dias))


@bp.route("/api/analytics/nichos")
@requires_auth
def api_nichos():
    dias, _ = _api_params()
    limit = request.args.get("limit", 10, type=int)
    monto_min = request.args.get("monto_min", 500_000, type=int)
    return jsonify(analytics.nichos_mercado(dias=dias, monto_min=monto_min, limit=limit))


@bp.route("/api/analytics/estacionalidad")
@requires_auth
def api_estacionalidad():
    return jsonify(analytics.estacionalidad_mensual())


@bp.route("/api/analytics/reporte")
@requires_auth
def api_reporte_completo():
    dias = request.args.get("dias", 90, type=int)
    return jsonify(analytics.reporte_completo(dias=dias))


# ── Status del sistema ────────────────────────────────────────────────────────

@bp.route("/api/status")
@requires_auth
def api_status():
    with get_db() as db:
        total_reglas = db.query(ReglaUsuario).count()
        reglas_activas = db.query(ReglaUsuario).filter_by(activa=True).count()
        total_alertas = db.query(AlertaGenerada).count()
        total_snapshots = db.query(LicitacionSnapshot).count()
        ultima_sinc = db.query(LicitacionSnapshot.fecha_sincronizacion).order_by(
            LicitacionSnapshot.fecha_sincronizacion.desc()
        ).first()

    from app.scheduler import _scheduler
    next_run = None
    if _scheduler and _scheduler.running:
        job = _scheduler.get_job("ciclo_diario")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {
            "total_reglas": total_reglas,
            "reglas_activas": reglas_activas,
            "total_alertas": total_alertas,
            "snapshots": total_snapshots,
            "ultima_sincronizacion": ultima_sinc[0].isoformat() if ultima_sinc and ultima_sinc[0] else None,
        },
        "scheduler": {
            "corriendo": bool(_scheduler and _scheduler.running),
            "proxima_ejecucion": next_run,
        },
    })
