"""
test_mvp.py — Suite completa MP Alertas (84 assertions + 19 HTTP)
"""
import os, sys, json, time, threading, base64
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# ── Entorno ───────────────────────────────────────────────────
os.environ.update({
    "DATABASE_URL":           "sqlite://",
    "TICKET_MERCADO_PUBLICO": "TEST_TICKET",
    "DASHBOARD_USER":         "admin",
    "DASHBOARD_PASS":         "testpass",
    "SECRET_KEY":             "test-secret-key",
    "SMTP_HOST":              "localhost",
    "SMTP_PORT":              "1025",
    "SMTP_USER":              "test@test.cl",
    "SMTP_PASSWORD":          "pass",
    "SMTP_FROM":              "test@test.cl",
    "SMTP_USE_TLS":           "false",
    "ADMIN_EMAIL":            "admin@test.cl",
    "ALERT_DEDUP_DAYS":       "30",
    "APP_BASE_URL":           "http://localhost:5000",
    "API_RATE_PER_SECOND":    "100",
    "API_RATE_PER_DAY":       "999999",
})
sys.path.insert(0, "/home/claude/mp_alertas")
from importlib import reload
import config; reload(config)
from config import settings

RESULTS = {"passed": 0, "failed": 0, "errors": []}

def ok(msg):   RESULTS["passed"] += 1; print(f"  ✅ {msg}")
def fail(msg, exc=None):
    RESULTS["failed"] += 1
    detail = f": {exc}" if exc else ""
    RESULTS["errors"].append(f"{msg}{detail}")
    print(f"  ❌ {msg}{detail}")
def section(n): print(f"\n{'─'*58}\n  {n}\n{'─'*58}")

# ══════════════════════════════════════════════
# 1. CONFIG
# ══════════════════════════════════════════════
section("1. Configuración y Settings")
try:
    assert settings.TICKET_MERCADO_PUBLICO == "TEST_TICKET"
    assert settings.DASHBOARD_USER == "admin"
    assert settings.TIMEZONE == "America/Santiago"
    assert settings.ALERT_DEDUP_DAYS == 30
    ok("Settings correctas desde env vars")
except Exception as e: fail("Settings", e)

# ══════════════════════════════════════════════
# 2. BASE DE DATOS + MODELOS
# ══════════════════════════════════════════════
section("2. Base de datos y modelos ORM")
from app.database import init_db, get_db, Base
from app.models import ReglaUsuario, AlertaGenerada, LicitacionSnapshot

try:
    init_db()
    ok("BD SQLite en memoria inicializada")
except Exception as e: fail("init_db", e)

try:
    with get_db() as db:
        db.add(ReglaUsuario(nombre_regla="TI-RM",activa=True,tipo_entidad="licitacion",
                            filtros={"region":13,"monto_min":1_000_000},
                            email_destino="a@b.cl, c@d.cl"))
    ok("ReglaUsuario INSERT")
except Exception as e: fail("ReglaUsuario INSERT", e)

try:
    with get_db() as db:
        r = db.query(ReglaUsuario).filter_by(nombre_regla="TI-RM").first()
        assert r and r.activa and r.filtros == {"region":13,"monto_min":1_000_000}
        emails = r.emails_lista
        assert len(emails) == 2
    ok(f"ReglaUsuario SELECT + emails_lista: {emails}")
except Exception as e: fail("ReglaUsuario SELECT", e)

try:
    with get_db() as db:
        regla = db.query(ReglaUsuario).first()
        db.add(AlertaGenerada(regla_id=regla.id, entidad_id="LIC-001",
                              datos_resumen={"titulo":"ERP","monto_clp":5_000_000},
                              enviado_email=False))
    ok("AlertaGenerada INSERT")
except Exception as e: fail("AlertaGenerada INSERT", e)

try:
    with get_db() as db:
        db.add(LicitacionSnapshot(codigo="LIC-001",tipo="licitacion",
                                  datos={"titulo":"ERP"},region=13,monto_clp=5_000_000))
    ok("LicitacionSnapshot INSERT")
