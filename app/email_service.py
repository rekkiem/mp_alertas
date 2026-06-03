"""
app/email_service.py — Servicio de envío de correos electrónicos.

Características:
  • Cola thread-safe con hilo worker dedicado (no bloquea el scheduler)
  • Plantilla HTML profesional con datos de la entidad
  • Correo de resumen diario para el administrador
  • Retry simple ante fallos SMTP transitorios
"""
from __future__ import annotations

import logging
import queue
import smtplib
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)


# ── Queue global de correos ────────────────────────────────────────────────────

_email_queue: queue.Queue = queue.Queue(maxsize=500)
_worker_thread: Optional[threading.Thread] = None
_shutdown_event = threading.Event()


# ── Conexión SMTP ─────────────────────────────────────────────────────────────

def _crear_conexion_smtp() -> smtplib.SMTP:
    """Abre y autentica conexión SMTP desde variables de entorno."""
    smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
    smtp.ehlo()
    if settings.SMTP_USE_TLS:
        smtp.starttls()
        smtp.ehlo()
    if settings.SMTP_USER and settings.SMTP_PASSWORD:
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
    return smtp


def _enviar_raw(msg: MIMEMultipart, destinatarios: List[str], max_retries: int = 3) -> bool:
    """
    Envía un mensaje MIME con reintentos y backoff.
    Retorna True si el envío fue exitoso.
    """
    for attempt in range(max_retries):
        try:
            smtp = _crear_conexion_smtp()
            smtp.sendmail(settings.SMTP_FROM, destinatarios, msg.as_string())
            smtp.quit()
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error("Error de autenticación SMTP: %s", e)
            return False          # No reintentar, credenciales malas
        except Exception as e:
            wait = 2 ** attempt * 5
            logger.warning("Error SMTP (intento %d/%d): %s. Reintentando en %ss…", attempt + 1, max_retries, e, wait)
            if attempt < max_retries - 1:
                time.sleep(wait)

    logger.error("No se pudo enviar correo tras %d intentos.", max_retries)
    return False


# ── Plantilla HTML para alertas ───────────────────────────────────────────────

def _html_alerta(regla_nombre: str, entidad: Dict) -> str:
    monto = entidad.get("monto_clp")
    monto_str = f"${monto:,.0f} CLP" if monto else "No especificado"
    fecha_pub = entidad.get("fecha_publicacion") or "No especificada"
    fecha_cierre = entidad.get("fecha_cierre") or "No especificada"
    link = entidad.get("link_detalle", "#")
    region = entidad.get("nombre_region") or f"Región {entidad.get('region')}" or "No especificada"
    organismo = entidad.get("organismo") or "No especificado"
    tipo_label = {
        "licitacion": "🏛 Licitación",
        "orden_compra": "📋 Orden de Compra",
        "compra_agil": "⚡ Compra Ágil",
    }.get(entidad.get("tipo", ""), "📌 Oportunidad")

    app_url = settings.APP_BASE_URL.rstrip("/")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Nueva alerta ChileCompra</title>
