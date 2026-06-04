#!/usr/bin/env python3
"""
scripts/crear_reglas_ejemplo.py
Crea reglas de alerta reales y útiles para Chile.
Edítalas según tu caso de uso antes de correr.

Uso: python scripts/crear_reglas_ejemplo.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()

from app.database import init_db, get_db
from app.models import ReglaUsuario

# ══════════════════════════════════════════════════════════════
# EDITA AQUÍ: tu email de destino
# ══════════════════════════════════════════════════════════════
MI_EMAIL = os.environ.get("ADMIN_EMAIL", "tu@email.cl")

REGLAS = [
    # ── Tecnología ──────────────────────────────────────────────
    {
        "nombre_regla":  "Licitaciones TI/Software RM",
        "tipo_entidad":  "licitacion",
        "email_destino": MI_EMAIL,
        "filtros": {
            "region":            13,          # Región Metropolitana
            "titulo_contains":   "software",
            "monto_min":         1_000_000,   # mínimo $1M CLP
        },
    },
    {
        "nombre_regla":  "Licitaciones TI — Cualquier región >$5M",
        "tipo_entidad":  "licitacion",
        "email_destino": MI_EMAIL,
        "filtros": {
            "titulo_contains":   "software",
            "monto_min":         5_000_000,
            "estado":            "Publicada",
        },
    },
    {
        "nombre_regla":  "Servicios de Consultoría TI (todas las regiones)",
        "tipo_entidad":  "licitacion",
        "email_destino": MI_EMAIL,
        "filtros": {
            "titulo_contains":   "consultoría",
            "monto_min":         2_000_000,
            "estado":            "Publicada",
        },
    },
    # ── Equipamiento ────────────────────────────────────────────
    {
        "nombre_regla":  "Computadores y notebooks — Sector público",
        "tipo_entidad":  "licitacion",
        "email_destino": MI_EMAIL,
        "filtros": {
            "titulo_contains":   "notebook",
            "monto_min":         500_000,
        },
    },
    # ── Órdenes de Compra ────────────────────────────────────────
    {
        "nombre_regla":  "OC Software/Licencias RM >$500K",
        "tipo_entidad":  "orden_compra",
        "email_destino": MI_EMAIL,
        "filtros": {
            "region":            13,
            "titulo_contains":   "licencia",
            "monto_min":         500_000,
        },
    },
    {
        "nombre_regla":  "OC Grandes montos (>$50M cualquier tipo)",
        "tipo_entidad":  "orden_compra",
        "email_destino": MI_EMAIL,
        "filtros": {
            "monto_min":         50_000_000,
        },
    },
    # ── Los Lagos / Los Ríos (personaliza según tu región) ──────
    {
        "nombre_regla":  "Licitaciones Los Lagos + Los Ríos",
        "tipo_entidad":  "licitacion",
        "email_destino": MI_EMAIL,
        "filtros": {
            "region_in":   [10, 14],   # Los Lagos = 10, Los Ríos = 14
            "monto_min":   500_000,
        },
    },
    # ── Compra Ágil ─────────────────────────────────────────────
    {
        "nombre_regla":  "Compra Ágil TI <$3M (oportunidades rápidas)",
        "tipo_entidad":  "compra_agil",
        "email_destino": MI_EMAIL,
        "filtros": {
            "titulo_contains":   "software",
            "monto_max":         3_000_000,
        },
    },
]

def main():
    GREEN = "\033[92m"; YELLOW = "\033[93m"; NC = "\033[0m"
    print(f"\n── Creando {len(REGLAS)} reglas de alerta ──\n")
    init_db()

    creadas = 0
    with get_db() as db:
        existentes = {r.nombre_regla for r in db.query(ReglaUsuario).all()}
        for r in REGLAS:
            if r["nombre_regla"] in existentes:
                print(f"  {YELLOW}SKIP{NC} (ya existe): {r['nombre_regla']}")
                continue
            regla = ReglaUsuario(
                nombre_regla  = r["nombre_regla"],
                activa        = True,
                tipo_entidad  = r["tipo_entidad"],
                filtros       = r["filtros"],
                email_destino = r["email_destino"],
            )
            db.add(regla)
            creadas += 1
            print(f"  {GREEN}✅ Creada{NC}: {r['nombre_regla']}")
            print(f"     tipo={r['tipo_entidad']} | email={r['email_destino']}")
            print(f"     filtros={r['filtros']}")
            print()

    print(f"  Resultado: {creadas} reglas nuevas / {len(REGLAS)} totales")
    print(f"\n  Próximo paso:")
    print(f"    python main.py --once    → ejecutar ciclo real ahora")
    print(f"    o abre el dashboard y usa el botón ▶ en cada regla\n")

if __name__ == "__main__":
    main()