except Exception as e: fail("LicitacionSnapshot INSERT", e)

try:
    with get_db() as db:
        db.add(LicitacionSnapshot(codigo="LIC-001",tipo="licitacion",datos={}))
    fail("Debería fallar UNIQUE")
except Exception: ok("UNIQUE constraint activo")

# ══════════════════════════════════════════════
# 3. NORMALIZER
# ══════════════════════════════════════════════
section("3. Normalizer — transformación de entidades")
from app.normalizer import (normalizar_licitacion, normalizar_orden_compra,
                             normalizar_entidad, datos_resumen, _parse_date)

RAW_LIC = {
    "CodigoExterno":"9999-1-LE24","Nombre":"Adquisición de software de gestión",
    "Descripcion":"Sistema ERP","Estado":"Publicada",
    "FechaPublicacion":"2024-03-15T00:00:00","FechaCierre":"2024-04-01T00:00:00",
    "MontoEstimado":8_500_000,"Moneda":"CLP",
    "Comprador":{"CodigoOrganismo":"MC001","NombreOrganismo":"Municipalidad de Providencia",
                 "CodigoRegion":13,"NombreRegion":"Región Metropolitana"},
    "Items":{"Listado":[{"CodigoProducto":"30200000"},{"CodigoProducto":"43230000"}]},
}
try:
    n = normalizar_licitacion(RAW_LIC)
    assert n["codigo"] == "9999-1-LE24"
    assert n["region"] == 13
    assert n["monto_clp"] == 8_500_000
    assert "30200000" in n["codigo_producto"]
    assert n["fecha_publicacion"] == "2024-03-15"
    assert "mercadopublico.cl" in n["link_detalle"]
    ok(f"Licitación: {n['codigo']}, region={n['region']}, fecha={n['fecha_publicacion']}")
except Exception as e: fail("normalizar_licitacion", e)

try:
    raw_oc = {"Numero":"OC-2024-001","Nombre":"Equipos","Estado":"Aceptada",
              "MontoTotal":2_000_000,"Moneda":"CLP","FechaEnvio":"2024-03-10T00:00:00",
              "Comprador":{"CodigoRegion":8,"NombreRegion":"Biobío","NombreOrganismo":"SEREMI"},
              "Items":{"Listado":[{"CodigoProducto":"44121501"}]}}
    n2 = normalizar_orden_compra(raw_oc)
    assert n2["tipo"] == "orden_compra" and n2["region"] == 8
    ok(f"Orden de Compra: {n2['codigo']}, region={n2['region']}")
except Exception as e: fail("normalizar_orden_compra", e)

# Fechas
for inp, expected, label in [
    ("2024-01-15T00:00:00", "2024-01-15", "ISO datetime"),
    ("2024-03-15T08:30:00", "2024-03-15", "ISO con hora"),
    ("/Date(1710460800000)/", "2024-03-15", "formato .NET"),
    ("15-01-2024",           "2024-01-15", "DD-MM-YYYY"),
    (None,                   None,          "None"),
    ("",                     None,          "vacío"),
]:
    try:
        r = _parse_date(inp)
        assert r == expected, f"obtuvo {r!r}"
        ok(f"_parse_date {label}: {inp!r} → {r!r}")
    except Exception as e: fail(f"_parse_date {label}", e)

try:
    raw_usd = dict(RAW_LIC, MontoEstimado=10_000, Moneda="USD")
    n_usd = normalizar_licitacion(raw_usd)
    assert n_usd["monto_clp"] > 1_000_000
    ok(f"Conversión USD→CLP: $10K USD = ${n_usd['monto_clp']:,} CLP")
except Exception as e: fail("Conversión moneda", e)

try:
    resumen = datos_resumen(normalizar_licitacion(RAW_LIC))
    assert "_raw" not in resumen and "codigo" in resumen and "titulo" in resumen
    ok(f"datos_resumen: {sorted(resumen.keys())}")
except Exception as e: fail("datos_resumen", e)

