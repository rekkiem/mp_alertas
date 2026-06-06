#!/usr/bin/env python3
"""
scripts/poblar_bd.py — Sincronización inicial con Mercado Público.

La API de listado solo devuelve: CodigoExterno, Nombre, CodigoEstado, FechaCierre.
Los campos region, monto, fecha_publicacion, organismo requieren llamadas de detalle.
Usa --enriquecer para obtener datos completos (más lento, más llamadas a la API).

Uso:
  python scripts/poblar_bd.py                            # hoy, licitaciones+OC
  python scripts/poblar_bd.py --dias 7                  # últimos 7 días
  python scripts/poblar_bd.py --tipo licitacion --enriquecer  # con datos completos
  python scripts/poblar_bd.py --dry-run                 # sin escribir
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from datetime import datetime, timedelta
from app.database import init_db, get_db
from app.models import LicitacionSnapshot
from app.api_client import MercadoPublicoClient, QuotaExhaustedException
from app.normalizer import normalizar_entidad

G="\033[92m"; Y="\033[93m"; C="\033[96m"; R="\033[91m"; B="\033[1m"; NC="\033[0m"

LIMITES_DEFAULT = {"licitacion": 20_000, "orden_compra": 5_000, "compra_agil": 0}


def run(dias, tipos, max_reg, dry_run, enriquecer):
    print(f"\n{B}{'═'*58}{NC}")
    print(f"{B}  Poblar BD — Mercado Público{NC}")
    print(f"{'─'*58}")
    print(f"  Período   : últimos {dias} día(s)   Modo: {'DRY RUN' if dry_run else 'ESCRITURA'}")
    print(f"  Tipos     : {', '.join(tipos)}")
    print(f"  Enriquecer: {'Sí (detalle por ítem)' if enriquecer else 'No (solo datos de listado)'}")
    print(f"{'═'*58}\n")

    init_db()
    client  = MercadoPublicoClient()
    fecha_d = datetime.now() - timedelta(days=dias)
    fecha_s = client.to_api_date(fecha_d)
    totales = {"insertados": 0, "actualizados": 0, "omitidos": 0, "errores": 0, "enriquecidos": 0}

    for tipo in tipos:
        if tipo == "compra_agil":
            print(f"{Y}── COMPRA_AGIL: omitida (API no soporta tipo=CO, retorna HTTP 400){NC}\n")
            continue

        limite = min(max_reg, LIMITES_DEFAULT[tipo]) if max_reg > 0 else LIMITES_DEFAULT[tipo]
        print(f"{C}── {tipo.upper().replace('_',' ')} (hasta {limite:,} registros) ──────────────{NC}")

        iterador = (client.iter_licitaciones(fecha_desde=fecha_s)
                    if tipo == "licitacion"
                    else client.iter_ordenes_compra(fecha_desde=fecha_s))

        lote, seen, n, detenido = [], set(), 0, False

        try:
            for raw in iterador:
                entidad = normalizar_entidad(raw, tipo)
                if not entidad:
                    totales["errores"] += 1; continue

                codigo = entidad.get("codigo", "")
                if not codigo or codigo in seen:
                    totales["omitidos"] += 1; continue
                seen.add(codigo)

                # Enriquecimiento opcional: fetch detalle para obtener región/monto
                if enriquecer and not entidad.get("_enriquecido") and tipo == "licitacion":
                    detalle = client.get_licitacion_detalle(codigo)
                    if detalle:
                        from app.normalizer import normalizar_licitacion
                        entidad = normalizar_licitacion(detalle)
                        totales["enriquecidos"] += 1

                lote.append(entidad)
                n += 1

                if n % 100 == 0:
                    print(f"\r  {'█'*min(40,n//100)}{'░'*max(0,40-n//100)} {n:,}…", end="", flush=True)

                if len(lote) >= 500 and not dry_run:
                    _guardar(lote, tipo, totales); lote = []

                if n >= limite:
                    print(f"\n  {Y}Límite {limite:,} alcanzado.{NC}"); detenido = True; break

        except QuotaExhaustedException:
            print(f"\n  {R}Cuota API agotada. Reintenta mañana.{NC}"); break
        except KeyboardInterrupt:
            print(f"\n  {Y}Interrumpido.{NC}"); break

        if lote and not dry_run:
            _guardar(lote, tipo, totales)
        elif dry_run:
            totales["insertados"] += len(lote)

        sym = f"{G}✅{NC}" if not detenido else f"{Y}⚠️ {NC}"
        print(f"\n  {sym} {tipo}: {n:,} procesados\n")

    # Resumen
    with get_db() as db:
        total_bd = db.query(LicitacionSnapshot).count()
    print(f"{B}{'═'*58}{NC}")
    print(f"  {G}Insertados  : {totales['insertados']:,}{NC}")
    print(f"  {C}Actualizados: {totales['actualizados']:,}{NC}")
    if totales["enriquecidos"]:
        print(f"  {C}Enriquecidos: {totales['enriquecidos']:,}{NC}")
    print(f"  Omitidos    : {totales['omitidos']:,}")
    print(f"  {B}Total en BD : {total_bd:,}{NC}")
    print(f"{'═'*58}")
    if not dry_run and total_bd > 0:
        print(f"\n  {G}→ python main.py --once   para evaluar reglas{NC}\n")


def _guardar(lote, tipo, totales):
    from datetime import datetime as dt
    with get_db() as db:
        # Cargar los codigos existentes en un set para evitar N queries
        codigos = [e["codigo"] for e in lote if e.get("codigo")]
        existentes = {
            r.codigo for r in
            db.query(LicitacionSnapshot.codigo)
              .filter(LicitacionSnapshot.codigo.in_(codigos)).all()
        }
        for entidad in lote:
            codigo = entidad.get("codigo", "")
            if not codigo:
                totales["omitidos"] += 1; continue
            fp = None
            if entidad.get("fecha_publicacion"):
                try: fp = dt.strptime(entidad["fecha_publicacion"], "%Y-%m-%d")
                except Exception: pass
            elif entidad.get("fecha_cierre"):
                try: fp = dt.strptime(entidad["fecha_cierre"], "%Y-%m-%d")
                except Exception: pass

            if codigo in existentes:
                db.query(LicitacionSnapshot).filter_by(codigo=codigo).update({
                    "datos":     entidad,
                    "region":    entidad.get("region"),
                    "monto_clp": entidad.get("monto_clp"),
                    "estado":    entidad.get("estado"),
                    "fecha_publicacion": fp,
                })
                totales["actualizados"] += 1
            else:
                db.add(LicitacionSnapshot(
                    codigo=codigo, tipo=tipo, datos=entidad,
                    region=entidad.get("region"),
                    monto_clp=entidad.get("monto_clp"),
                    estado=entidad.get("estado"),
                    fecha_publicacion=fp,
                ))
                existentes.add(codigo)   # evitar duplicado dentro del mismo lote
                totales["insertados"] += 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Poblar BD con datos reales de Mercado Público")
    p.add_argument("--dias",       type=int, default=1)
    p.add_argument("--tipo",       nargs="+",
                   choices=["licitacion","orden_compra"],
                   default=["licitacion","orden_compra"])
    p.add_argument("--max",        type=int, default=0,
                   help="Máx registros por tipo (0=usar límite por defecto)")
    p.add_argument("--enriquecer", action="store_true",
                   help="Fetch detalle por cada ítem para obtener region/monto (lento)")
    p.add_argument("--dry-run",    action="store_true")
    args = p.parse_args()
    run(args.dias, args.tipo, args.max, args.dry_run, args.enriquecer)
