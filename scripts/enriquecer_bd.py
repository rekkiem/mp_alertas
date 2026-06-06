#!/usr/bin/env python3
"""
scripts/enriquecer_bd.py
Enriquece los snapshots existentes que solo tienen datos del listado
(sin región, monto, organismo) llamando al endpoint de detalle por cada código.

Uso:
  python scripts/enriquecer_bd.py                # enriquece todos los que faltan datos
  python scripts/enriquecer_bd.py --limite 500   # solo los primeros 500
  python scripts/enriquecer_bd.py --dry-run      # ver cuántos sin enriquecer
"""
import sys, os, argparse, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.WARNING)

from app.database import init_db, get_db
from app.models import LicitacionSnapshot
from app.api_client import MercadoPublicoClient, QuotaExhaustedException
from app.normalizer import normalizar_licitacion, normalizar_orden_compra

G="\033[92m"; Y="\033[93m"; C="\033[96m"; R="\033[91m"; B="\033[1m"; NC="\033[0m"

def run(limite, dry_run, solo_sin_region):
    init_db()
    client = MercadoPublicoClient()

    with get_db() as db:
        q = db.query(LicitacionSnapshot)
        if solo_sin_region:
            q = q.filter(LicitacionSnapshot.region.is_(None))
        total_pendiente = q.count()
        pendientes = q.limit(limite).all() if limite else q.all()
        codigos = [(s.id, s.codigo, s.tipo) for s in pendientes]

    print(f"\n{B}Enriquecer BD — fetch detalle por código{NC}")
    print(f"  Pendientes : {total_pendiente:,} snapshots sin región/monto")
    print(f"  A procesar : {len(codigos):,}")
    print(f"  Modo       : {'DRY RUN' if dry_run else 'ESCRITURA'}\n")

    if dry_run:
        print(f"  {Y}DRY RUN: se necesitan {len(codigos)} llamadas a la API.{NC}")
        print(f"  Tiempo estimado: ~{len(codigos)//5//60}m {len(codigos)//5%60}s (5 req/seg)\n")
        return

    ok = err = omit = 0
    for i, (snap_id, codigo, tipo) in enumerate(codigos, 1):
        try:
            if tipo == "licitacion":
                detalle = client.get_licitacion_detalle(codigo)
                if not detalle:
                    omit += 1; continue
                norm = normalizar_licitacion(detalle)
            else:
                # Para OC el endpoint de detalle usa codigo directamente
                data = client._get("publico/ordenesdecompra.json", {"codigo": codigo})
                listado = (data.get("Listado") or [])
                if not listado:
                    omit += 1; continue
                norm = normalizar_orden_compra(listado[0])

            from datetime import datetime as dt
            fp = None
            if norm.get("fecha_publicacion"):
                try: fp = dt.strptime(norm["fecha_publicacion"], "%Y-%m-%d")
                except Exception: pass

            with get_db() as db:
                db.query(LicitacionSnapshot).filter_by(id=snap_id).update({
                    "datos":     norm,
                    "region":    norm.get("region"),
                    "monto_clp": norm.get("monto_clp"),
                    "estado":    norm.get("estado"),
                    "fecha_publicacion": fp,
                })
            ok += 1

        except QuotaExhaustedException:
            print(f"\n  {R}Cuota diaria agotada en el ítem {i}. Reintenta mañana.{NC}")
            break
        except Exception as e:
            err += 1
            if err <= 5:
                print(f"  {R}Error {codigo}: {e}{NC}")

        if i % 50 == 0 or i == len(codigos):
            pct = i / len(codigos) * 100
            bar = "█" * int(pct/2.5) + "░" * (40 - int(pct/2.5))
            print(f"\r  [{bar}] {i:,}/{len(codigos):,} ({pct:.0f}%) ✅{ok} ❌{err}", end="", flush=True)

    print(f"\n\n  {G}✅ Enriquecidos: {ok:,}{NC}  ❌ Errores: {err}  ⏭ Omitidos: {omit}")
    with get_db() as db:
        sin_region = db.query(LicitacionSnapshot).filter(LicitacionSnapshot.region.is_(None)).count()
    print(f"  Quedan sin región: {sin_region:,}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limite",         type=int, default=0, help="Máx a procesar (0=todos)")
    p.add_argument("--dry-run",        action="store_true")
    p.add_argument("--solo-sin-region",action="store_true", default=True,
                   help="Solo procesar snapshots sin región (default: True)")
    args = p.parse_args()
    run(args.limite or None, args.dry_run, args.solo_sin_region)