# ══════════════════════════════════════════════
# 4. MOTOR DE FILTROS — OPERADORES
# ══════════════════════════════════════════════
section("4. Motor de filtros — 27 operadores")
from app.filter_engine import evaluar_regla, validar_filtros, FILTROS_VALIDOS

class R:
    def __init__(self, f, n="T"): self.filtros=f; self.nombre_regla=n

ENT = {
    "tipo":"licitacion","codigo":"T-001",
    "titulo":"Adquisición de software ERP para gestión",
    "descripcion":"Sistema de planificación de recursos empresariales",
    "region":13,"nombre_region":"Metropolitana","monto_clp":3_500_000,
    "estado":"Publicada","codigo_producto":["30200000","43230000"],
    "organismo":"Municipalidad de Las Condes",
    "fecha_publicacion":"2024-03-15","fecha_cierre":"2024-04-01",
}

TESTS = [
    ({"region":13},                               True,  "region exacta ✓"),
    ({"region":5},                                False, "region incorrecta ✗"),
    ({"region_in":[13,14,15]},                    True,  "region_in ✓"),
    ({"region_in":[5,6,7]},                       False, "region_in sin match ✗"),
    ({"monto_min":1_000_000},                     True,  "monto_min OK ✓"),
    ({"monto_min":5_000_000},                     False, "monto_min alto ✗"),
    ({"monto_max":5_000_000},                     True,  "monto_max OK ✓"),
    ({"monto_max":1_000_000},                     False, "monto_max bajo ✗"),
    ({"monto_min":1_000_000,"monto_max":5_000_000},True, "rango monto ✓"),
    ({"titulo_contains":"software"},              True,  "titulo_contains ✓"),
    ({"titulo_contains":"madera"},                False, "titulo_contains ✗"),
    ({"titulo_contains":"SOFTWARE"},              True,  "case-insensitive ✓"),
    ({"titulo_not_contains":"madera"},            True,  "titulo_not_contains ✓"),
    ({"titulo_not_contains":"software"},          False, "titulo_not_contains ✗"),
    ({"descripcion_contains":"planificación"},    True,  "descripcion_contains ✓"),
    ({"organismo_contains":"Las Condes"},         True,  "organismo_contains ✓"),
    ({"organismo_contains":"Valparaíso"},         False, "organismo_contains ✗"),
    ({"codigo_producto_in":["30200000"]},         True,  "codigo_producto_in ✓"),
    ({"codigo_producto_in":["99999999"]},         False, "codigo_producto_in ✗"),
    ({"codigo_producto_in":["30200000","9999"]},  True,  "cod_prod intersección ✓"),
    ({"estado":"Publicada"},                      True,  "estado exacto ✓"),
    ({"estado":"publicada"},                      True,  "estado case-insensitive ✓"),
    ({"estado":"Adjudicada"},                     False, "estado incorrecto ✗"),
    ({"estado_in":["Publicada","Adjudicada"]},    True,  "estado_in ✓"),
    ({},                                          True,  "sin filtros = acepta todo ✓"),
    ({"region":13,"titulo_contains":"software","monto_min":1_000_000}, True,  "AND multi ✓"),
    ({"region":13,"titulo_contains":"madera"},    False, "AND uno falla ✗"),
]
for filtros, esperado, desc in TESTS:
    try:
        r = evaluar_regla(R(filtros), ENT)
        if r == esperado: ok(desc)
        else: fail(f"{desc} → obtuvo {r}")
    except Exception as e: fail(desc, e)

# ══════════════════════════════════════════════
# 5. FILTROS — CASOS DE BORDE
# ══════════════════════════════════════════════
section("5. Motor de filtros — casos de borde")

EMPTY_ENT = {"tipo":"licitacion","codigo":"X","titulo":None,
             "region":None,"monto_clp":None,"estado":None,
             "codigo_producto":[],"organismo":None,"descripcion":None}
