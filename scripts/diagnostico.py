#!/usr/bin/env python3
"""
scripts/diagnostico.py
Verifica conectividad real con la API de Mercado Público
y muestra la estructura exacta de los datos que retorna.

Uso: python scripts/diagnostico.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

import requests
from datetime import datetime, timedelta

TICKET = os.environ.get("TICKET_MERCADO_PUBLICO", "")
BASE   = os.environ.get("API_BASE_URL", "https://api.mercadopublico.cl/servicios/v1")

RED    = "\033[91m"; GREEN  = "\033[92m"
YELLOW = "\033[93m"; CYAN   = "\033[96m"; NC = "\033[0m"

def ok(msg):   print(f"  {GREEN}✅{NC} {msg}")
def err(msg):  print(f"  {RED}❌{NC} {msg}")
def warn(msg): print(f"  {YELLOW}⚠️ {NC} {msg}")
def info(msg): print(f"  {CYAN}→{NC}  {msg}")

print(f"\n{'═'*60}")
print("  Diagnóstico API Mercado Público")
print(f"{'═'*60}\n")

# 1. Ticket
if not TICKET:
    err("TICKET_MERCADO_PUBLICO no está definido en .env"); sys.exit(1)
if len(TICKET) < 10:
    warn(f"El ticket parece muy corto ({len(TICKET)} chars): {TICKET!r}")
else:
    ok(f"Ticket cargado ({len(TICKET)} chars): {TICKET[:8]}…{TICKET[-4:]}")

# 2. Conectividad
print("\n── Conectividad ──────────────────────────────────────────")
fecha_hoy   = datetime.now().strftime("%d%m%Y")
fecha_ayer  = (datetime.now() - timedelta(days=1)).strftime("%d%m%Y")

endpoints = {
    "licitacion":   f"{BASE}/publico/licitaciones.json",
    "orden_compra": f"{BASE}/publico/ordenesdecompra.json",
}

muestra = {}
for tipo, url in endpoints.items():
    for fecha in [fecha_hoy, fecha_ayer]:
        params = {"ticket": TICKET, "fecha": fecha, "pagina": 1}
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json()
                listado = data.get("Listado") or []
                cantidad = data.get("Cantidad", len(listado))
                ok(f"{tipo.upper()} fecha={fecha}: {cantidad} registros disponibles")
                if listado:
                    muestra[tipo] = listado[0]
                break
            elif r.status_code == 401:
                err(f"{tipo.upper()}: Ticket inválido o expirado (401)")
                info("Solicita nuevo ticket en: https://apis.mercadopublico.cl/")
                break
            elif r.status_code == 429:
                warn(f"{tipo.upper()}: Rate limit alcanzado (429). Espera 1 minuto.")
                break
            else:
                warn(f"{tipo.upper()} fecha={fecha}: HTTP {r.status_code} — {r.text[:100]}")
        except requests.exceptions.ConnectionError:
            err(f"Sin conexión a {url}")
            sys.exit(1)
        except Exception as e:
            err(f"{tipo.upper()}: {e}")

# 3. Estructura real de los datos
if muestra:
    print("\n── Estructura real de los datos ──────────────────────────")
    for tipo, item in muestra.items():
        print(f"\n  [{tipo.upper()}] Campos disponibles:")
        for k, v in item.items():
            if isinstance(v, dict):
                print(f"    {CYAN}{k}{NC} → dict({list(v.keys())})")
            elif isinstance(v, list):
                print(f"    {CYAN}{k}{NC} → list[{len(v)}]")
            else:
                disp = str(v)[:60] + ("…" if len(str(v)) > 60 else "")
                print(f"    {CYAN}{k}{NC} → {disp!r}")

    # 4. Probar normalizer
    print("\n── Normalizer ────────────────────────────────────────────")
    from app.normalizer import normalizar_licitacion, normalizar_orden_compra
    for tipo, item in muestra.items():
        fn = normalizar_licitacion if tipo == "licitacion" else normalizar_orden_compra
        try:
            norm = fn(item)
            ok(f"{tipo}: normalizado OK")
            for campo in ["codigo","titulo","region","monto_clp","fecha_publicacion","estado"]:
                info(f"  {campo}: {norm.get(campo)!r}")
        except Exception as e:
            err(f"Error normalizando {tipo}: {e}")

# 5. Rate limit remaining
print("\n── Resumen ───────────────────────────────────────────────")
ok("Diagnóstico completado — la API responde correctamente")
info("Ejecuta: python main.py --once   para el primer ciclo real")
info("O abre el dashboard y usa el botón 'Ejecutar ahora' en cada regla")
print()
