#!/usr/bin/env python3
"""
scripts/poblar_bd.py
Sincronización inicial: descarga licitaciones y órdenes de compra
de los últimos N días y puebla licitaciones_snapshot.

Uso:
  python scripts/poblar_bd.py           # últimos 7 días
  python scripts/poblar_bd.py --dias 30 # últimos 30 días
  python scripts/poblar_bd.py --tipo licitacion --dias 3

El script muestra progreso en tiempo real y un resumen final.
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

from datetime import datetime, timedelta
from app.database import init_db, get_db
from app.models import LicitacionSnapshot
from app.api_client import MercadoPublicoClient, QuotaExhaustedException
from app.normalizer import normalizar_entidad

GREEN = "\033[92m"; YELLOW = "\033[93m"; CYAN = "\033[96m"
RED   = "\033[91m"; BOLD   = "\033[1m";  NC   = "\033[0m"

def barra(n, total, largo=40):
    lleno = int(largo * n / max(total, 1))
    return f"[{'█'*lleno}{'░'*(largo-lleno)}] {n}/{total}"

def run(dias: int, tipos: list[str], dry_run: bool):
    print(f"\n{BOLD}{'═'*60}{NC}")
    print(f"{BOLD}  Poblar BD — Sincronización real con Mercado Público{NC}")
    print(f"{'═'*60}")
    print(f"  Período:  últimos {dias} días")
    print(f"  Tipos:    {', '.join(tipos)}")
    print(f"  Modo:     {'DRY RUN (sin escritura)' if dry_run else 'ESCRITURA REAL'}")
    print(f"{'═'*60}\n")

    init_db()
    client = MercadoPublicoClient()

    fecha_desde = datetime.now() - timedelta(days=dias)

    totales = {"insertados": 0, "actualizados": 0, "errores": 0, "omitidos": 0}

    for tipo in tipos:
        print(f"{CYAN}── {tipo.upper().replace('_',' ')} ──────────────────────────────────{NC}")
        fecha_str = client.to_api_date(fecha_desde)

        # Obtener iterador según tipo
        if tipo == "licitacion":
            iterador = client.iter_licitaciones(fecha_desde=fecha_str)
        elif tipo == "orden_compra":
            iterador = client.iter_ordenes_compra(fecha_desde=fecha_str)
        elif tipo == "compra_agil":
            iterador = client.iter_oportunidades(fecha_desde=fecha_str)
        else:
            print(f"  {RED}Tipo desconocido: {tipo}{NC}")
            continue

        lote = []
        n_vistos = 0
        print(f"  Descargando datos desde {fecha_str}…")

        try:
            for raw in iterador:
                n_vistos += 1
                entidad = normalizar_entidad(raw, tipo)
                if not entidad or not entidad.get("codigo"):
                    totales["errores"] += 1
                    continue
                lote.append(entidad)

                # Progreso cada 50 ítems
                if n_vistos % 50 == 0:
                    print(f"  \r  {barra(n_vistos, n_vistos+1)} descargados…", end="", flush=True)

                # Commit por lotes de 200 para no acumular demasiado en memoria
                if len(lote) >= 200 and not dry_run:
                    _guardar_lote(lote, tipo, totales)
                    lote = []

        except QuotaExhaustedException:
            print(f"\n  {YELLOW}⚠️  Cuota diaria agotada. Reintenta mañana.{NC}")
            break
        except KeyboardInterrupt:
            print(f"\n  {YELLOW}Interrumpido por el usuario.{NC}")
            break

        # Último lote
        if lote and not dry_run:
            _guardar_lote(lote, tipo, totales)
        elif dry_run and lote:
            print(f"\n  {YELLOW}[DRY RUN] Se habrían insertado/actualizado {len(lote)} registros{NC}")
            totales["insertados"] += len(lote)

        print(f"\n  {GREEN}✅ {tipo}: {n_vistos} descargados{NC}\n")

    # Resumen final
    print(f"{BOLD}{'═'*60}{NC}")
    print(f"{BOLD}  RESUMEN FINAL{NC}")
    print(f"{'─'*60}")
    print(f"  {GREEN}Insertados:   {totales['insertados']}{NC}")
    print(f"  {CYAN}Actualizados: {totales['actualizados']}{NC}")
    print(f"  Omitidos:     {totales['omitidos']}")
    if totales["errores"]:
        print(f"  {RED}Errores:      {totales['errores']}{NC}")

    with get_db() as db:
        total_bd = db.query(LicitacionSnapshot).count()
    print(f"  {BOLD}Total en BD:  {total_bd} registros{NC}")
    print(f"{'═'*60}\n")

    if totales["insertados"] > 0:
        print(f"  {GREEN}🚀 BD poblada correctamente.{NC}")
        print(f"     Ahora abre el dashboard y crea reglas,")
        print(f"     o ejecuta: python main.py --once\n")


def _guardar_lote(lote: list, tipo: str, totales: dict):
    with get_db() as db:
        for entidad in lote:
            codigo = entidad["codigo"]
            existing = db.query(LicitacionSnapshot).filter_by(codigo=codigo).first()
            if existing:
                existing.datos = entidad
                existing.region = entidad.get("region")
                existing.monto_clp = entidad.get("monto_clp")
                existing.estado = entidad.get("estado")
                if entidad.get("fecha_publicacion"):
                    try:
                        from datetime import datetime as dt
                        existing.fecha_publicacion = dt.strptime(
                            entidad["fecha_publicacion"], "%Y-%m-%d"
                        )
                    except Exception:
                        pass
                totales["actualizados"] += 1
            else:
                fp = None
                if entidad.get("fecha_publicacion"):
                    try:
                        from datetime import datetime as dt
                        fp = dt.strptime(entidad["fecha_publicacion"], "%Y-%m-%d")
                    except Exception:
                        pass
                snap = LicitacionSnapshot(
                    codigo=codigo, tipo=tipo,
                    datos=entidad,
                    region=entidad.get("region"),
                    monto_clp=entidad.get("monto_clp"),
                    estado=entidad.get("estado"),
                    fecha_publicacion=fp,
                )
                db.add(snap)
                totales["insertados"] += 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Poblar BD con datos reales de Mercado Público")
    p.add_argument("--dias",    type=int, default=7,
                   help="Días hacia atrás a sincronizar (default: 7)")
    p.add_argument("--tipo",    nargs="+",
                   choices=["licitacion","orden_compra","compra_agil"],
                   default=["licitacion","orden_compra"],
                   help="Tipos a sincronizar")
    p.add_argument("--dry-run", action="store_true",
                   help="Mostrar cuánto se descargaría sin escribir en BD")
    args = p.parse_args()
    run(args.dias, args.tipo, args.dry_run)