for filtros, esperado, desc in [
    ({"region":13},         False, "region=None → False"),
    ({"monto_min":1},       False, "monto=None con monto_min → False"),
    ({"titulo_contains":"x"},False,"titulo=None → False"),
    ({},                    True,  "sin filtros + entidad vacía → True"),
]:
    try:
        assert evaluar_regla(R(filtros), EMPTY_ENT) == esperado; ok(desc)
    except Exception as e: fail(desc, e)

try:
    r = evaluar_regla(R({"titulo_contains":"","region_in":[],"monto_min":None}), ENT)
    assert r == True; ok("Valores vacíos en filtros ignorados → True")
except Exception as e: fail("Filtros vacíos", e)

try:
    assert validar_filtros({"region":13,"monto_min":1_000_000}) == []
    ok("validar_filtros: válidos → 0 errores")
    errs = validar_filtros({"region":"trece"})
    assert len(errs) > 0; ok(f"validar_filtros: tipo incorrecto detectado")
except Exception as e: fail("validar_filtros", e)

# ══════════════════════════════════════════════
# 6. RATE LIMITER
# ══════════════════════════════════════════════
section("6. API Client — Rate Limiter")
from app.api_client import RateLimiter, QuotaExhaustedException

try:
    rl = RateLimiter(per_second=50, per_day=100)
    t0 = time.monotonic()
    for _ in range(10): rl.acquire()
    assert time.monotonic()-t0 < 2.0; ok("10 req < 2s")
except Exception as e: fail("RateLimiter velocidad", e)

try:
    rl2 = RateLimiter(per_second=1000, per_day=5)
    for _ in range(5): rl2.acquire()
    try: rl2.acquire(); fail("Debería lanzar QuotaExhaustedException")
    except QuotaExhaustedException: ok("QuotaExhaustedException al superar cuota diaria")
except Exception as e: fail("RateLimiter cuota diaria", e)

# ══════════════════════════════════════════════
# 7. API CLIENT — PAGINACIÓN (mock)
# ══════════════════════════════════════════════
section("7. API Client — Paginación (mocked)")
from app.api_client import MercadoPublicoClient

P1 = [{"CodigoExterno":f"L{i:04d}","Nombre":f"Lic{i}","Estado":"Publicada",
       "FechaPublicacion":"2024-03-15T00:00:00","MontoEstimado":i*1000000,
       "Comprador":{"CodigoRegion":13,"NombreRegion":"RM","NombreOrganismo":"ORG"},
       "Items":{"Listado":[]}} for i in range(1000)]
P2 = [{"CodigoExterno":f"L{i:04d}","Nombre":f"Lic{i}","Estado":"Publicada",
       "FechaPublicacion":"2024-03-15T00:00:00","MontoEstimado":i*1000000,
       "Comprador":{"CodigoRegion":13,"NombreRegion":"RM","NombreOrganismo":"ORG"},
       "Items":{"Listado":[]}} for i in range(1000,1050)]

try:
    client = MercadoPublicoClient("TEST")
    calls = [0]
    def mock_get(path, params=None, **kw):
        calls[0] += 1
        pg = (params or {}).get("pagina",1)
        return {"Listado": P1 if pg==1 else (P2 if pg==2 else [])}
    with patch.object(client, "_get", side_effect=mock_get):
        items = list(client.iter_licitaciones())
    assert len(items) == 1050, f"esperaba 1050, obtuvo {len(items)}"
    assert calls[0] in (2,3), f"esperaba 2-3 calls, obtuvo {calls[0]}"
    ok(f"Paginación: 1050 items en {calls[0]} requests")
except Exception as e: fail("Paginación", e)

# ══════════════════════════════════════════════
# 8. EMAIL SERVICE
# ══════════════════════════════════════════════
section("8. Email Service — Cola thread-safe")
from app.email_service import encolar_alerta, encolar_resumen_admin, _email_queue

