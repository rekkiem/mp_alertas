#!/usr/bin/env python3
"""
fix_errors.py  — Aplica 2 correcciones en tu proyecto mp_alertas.

Errores corregidos:
  1. DetachedInstanceError en /licitaciones/export.csv (routes.py)
  2. requirements.txt incompatible con Python 3.13 (pydantic-core sin wheel)

Uso:
  python fix_errors.py          # muestra qué cambiaría
  python fix_errors.py --apply  # aplica los cambios
"""
import sys, os, re, shutil
from pathlib import Path

ROOT   = Path(__file__).parent
APPLY  = "--apply" in sys.argv

def status(msg):  print(f"  {'[APLICA]' if APPLY else '[PREVIEW]'} {msg}")
def ok(msg):      print(f"  ✅ {msg}")
def err(msg):     print(f"  ❌ {msg}"); sys.exit(1)

print("\n══════════════════════════════════════════════")
print("  fix_errors.py — MP Alertas")
print(f"  Modo: {'APLICAR CAMBIOS' if APPLY else 'PREVIEW (pasa --apply para aplicar)'}")
print("══════════════════════════════════════════════\n")

# ═════════════════════════════════════════════════════════════════════════════
# FIX 1: DetachedInstanceError en export CSV
# El loop que accede a s.datos debe estar DENTRO del with get_db()
# ═════════════════════════════════════════════════════════════════════════════
print("── Fix 1: DetachedInstanceError en /licitaciones/export.csv ──")

routes_path = ROOT / "app" / "dashboard" / "routes.py"
if not routes_path.exists():
    err(f"No se encontró {routes_path}")

content = routes_path.read_text(encoding="utf-8")

# Detectar si el error aún existe: buscar el patrón "for s in snaps:" FUERA del with
# El bug: snaps se carga dentro del with, pero el loop for está fuera
bug_pattern = re.search(
    r'(snaps\s*=\s*base_q\.order_by[^\n]+\.all\(\))\s*\n\n\s*buf\s*=\s*io\.StringIO',
    content
)

if bug_pattern:
    print("  ⚠️  Bug detectado: loop CSV fuera del contexto de sesión → DetachedInstanceError")

    # Código con el bug
    old_code = r"""        snaps = base_q.order_by(orden_map.get(orden, LicitacionSnapshot.fecha_sincronizacion.desc())).limit(10000).all()

    buf = io.StringIO()
    buf.write("\ufeff")   # BOM para Excel
    writer = csv.writer(buf)
    writer.writerow(["Codigo","Tipo","Titulo","Organismo","Estado",
                     "Region","Monto_CLP","Fecha_Publicacion","Fecha_Cierre",
                     "Datos_Completos","Link"])
    for s in snaps:
        d = s.datos or {}
        writer.writerow([
            s.codigo, s.tipo,
            d.get("titulo",""), d.get("organismo",""),
            s.estado or "",
            REGIONES_CHILE.get(s.region, "") if s.region else "",
            s.monto_clp or "",
            s.fecha_publicacion.strftime("%Y-%m-%d") if s.fecha_publicacion else "",
            d.get("fecha_cierre",""),
            "Si" if (s.region is not None) else "No",
            d.get("link_detalle",""),
        ])"""

    new_code = r"""        # FIX: serializar DENTRO del with get_db() para evitar DetachedInstanceError
        snaps_raw = base_q.order_by(
            orden_map.get(orden, LicitacionSnapshot.fecha_sincronizacion.desc())
        ).limit(10000).all()

        filas_csv = []
        for s in snaps_raw:
            d = s.datos or {}
            filas_csv.append([
                s.codigo, s.tipo,
                d.get("titulo",""), d.get("organismo",""),
                s.estado or "",
                REGIONES_CHILE.get(s.region, "") if s.region else "",
                s.monto_clp or "",
                s.fecha_publicacion.strftime("%Y-%m-%d") if s.fecha_publicacion else "",
                d.get("fecha_cierre",""),
                "Si" if (s.region is not None) else "No",
                d.get("link_detalle",""),
            ])

    buf = io.StringIO()
    buf.write("\ufeff")   # BOM para Excel
    writer = csv.writer(buf)
    writer.writerow(["Codigo","Tipo","Titulo","Organismo","Estado",
                     "Region","Monto_CLP","Fecha_Publicacion","Fecha_Cierre",
                     "Datos_Completos","Link"])
    for fila in filas_csv:
        writer.writerow(fila)"""

    if old_code in content:
        if APPLY:
            backup = routes_path.with_suffix(".py.bak")
            shutil.copy(routes_path, backup)
            routes_path.write_text(content.replace(old_code, new_code), encoding="utf-8")
            ok(f"routes.py parcheado (backup: {backup.name})")
        else:
            status("routes.py — reemplazar el loop CSV para serializar dentro de la sesión")
    else:
        # Fallback: buscar patrón más flexible
        # Puede que el código tenga pequeñas diferencias de espaciado
        pattern2 = r'snaps\s*=\s*base_q\.order_by\([^)]+\)\.limit\(10000\)\.all\(\)'
        if re.search(pattern2, content):
            print("  ℹ️  Patrón alternativo encontrado — el código puede diferir levemente.")
            print("     Edita manualmente routes.py: mueve el loop 'for s in snaps:' DENTRO del 'with get_db():'")
        else:
            ok("routes.py ya tiene el fix aplicado (no se encontró el patrón del bug)")