</head>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;padding:32px 0;">
    <tr><td align="center">
      <table width="620" cellpadding="0" cellspacing="0" style="background:#161b22;border-radius:12px;overflow:hidden;border:1px solid #30363d;">

        <!-- HEADER -->
        <tr>
          <td style="background:linear-gradient(135deg,#e6a817 0%,#f59e0b 100%);padding:28px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="font-size:11px;color:#78350f;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Mercado Público · Alerta</span>
                  <h1 style="margin:8px 0 0;font-size:22px;color:#1c1917;font-weight:800;line-height:1.3;">
                    Nueva coincidencia detectada
                  </h1>
                </td>
                <td align="right" style="font-size:28px;">{tipo_label.split()[0]}</td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- REGLA -->
        <tr>
          <td style="padding:20px 32px 0;border-bottom:1px solid #21262d;">
            <p style="margin:0 0 4px;font-size:11px;color:#8b949e;letter-spacing:1px;text-transform:uppercase;">Tu regla</p>
            <p style="margin:0 0 16px;font-size:16px;color:#e6a817;font-weight:700;">{regla_nombre}</p>
          </td>
        </tr>

        <!-- CUERPO PRINCIPAL -->
        <tr>
          <td style="padding:24px 32px;">

            <!-- Título -->
            <h2 style="margin:0 0 8px;font-size:18px;color:#e6edf3;font-weight:700;line-height:1.4;">
              {entidad.get('titulo', 'Sin título')}
            </h2>
            <p style="margin:0 0 20px;font-size:12px;color:#8b949e;font-family:monospace;">
              Código: {entidad.get('codigo', 'N/D')} &nbsp;·&nbsp; {tipo_label}
            </p>

            <!-- Grid de datos -->
            <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:8px;overflow:hidden;border:1px solid #21262d;">
              <tr style="background:#1c2128;">
                <td style="padding:12px 16px;border-right:1px solid #21262d;width:50%;">
                  <p style="margin:0 0 2px;font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Monto estimado</p>
                  <p style="margin:0;font-size:18px;color:#3fb950;font-weight:800;font-family:monospace;">{monto_str}</p>
                </td>
                <td style="padding:12px 16px;">
                  <p style="margin:0 0 2px;font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Organismo</p>
                  <p style="margin:0;font-size:13px;color:#e6edf3;font-weight:600;">{organismo}</p>
                </td>
              </tr>
              <tr style="border-top:1px solid #21262d;">
                <td style="padding:12px 16px;border-right:1px solid #21262d;">
                  <p style="margin:0 0 2px;font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Publicación</p>
                  <p style="margin:0;font-size:13px;color:#e6edf3;">{fecha_pub}</p>
                </td>
                <td style="padding:12px 16px;">
                  <p style="margin:0 0 2px;font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Cierre</p>
                  <p style="margin:0;font-size:13px;color:{'#f85149' if fecha_cierre != 'No especificada' else '#e6edf3'};">{fecha_cierre}</p>
                </td>
              </tr>
              <tr style="border-top:1px solid #21262d;background:#1c2128;">
                <td colspan="2" style="padding:12px 16px;">
                  <p style="margin:0 0 2px;font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Región</p>
                  <p style="margin:0;font-size:13px;color:#e6edf3;">{region}</p>
                </td>
              </tr>
            </table>

            <!-- CTA -->
            <div style="text-align:center;margin-top:28px;">
              <a href="{link}" style="display:inline-block;background:#e6a817;color:#1c1917;text-decoration:none;font-weight:800;font-size:14px;padding:14px 32px;border-radius:8px;letter-spacing:0.5px;">
                Ver detalle en Mercado Público →
              </a>
            </div>

            <!-- Link desactivar regla -->
            <p style="text-align:center;margin:20px 0 0;font-size:12px;color:#8b949e;">
              ¿Demasiadas alertas?
              <a href="{app_url}/reglas" style="color:#8b949e;">Ver y gestionar tus reglas</a>
            </p>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="padding:16px 32px;background:#0d1117;border-top:1px solid #21262d;">
            <p style="margin:0;font-size:11px;color:#484f58;text-align:center;">
              Sistema de Alertas Mercado Público · Generado automáticamente ·
              <a href="{app_url}" style="color:#484f58;">Dashboard</a>
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _html_resumen_admin(stats: Dict) -> str:
    """HTML para el correo de resumen diario al administrador."""
    filas = ""
    for r in stats.get("por_regla", []):
        filas += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:#e6edf3;">{r['regla']}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:#3fb950;text-align:center;font-weight:700;">{r['alertas']}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:#8b949e;text-align:center;">{r['estado']}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"></head>