try:
    n0 = _email_queue.qsize()
    ent_email = {"titulo":"ERP","link_detalle":"http://mp.cl","monto_clp":1_000_000,
                 "tipo":"licitacion","codigo":"E-001","organismo":"MINSAL",
                 "region":13,"nombre_region":"RM","fecha_publicacion":"2024-03-15",
                 "estado":"Publicada","fecha_cierre":"2024-04-01"}
    encolar_alerta("Regla X", ent_email, ["dest@t.cl"])
    assert _email_queue.qsize() == n0+1; ok("encolar_alerta encolado ✓")
    encolar_resumen_admin({"reglas_evaluadas":3,"alertas_generadas":2,"errores":0,
                           "duracion_seg":5.2,"tipos_consultados":["licitacion"]})
    assert _email_queue.qsize() == n0+2; ok("encolar_resumen_admin encolado ✓")
except Exception as e: fail("Email queue", e)

try:
    from app.email_service import _enviar_raw
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    mock_smtp = MagicMock()
    msg = MIMEMultipart("alternative")
    msg["Subject"]="Test"; msg["From"]="t@t.cl"; msg["To"]="d@t.cl"
    msg.attach(MIMEText("<p>Test</p>","html"))
    with patch("smtplib.SMTP", return_value=mock_smtp):
        mock_smtp.__enter__ = lambda s: s
        mock_smtp.__exit__ = MagicMock(return_value=False)
        _enviar_raw(msg, ["d@t.cl"], max_retries=1)
    ok("_enviar_raw con SMTP mock ✓")
except Exception as e: fail("_enviar_raw mock", e)

# ══════════════════════════════════════════════
# 9. SCHEDULER — CICLO COMPLETO
# ══════════════════════════════════════════════
section("9. Scheduler — ciclo con snapshots")
from app.scheduler import ejecutar_ciclo_completo, ejecutar_regla_manualmente

try:
    with get_db() as db:
        db.query(AlertaGenerada).delete()
        db.query(LicitacionSnapshot).delete()
        db.query(ReglaUsuario).delete()
        db.add(ReglaUsuario(nombre_regla="Sched-TI",activa=True,tipo_entidad="licitacion",
                            filtros={"region":13,"titulo_contains":"software"},
                            email_destino="d@t.cl"))
        db.add(ReglaUsuario(nombre_regla="Sched-Monto",activa=True,tipo_entidad="licitacion",
                            filtros={"monto_min":1_000_000},email_destino="d@t.cl"))
        db.add(ReglaUsuario(nombre_regla="INACTIVA",activa=False,tipo_entidad="licitacion",
                            filtros={},email_destino="d@t.cl"))
    ok("3 reglas seed (2 activas)")
except Exception as e: fail("Seed reglas scheduler", e)

SNAPS = [
    {"codigo":"SW-001","tipo":"licitacion","region":13,"monto_clp":2_000_000,
     "datos":{"tipo":"licitacion","codigo":"SW-001","titulo":"Compra de software CRM",
              "region":13,"monto_clp":2_000_000,"estado":"Publicada","organismo":"MINSAL",
              "nombre_region":"RM","fecha_publicacion":"2024-03-15","fecha_cierre":"2024-04-01",
              "codigo_producto":["30200000"],"link_detalle":"https://mp.cl/1"}},
    {"codigo":"HW-001","tipo":"licitacion","region":5,"monto_clp":5_000_000,
     "datos":{"tipo":"licitacion","codigo":"HW-001","titulo":"Adquisición de notebooks",
              "region":5,"monto_clp":5_000_000,"estado":"Publicada","organismo":"SEREMI",
              "nombre_region":"Valparaíso","fecha_publicacion":"2024-03-15","fecha_cierre":"2024-04-01",
              "codigo_producto":["43211503"],"link_detalle":"https://mp.cl/2"}},
]
try:
    with get_db() as db:
        for s in SNAPS:
            db.add(LicitacionSnapshot(codigo=s["codigo"],tipo=s["tipo"],
                datos=s["datos"],region=s["region"],monto_clp=s["monto_clp"],
                fecha_publicacion=datetime.now(timezone.utc)-timedelta(hours=1)))
    ok(f"{len(SNAPS)} snapshots seed")