else:
    # Verificar si ya está corregido
    if "filas_csv" in content and "for fila in filas_csv:" in content:
        ok("routes.py ya tiene el fix (usa filas_csv dentro del with)")
    else:
        print("  ⚠️  No se pudo detectar el patrón automáticamente.")
        print("     Busca en routes.py la función 'licitaciones_export_csv'")
        print("     y asegúrate de que el loop 'for s in snaps:' esté DENTRO del 'with get_db():'")

# ═════════════════════════════════════════════════════════════════════════════
# FIX 2: requirements.txt para Python 3.13
# pydantic==2.7.1 → pydantic-core==2.18.2 NO tiene wheel cp313-win_amd64
# pydantic==2.9.2 → pydantic-core==2.23.4 SÍ tiene wheel cp313-win_amd64
# ═════════════════════════════════════════════════════════════════════════════
print("\n── Fix 2: requirements.txt incompatible con Python 3.13 ──")

req_path = ROOT / "requirements.txt"
req_content = req_path.read_text(encoding="utf-8")

# Detectar versiones problemáticas
py_ver = sys.version_info
print(f"  Python detectado: {py_ver.major}.{py_ver.minor}.{py_ver.micro}")

new_req_content = req_content

replacements = [
    # pydantic 2.7.1 no tiene wheel cp313 → usar 2.9.2 (primera versión con soporte Python 3.13)
    ("pydantic==2.7.1",          "pydantic==2.9.2"),
    # pydantic-settings debe ser compatible con pydantic 2.9.x
    ("pydantic-settings==2.2.1", "pydantic-settings==2.4.0"),
    # pydantic-core no se suele poner en requirements.txt directamente,
    # pero si estuviera, reemplazarlo también
    ("pydantic-core==2.18.2",    "pydantic-core==2.23.4"),
]

cambios = []
for old, new in replacements:
    if old in new_req_content:
        new_req_content = new_req_content.replace(old, new)
        cambios.append((old, new))
        status(f"  {old}  →  {new}")

if cambios:
    if APPLY:
        req_path.write_text(new_req_content, encoding="utf-8")
        ok(f"requirements.txt actualizado ({len(cambios)} cambios)")
        print()
        print("  Ahora ejecuta:")
        print("    pip install -r requirements.txt")
    else:
        print(f"\n  {len(cambios)} cambio(s) pendientes. Pasa --apply para aplicar.")
else:
    ok("requirements.txt ya es compatible con Python 3.13")

# ═════════════════════════════════════════════════════════════════════════════
print("\n══════════════════════════════════════════════")
if APPLY:
    print("  ✅ Correcciones aplicadas.")
    print("  Próximos pasos:")
    print("    1. pip install -r requirements.txt")
    print("    2. python main.py")
else:
    print("  Preview completado. Pasa --apply para aplicar.")
print("══════════════════════════════════════════════\n")