<body style="background:#0d1117;font-family:'Segoe UI',sans-serif;padding:32px;">
  <div style="max-width:600px;margin:0 auto;background:#161b22;border-radius:12px;overflow:hidden;border:1px solid #30363d;">
    <div style="background:#21262d;padding:24px 32px;border-bottom:1px solid #30363d;">
      <h1 style="margin:0;font-size:20px;color:#e6edf3;">📊 Resumen Diario del Scheduler</h1>
      <p style="margin:8px 0 0;color:#8b949e;font-size:13px;">{stats.get('fecha', '')}</p>
    </div>
    <div style="padding:24px 32px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
        <tr>
          <td style="text-align:center;padding:16px;background:#1c2128;border-radius:8px;margin:4px;">
            <p style="margin:0;font-size:32px;font-weight:800;color:#e6a817;">{stats.get('total_alertas', 0)}</p>
            <p style="margin:4px 0 0;font-size:12px;color:#8b949e;">Alertas generadas</p>
          </td>
          <td width="12"></td>
          <td style="text-align:center;padding:16px;background:#1c2128;border-radius:8px;">
            <p style="margin:0;font-size:32px;font-weight:800;color:#3fb950;">{stats.get('reglas_evaluadas', 0)}</p>
            <p style="margin:4px 0 0;font-size:12px;color:#8b949e;">Reglas evaluadas</p>
          </td>
          <td width="12"></td>
          <td style="text-align:center;padding:16px;background:#1c2128;border-radius:8px;">
            <p style="margin:0;font-size:32px;font-weight:800;color:{'#f85149' if stats.get('errores',0) > 0 else '#e6edf3'};">{stats.get('errores', 0)}</p>
            <p style="margin:4px 0 0;font-size:12px;color:#8b949e;">Errores</p>
          </td>
        </tr>
      </table>

      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #21262d;border-radius:8px;overflow:hidden;">
        <tr style="background:#1c2128;">
          <th style="padding:10px 12px;text-align:left;font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Regla</th>
          <th style="padding:10px 12px;text-align:center;font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Alertas</th>
          <th style="padding:10px 12px;text-align:center;font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;">Estado</th>
        </tr>
        {filas}
      </table>
    </div>
  </div>
</body></html>"""


# ── Funciones públicas ─────────────────────────────────────────────────────────

def encolar_alerta(regla_nombre: str, entidad: Dict, destinatarios: List[str]) -> None:
    """
    Encola un correo de alerta para envío asíncrono.
    No bloquea al llamador.
    """
    try:
        _email_queue.put_nowait({
            "tipo": "alerta",
            "regla_nombre": regla_nombre,
            "entidad": entidad,
            "destinatarios": destinatarios,
        })
        logger.debug("Correo encolado para: %s", destinatarios)
    except queue.Full:
        logger.error("Cola de correos llena. Descartando alerta para %s.", destinatarios)


def encolar_resumen_admin(stats: Dict) -> None:
    """Encola el resumen diario para el administrador."""
    if not settings.ADMIN_EMAIL:
        return
    try:
        _email_queue.put_nowait({
            "tipo": "resumen_admin",
            "stats": stats,
            "destinatarios": [settings.ADMIN_EMAIL],
        })
    except queue.Full:
        logger.error("Cola de correos llena. Descartando resumen admin.")


def _worker_loop() -> None:
    """Loop del hilo worker que procesa la cola de correos."""
    logger.info("Worker de correos iniciado.")
    while not _shutdown_event.is_set():
        try:
            item = _email_queue.get(timeout=5)
        except queue.Empty:
            continue

        try:
            if item["tipo"] == "alerta":
                _procesar_alerta(item)
            elif item["tipo"] == "resumen_admin":
                _procesar_resumen_admin(item)
        except Exception as e:
            logger.exception("Error procesando correo de la cola: %s", e)
        finally:
            _email_queue.task_done()

    logger.info("Worker de correos detenido.")


def _procesar_alerta(item: Dict) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        logger.warning("SMTP no configurado. Correo de alerta omitido.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[ChileCompra] Nueva coincidencia: {item['regla_nombre']}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(item["destinatarios"])

    html = _html_alerta(item["regla_nombre"], item["entidad"])
    msg.attach(MIMEText(html, "html", "utf-8"))

    exito = _enviar_raw(msg, item["destinatarios"])
    if exito:
        logger.info("Alerta enviada a %s", item["destinatarios"])


def _procesar_resumen_admin(item: Dict) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[ChileCompra] Resumen Diario Scheduler — {item['stats'].get('fecha', '')}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(item["destinatarios"])

    html = _html_resumen_admin(item["stats"])
    msg.attach(MIMEText(html, "html", "utf-8"))

    _enviar_raw(msg, item["destinatarios"])


def iniciar_worker() -> None:
    """Inicia el hilo worker de correos. Llamar una sola vez al arrancar la app."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _shutdown_event.clear()
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="email-worker")
    _worker_thread.start()
    logger.info("Worker de correos arrancado (thread id=%s).", _worker_thread.ident)


def detener_worker(timeout: int = 10) -> None:
    """Señaliza el shutdown y espera que el hilo termine."""
    _shutdown_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=timeout)