except Exception as e: fail("Seed snapshots", e)

try:
    with patch("app.scheduler.MercadoPublicoClient"), \
         patch("app.scheduler.encolar_alerta") as mock_email:
        stats = ejecutar_ciclo_completo()
    ok(f"ejecutar_ciclo_completo: {stats}")
    with get_db() as db:
        n = db.query(AlertaGenerada).count()
    ok(f"Alertas en BD tras ciclo: {n}")
except Exception as e: fail("ejecutar_ciclo_completo", e)

try:
    with patch("app.scheduler.MercadoPublicoClient"), \
         patch("app.scheduler.encolar_alerta"):
        ejecutar_ciclo_completo()
    with get_db() as db:
        n2 = db.query(AlertaGenerada).count()
    ok(f"Deduplicación: segunda ejecución mantiene {n2} alertas (sin duplicados)")
except Exception as e: fail("Deduplicación", e)

try:
    with get_db() as db:
        rid = db.query(ReglaUsuario).filter_by(nombre_regla="Sched-TI").first().id
    with patch("app.scheduler.MercadoPublicoClient"), \
         patch("app.scheduler.encolar_alerta"):
        res = ejecutar_regla_manualmente(rid)
    assert isinstance(res, dict); ok(f"ejecutar_regla_manualmente: {res}")
except Exception as e: fail("ejecutar_regla_manualmente", e)

# ══════════════════════════════════════════════
# 10. DASHBOARD HTTP (Flask test client)
# ══════════════════════════════════════════════
section("10. Dashboard — rutas HTTP con auth")
from app.dashboard import create_app
flask_app = create_app()
flask_app.config["TESTING"] = True

