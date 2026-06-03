"""
app/scheduler.py — Daemon diario con APScheduler.

Estrategia de ejecución:
  1. Sincronización incremental: descarga UNA VEZ todos los datos del día
     por tipo (licitacion / orden_compra / compra_agil) y los persiste
     en licitaciones_snapshot.
  2. Evaluación en memoria: aplica todas las reglas activas sobre el
     conjunto ya descargado → reduce llamadas API a N tipos × 1 en vez
     de N tipos × M reglas.
  3. Deduplicación: no genera alertas duplicadas dentro de ALERT_DEDUP_DAYS.
  4. Resumen al administrador al finalizar cada ciclo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from app.database import get_db, SessionLocal
from app.models import ReglaUsuario, AlertaGenerada, LicitacionSnapshot
from app.api_client import MercadoPublicoClient, QuotaExhaustedException, ApiClientException
from app.normalizer import normalizar_entidad, datos_resumen
from app.filter_engine import evaluar_regla, FiltroInvalidoError
from app.email_service import encolar_alerta, encolar_resumen_admin

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


# ── Sincronización incremental ─────────────────────────────────────────────────

def _sincronizar_tipo(
    client: MercadoPublicoClient,
    tipo: str,
    fecha_desde: datetime,
) -> List[Dict]:
    """
    Descarga y normaliza todas las entidades del tipo dado publicadas
    desde fecha_desde. Persiste en licitaciones_snapshot y retorna la lista.
    """
    fecha_api = MercadoPublicoClient.to_api_date(fecha_desde)
    entidades_norm: List[Dict] = []

    logger.info("[SYNC] Tipo=%s desde %s", tipo, fecha_api)

    if tipo == "licitacion":
        iterator = client.iter_licitaciones(fecha_desde=fecha_api)
    elif tipo == "orden_compra":
        iterator = client.iter_ordenes_compra(fecha_desde=fecha_api)
    elif tipo == "compra_agil":
        iterator = client.iter_oportunidades(fecha_desde=fecha_api)
    else:
        logger.error("Tipo desconocido en sincronización: %s", tipo)
        return []

    db = SessionLocal()
    try:
        count_new = 0
        count_upd = 0

        for raw in iterator:
            normalizado = normalizar_entidad(raw, tipo)
            if not normalizado:
                continue

            entidades_norm.append(normalizado)
            codigo = normalizado.get("codigo", "")
            if not codigo:
                continue

            # Upsert en licitaciones_snapshot
            try:
                snap = db.query(LicitacionSnapshot).filter_by(codigo=codigo).first()
                fecha_pub_dt = None
                if normalizado.get("fecha_publicacion"):
                    try:
                        fecha_pub_dt = datetime.fromisoformat(normalizado["fecha_publicacion"])
                    except ValueError:
                        pass

                if snap:
                    snap.datos = normalizado
                    snap.estado = normalizado.get("estado")
                    snap.region = normalizado.get("region")
                    snap.monto_clp = normalizado.get("monto_clp")
                    snap.fecha_publicacion = fecha_pub_dt
                    snap.fecha_sincronizacion = datetime.now(timezone.utc).replace(tzinfo=None)
                    count_upd += 1
                else:
                    snap = LicitacionSnapshot(
                        codigo=codigo,
                        tipo=tipo,
                        datos=normalizado,
                        estado=normalizado.get("estado"),
                        region=normalizado.get("region"),
                        monto_clp=normalizado.get("monto_clp"),
                        fecha_publicacion=fecha_pub_dt,
                    )
                    db.add(snap)
                    count_new += 1

                # Commit por lotes de 100
                if (count_new + count_upd) % 100 == 0:
                    db.commit()

            except Exception as e:
                logger.warning("Error guardando snapshot código=%s: %s", codigo, e)
                db.rollback()

        db.commit()
        logger.info(
            "[SYNC] Tipo=%s → %d nuevos, %d actualizados (total=%d)",
            tipo, count_new, count_upd, count_new + count_upd,
        )

    except (QuotaExhaustedException, ApiClientException) as e:
        logger.error("[SYNC] Error API tipo=%s: %s", tipo, e)
        raise
    finally:
        db.close()

    return entidades_norm


# ── Evaluación de reglas sobre datos ya descargados ───────────────────────────

def _evaluar_reglas_sobre_entidades(
    reglas: List[ReglaUsuario],
    entidades: List[Dict],
    tipo: str,
) -> Tuple[int, int]:
    """
    Evalúa todas las reglas activas del tipo dado sobre la lista de entidades.
    Retorna (alertas_generadas, emails_encolados).
    """
    if not reglas or not entidades:
        return 0, 0

    reglas_tipo = [r for r in reglas if r.tipo_entidad == tipo]
    if not reglas_tipo:
        return 0, 0

    alertas_gen = 0
    emails_env = 0
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    limite_dedup = ahora - timedelta(days=settings.ALERT_DEDUP_DAYS)

    with get_db() as db:
        # Pre-cargar alertas existentes en período de dedup para comparación rápida
        alertas_recientes = set(
            (a.regla_id, a.entidad_id)
            for a in db.query(AlertaGenerada.regla_id, AlertaGenerada.entidad_id)
            .filter(AlertaGenerada.fecha_alerta >= limite_dedup)
            .all()
        )

        for regla in reglas_tipo:
            regla_alertas = 0
            for entidad in entidades:
                try:
                    if not evaluar_regla(regla, entidad):
                        continue
                except FiltroInvalidoError as e:
                    logger.error("Filtros inválidos en regla id=%d: %s", regla.id, e)
                    break  # Saltar toda la regla si los filtros son incorrectos

                codigo = entidad.get("codigo", "")
                clave = (regla.id, codigo)

                if clave in alertas_recientes:
                    logger.debug("Alerta deduplicada: regla=%d, entidad=%s", regla.id, codigo)
                    continue

                # Crear alerta
                resumen = datos_resumen(entidad)
                alerta = AlertaGenerada(
                    regla_id=regla.id,
                    entidad_id=codigo,
                    datos_resumen=resumen,
                    fecha_alerta=ahora,
                    enviado_email=False,
                    mostrado_dashboard=True,
                )
                db.add(alerta)
                alertas_recientes.add(clave)
                alertas_gen += 1
                regla_alertas += 1

                # Encolar correo
                try:
                    encolar_alerta(
                        regla_nombre=regla.nombre_regla,
                        entidad=entidad,
                        destinatarios=regla.emails_lista,
                    )
                    alerta.enviado_email = True
                    emails_env += 1
                except Exception as e:
                    logger.error("Error encolando correo regla=%d: %s", regla.id, e)

            logger.info(
                "Regla '%s' (id=%d): %d nuevas alertas sobre %d entidades",
                regla.nombre_regla, regla.id, regla_alertas, len(entidades),
            )

    return alertas_gen, emails_env


# ── Tarea principal del scheduler ─────────────────────────────────────────────

def ejecutar_ciclo_completo() -> Dict:
    """
    Tarea principal. Se ejecuta diariamente (o manualmente).
    Retorna un dict con estadísticas del ciclo para el resumen.
    """
    inicio = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("=" * 60)
    logger.info("INICIO CICLO SCHEDULER: %s", inicio.isoformat())
    logger.info("=" * 60)

    stats = {
        "fecha": inicio.strftime("%Y-%m-%d %H:%M UTC"),
        "reglas_evaluadas": 0,
        "total_alertas": 0,
        "emails_encolados": 0,
        "errores": 0,
        "por_regla": [],
        "entidades_sincronizadas": {},
    }

    client = MercadoPublicoClient()
    fecha_desde = inicio - timedelta(hours=25)   # Últimas ~25 h (buffer de solapamiento)

    # ── 1. Cargar reglas activas ───────────────────────────────────────────────
    with get_db() as db:
        reglas = db.query(ReglaUsuario).filter(ReglaUsuario.activa == True).all()
        # Detach de la sesión para usar en hilo del scheduler
        db.expunge_all()

    if not reglas:
        logger.info("No hay reglas activas. Ciclo terminado.")
        return stats

    stats["reglas_evaluadas"] = len(reglas)
    tipos_requeridos = set(r.tipo_entidad for r in reglas)
    logger.info("Reglas activas: %d | Tipos requeridos: %s", len(reglas), tipos_requeridos)

    # ── 2. Sincronización incremental por tipo ────────────────────────────────
    entidades_por_tipo: Dict[str, List[Dict]] = {}

    for tipo in tipos_requeridos:
        try:
            entidades = _sincronizar_tipo(client, tipo, fecha_desde)
            entidades_por_tipo[tipo] = entidades
            stats["entidades_sincronizadas"][tipo] = len(entidades)
        except QuotaExhaustedException:
            logger.critical("Cuota de API agotada. Deteniendo ciclo.")
            stats["errores"] += 1
            break
        except Exception as e:
            logger.error("Error sincronizando tipo=%s: %s", tipo, e)
            stats["errores"] += 1
            entidades_por_tipo[tipo] = []   # Continuar con otras reglas

    # ── 3. Evaluación de reglas sobre datos sincronizados ─────────────────────
    for tipo, entidades in entidades_por_tipo.items():
        try:
            alertas, emails = _evaluar_reglas_sobre_entidades(reglas, entidades, tipo)
            stats["total_alertas"] += alertas
            stats["emails_encolados"] += emails
        except Exception as e:
            logger.exception("Error evaluando reglas para tipo=%s: %s", tipo, e)
            stats["errores"] += 1

    # ── 4. Actualizar fecha_ultima_ejecucion de todas las reglas ──────────────
    with get_db() as db:
        for regla in db.query(ReglaUsuario).filter(ReglaUsuario.activa == True).all():
            regla.fecha_ultima_ejecucion = inicio

    # ── 5. Resumen por regla para el correo de admin ──────────────────────────
    with get_db() as db:
        for regla in reglas:
            n_alertas = db.query(AlertaGenerada).filter(
                AlertaGenerada.regla_id == regla.id,
                AlertaGenerada.fecha_alerta >= inicio,
            ).count()
            stats["por_regla"].append({
                "regla": regla.nombre_regla,
                "alertas": n_alertas,
                "estado": "OK" if n_alertas >= 0 else "Error",
            })

    # ── 6. Limpiar snapshots viejos (> 90 días) ───────────────────────────────
    try:
        _purgar_snapshots_viejos(dias=90)
    except Exception as e:
        logger.warning("Error purgando snapshots: %s", e)

    # ── 7. Enviar resumen al admin ────────────────────────────────────────────
    encolar_resumen_admin(stats)

    duracion = (datetime.now(timezone.utc).replace(tzinfo=None) - inicio).total_seconds()
    logger.info("FIN CICLO. Duración: %.1fs | Alertas: %d | Errores: %d",
                duracion, stats["total_alertas"], stats["errores"])

    return stats


def _purgar_snapshots_viejos(dias: int = 90) -> None:
    """Elimina snapshots más viejos que N días para controlar el tamaño de la BD."""
    with get_db() as db:
        limite = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
        eliminados = (
            db.query(LicitacionSnapshot)
            .filter(LicitacionSnapshot.fecha_sincronizacion < limite)
            .delete()
        )
        if eliminados:
            logger.info("Purgados %d snapshots viejos (> %d días).", eliminados, dias)


# ── Ejecución de una sola regla (para el botón "Ejecutar ahora" del dashboard) ─

def ejecutar_regla_manualmente(regla_id: int) -> Dict:
    """
    Ejecuta una sola regla bajo demanda.
    Primero intenta reusar datos del snapshot reciente (< 2 h);
    si no hay, los descarga de la API.
    """
    with get_db() as db:
        regla = db.query(ReglaUsuario).filter_by(id=regla_id).first()
        if not regla:
            return {"error": f"Regla {regla_id} no encontrada"}
        db.expunge(regla)

    logger.info("Ejecución manual: regla '%s' (id=%d)", regla.nombre_regla, regla.id)

    tipo = regla.tipo_entidad
    client = MercadoPublicoClient()
    fecha_desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)

    # Reusar snapshot reciente si existe (< 2 h)
    with get_db() as db:
        limite_cache = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        cache_count = db.query(LicitacionSnapshot).filter(
            LicitacionSnapshot.tipo == tipo,
            LicitacionSnapshot.fecha_sincronizacion >= limite_cache,
        ).count()

    if cache_count > 0:
        logger.info("Reutilizando %d entidades del snapshot (< 2 h).", cache_count)
        with get_db() as db:
            snaps = db.query(LicitacionSnapshot).filter(
                LicitacionSnapshot.tipo == tipo,
                LicitacionSnapshot.fecha_publicacion >= fecha_desde,
            ).all()
            entidades = [s.datos for s in snaps]
    else:
        try:
            entidades = _sincronizar_tipo(client, tipo, fecha_desde)
        except Exception as e:
            return {"error": f"Error descargando datos: {e}"}

    alertas, emails = _evaluar_reglas_sobre_entidades([regla], entidades, tipo)

    return {
        "regla_id": regla_id,
        "regla_nombre": regla.nombre_regla,
        "entidades_evaluadas": len(entidades),
        "alertas_generadas": alertas,
        "emails_encolados": emails,
    }


# ── Gestión del scheduler ──────────────────────────────────────────────────────

def iniciar_scheduler() -> BackgroundScheduler:
    """Crea e inicia el BackgroundScheduler con la tarea diaria."""
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("Scheduler ya está corriendo.")
        return _scheduler

    tz = pytz.timezone(settings.TIMEZONE)
    _scheduler = BackgroundScheduler(timezone=tz)

    trigger = CronTrigger(
        hour=settings.SCHEDULER_HOUR,
        minute=settings.SCHEDULER_MINUTE,
        timezone=tz,
    )

    _scheduler.add_job(
        func=ejecutar_ciclo_completo,
        trigger=trigger,
        id="ciclo_diario",
        name="Ciclo diario Mercado Público",
        replace_existing=True,
        misfire_grace_time=3_600,   # Tolera hasta 1 h de retraso
    )

    _scheduler.start()
    logger.info(
        "Scheduler iniciado. Próxima ejecución: %s",
        _scheduler.get_job("ciclo_diario").next_run_time,
    )
    return _scheduler


def detener_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido.")