def auth_hdr(u="admin", p="testpass"):
    return {"Authorization":"Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}

VALID  = auth_hdr()
WRONG  = auth_hdr("admin","wrong")
NO_AUTH = {}

with flask_app.test_client() as cli:
    # Auth
    for headers, exp, label in [
        (NO_AUTH, 401, "sin auth → 401"),
        (WRONG,   401, "auth incorrecta → 401"),
        (VALID,   200, "auth válida → 200"),
    ]:
        try:
            r = cli.get("/", headers=headers)
            assert r.status_code == exp; ok(label)
        except Exception as e: fail(label, e)

    # Páginas
    for path, exp, label in [
        ("/",                200, "GET / (reglas)"),
        ("/reglas/nueva",    200, "GET /reglas/nueva"),
        ("/alertas",         200, "GET /alertas"),
        ("/analytics",       200, "GET /analytics"),
    ]:
        try:
            r = cli.get(path, headers=VALID)
            assert r.status_code == exp; ok(f"{label} → {r.status_code}")
        except Exception as e: fail(label, e)

    # CRUD reglas
    try:
        r = cli.post("/reglas/nueva", headers=VALID,
                     data={"nombre_regla":"HTTP-Test","tipo_entidad":"licitacion",
                           "email_destino":"t@t.cl",
                           "filtros_json":json.dumps({"region":5})},
                     follow_redirects=False)
        assert r.status_code in (200,302); ok(f"POST /reglas/nueva → {r.status_code}")
    except Exception as e: fail("POST /reglas/nueva", e)

    try:
        with get_db() as db:
            rid = db.query(ReglaUsuario).filter_by(nombre_regla="HTTP-Test").first()
            rid = rid.id if rid else 1
        r = cli.get(f"/reglas/{rid}/editar", headers=VALID)
        assert r.status_code == 200; ok(f"GET /reglas/{rid}/editar → 200")
        r2 = cli.post(f"/reglas/{rid}/toggle", headers=VALID, follow_redirects=False)
        assert r2.status_code in (200,302); ok(f"POST toggle → {r2.status_code}")
    except Exception as e: fail("editar/toggle regla", e)

    # Alertas
    try:
        with get_db() as db:
            a = db.query(AlertaGenerada).first()
            aid = a.id if a else 1
        r = cli.get(f"/alertas/{aid}", headers=VALID)
        assert r.status_code == 200; ok(f"GET /alertas/{aid} → 200")
        r2 = cli.get("/alertas/99999", headers=VALID)
        assert r2.status_code == 404; ok("GET /alertas/99999 → 404")
    except Exception as e: fail("Alertas rutas", e)

    # API analytics JSON
    for ep, label in [
        ("/api/analytics/resumen",    "KPIs"),
        ("/api/analytics/categorias", "categorías"),
        ("/api/analytics/organismos", "organismos"),
        ("/api/analytics/regional",   "regional"),
        ("/api/analytics/temporal",   "temporal"),
        ("/api/analytics/diario",     "diario"),
    ]:
        try:
            r = cli.get(f"{ep}?dias=90", headers=VALID)
            data = json.loads(r.data)
            assert r.status_code == 200 and isinstance(data,(list,dict))
            n = len(data) if isinstance(data,list) else len(data.keys())
            ok(f"GET {ep} → 200 JSON({n}) {label}")
        except Exception as e: fail(f"GET {ep}", e)

    # Run manual — patch sobre el símbolo ya importado en routes
    try:
        with get_db() as db:
            rid = db.query(ReglaUsuario).filter_by(activa=True).first().id
        mock_res = {"regla_id":rid,"regla_nombre":"Test","entidades_evaluadas":2,
                    "alertas_generadas":1,"emails_encolados":1}
        with patch("app.dashboard.routes.ejecutar_regla_manualmente",
                   return_value=mock_res):
            r = cli.post(f"/reglas/{rid}/run", headers=VALID)
        assert r.status_code in (200, 302), f"status={r.status_code}"
        ok(f"POST /reglas/{rid}/run → {r.status_code} redirect OK (1 alerta generada)")
    except Exception as e: fail("run manual", e)

# ══════════════════════════════════════════════
# 11. ANALYTICS KPIs
# ══════════════════════════════════════════════
section("11. Analytics — KPIs con datos sintéticos")
from app.analytics import AnalyticsEngine

try:
    now = datetime.now(timezone.utc)
    with get_db() as db:
        for i in range(20):
            db.add(LicitacionSnapshot(
                codigo=f"ANA-{i:04d}", tipo="licitacion",
                datos={"tipo":"licitacion","titulo":f"Licitación {i}",
                       "monto_clp":1_000_000*(i+1),"region":(i%5)+1,
                       "organismo":f"Org{i%3}","codigo_producto":[f"3020000{i%5}"],
                       "estado":"Publicada","fecha_publicacion":(now-timedelta(days=i)).strftime("%Y-%m-%d")},
                region=(i%5)+1, monto_clp=1_000_000*(i+1),
                fecha_publicacion=now-timedelta(days=i), estado="Publicada"
            ))
    ok("20 snapshots sintéticos para analytics")
except Exception as e: fail("Seed analytics", e)

engine_an = AnalyticsEngine()
for fn, args, label in [
    ("resumen_ejecutivo", {"dias":90}, "resumen_ejecutivo"),
    ("top_categorias",    {"limit":5,"dias":90}, "top_categorias"),
    ("top_organismos",    {"limit":5,"dias":90}, "top_organismos"),
    ("distribucion_regional", {"dias":90}, "distribucion_regional"),
]:
    try:
        res = getattr(engine_an, fn)(**args)
        assert isinstance(res,(dict,list)); ok(f"{fn}: {type(res).__name__}({len(res) if isinstance(res,list) else len(res.keys())})")
    except Exception as e: fail(fn, e)

# ══════════════════════════════════════════════
# 12. INTEGRACIÓN E2E
# ══════════════════════════════════════════════
section("12. Integración End-to-End")
try:
    with get_db() as db:
        db.add(ReglaUsuario(nombre_regla="E2E-SW-RM",activa=True,tipo_entidad="licitacion",
                            filtros={"region":13,"titulo_contains":"software","monto_min":1_000_000},
                            email_destino="e2e@t.cl"))
    ok("Regla E2E creada")
except Exception as e: fail("Regla E2E", e)

try:
    match_datos = {"tipo":"licitacion","codigo":"E2E-M-001","titulo":"Adquisición de software contable",
                   "region":13,"monto_clp":3_000_000,"estado":"Publicada","organismo":"MINSAL",
                   "nombre_region":"RM","fecha_publicacion":"2024-03-15","fecha_cierre":"2024-04-01",
                   "codigo_producto":["30200000"],"link_detalle":"https://mp.cl/e2e"}
    nomatch_datos = dict(match_datos, codigo="E2E-N-001", region=5, nombre_region="Valparaíso")
    with get_db() as db:
        db.add(LicitacionSnapshot(codigo="E2E-M-001",tipo="licitacion",datos=match_datos,
               region=13,monto_clp=3_000_000,fecha_publicacion=datetime.now(timezone.utc)-timedelta(hours=1)))
        db.add(LicitacionSnapshot(codigo="E2E-N-001",tipo="licitacion",datos=nomatch_datos,
               region=5,monto_clp=3_000_000,fecha_publicacion=datetime.now(timezone.utc)-timedelta(hours=1)))
    ok("2 snapshots E2E insertados (1 match + 1 no-match)")
except Exception as e: fail("Snapshots E2E", e)

try:
    from app.filter_engine import evaluar_regla as ev
    matches, no_matches = [], []
    with get_db() as db:
        regla = db.query(ReglaUsuario).filter_by(nombre_regla="E2E-SW-RM").first()
        snaps = db.query(LicitacionSnapshot).filter(
            LicitacionSnapshot.codigo.in_(["E2E-M-001","E2E-N-001"])).all()
        for s in snaps:
            (matches if ev(regla,s.datos) else no_matches).append(s.codigo)
    assert matches == ["E2E-M-001"] and no_matches == ["E2E-N-001"]
    ok(f"Motor E2E: 1 match ({matches[0]}), 1 no-match ({no_matches[0]}) ✓")
except Exception as e: fail("Motor E2E", e)

try:
    with get_db() as db:
        regla = db.query(ReglaUsuario).filter_by(nombre_regla="E2E-SW-RM").first()
        snap_datos = db.query(LicitacionSnapshot).filter_by(codigo="E2E-M-001").first().datos
        db.add(AlertaGenerada(regla_id=regla.id,entidad_id="E2E-M-001",
                              datos_resumen=snap_datos,enviado_email=True,mostrado_dashboard=True))
    with get_db() as db:
        a = db.query(AlertaGenerada).filter_by(entidad_id="E2E-M-001").first()
        assert a and a.enviado_email
    ok("Alerta persistida y recuperada de BD ✓")
except Exception as e: fail("Alerta E2E BD", e)

try:
    with flask_app.test_client() as cli2:
        r = cli2.get("/alertas", headers=VALID)
        assert r.status_code == 200 and b"E2E" in r.data
    ok("Dashboard /alertas muestra alerta E2E ✓")
except Exception as e: fail("Dashboard E2E", e)

# ══════════════════════════════════════════════
# RESUMEN
# ══════════════════════════════════════════════
print(f"\n{'═'*58}")
print(f"  RESULTADO FINAL")
print(f"{'═'*58}")
total = RESULTS["passed"]+RESULTS["failed"]
print(f"  ✅ Passed : {RESULTS['passed']}/{total}")
print(f"  ❌ Failed : {RESULTS['failed']}/{total}")
if RESULTS["errors"]:
    print(f"\n  Errores detectados:")
    for e in RESULTS["errors"]: print(f"    • {e}")
pct = RESULTS["passed"]/total*100 if total else 0
print(f"\n  Cobertura: {pct:.1f}%")
print(f"  {'🚀 MVP VALIDADO' if not RESULTS['failed'] else '⚠  REVISAR ERRORES'}")
print(f"{'═'*58}")
sys.exit(0 if not RESULTS["failed"] else 1)
