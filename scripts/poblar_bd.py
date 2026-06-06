#!/usr/bin/env python3
"""
scripts/poblar_bd.py — Sincronización inicial con Mercado Público.

Uso:
  python scripts/poblar_bd.py                       # hoy, licitaciones+OC, max 5000 c/u
  python scripts/poblar_bd.py --dias 7              # últimos 7 días
  python scripts/poblar_bd.py --tipo licitacion --dias 3 --max 10000
  python scripts/poblar_bd.py --tipo orden_compra --dias 1 --max 2000
  python scripts/poblar_bd.py --dry-run             # mostrar sin escribir

Notas:
  - compra_agil: la API no soporta filtro de fecha directo, se filtra en memoria.
  - orden_compra con --dias grandes puede devolver millones de registros;
    usa --max para limitar (recomendado: 5000 para primer uso).
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from datetime import datetime, timedelta
from app.database import init_db, get_db
from app.models import LicitacionSnapshot
from app.api_client import MercadoPublicoClient, QuotaExhaustedException
from app.normalizer import normalizar_entidad

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"; R = "\033[91m"; B = "\033[1m"; NC = "\033[0m"

# Límites por defecto por tipo (registros máximos por ejecución)
LIMITES_DEFAULT = {
    "licitacion":   20_000,   # ~1-2 min descarga
    "orden_compra":  5_000,   # OC puede tener millones históricos → limitar siempre
    "compra_agil":   2_000,   # filtro en memoria, no tan eficiente
}

def barra(n, ancho=42):
    pct = min(n / max(ancho, 1), 1.0)
    lleno = int(ancho * pct)
    return f"[{'█'*lleno}{'░'*(ancho-lleno)}]"

def run(dias: int, tipos: list, max_reg: int, dry_run: bool):
    print(f"\n{B}{'═'*58}{NC}")
    print(f"{B}  Poblar BD — Mercado Público{NC}")
    print(f"{'─'*58}")
    print(f"  Período : últimos {dias} día(s)")
    print(f"  Tipos   : {', '.join(tipos)}")
    print(f"  Máx/tipo: {max_reg:,} registros")
    print(f"  Modo    : {'DRY RUN' if dry_run else 'ESCRITURA REAL'}")
    print(f"{'═'*58}\n")

    init_db()
    client = MercadoPublicoClient()
    fecha_desde = datetime.now() - timedelta(days=dias)
    fecha_str   = client.to_api_date(fecha_desde)
    totales     = {"insertados": 0, "actualizados": 0, "omitidos": 0, "errores": 0}

    for tipo in tipos:
        limite = min(max_reg, LIMITES_DEFAULT[tipo]) if max_reg > 0 else LIMITES_DEFAULT[tipo]
        print(f"{C}── {tipo.upper().replace('_',' ')} (límite: {limite:,}) ──────────────────{NC}")

        if tipo == "compra_agil":
            print(f"  {Y}Nota: compra_agil no acepta filtro de fecha en la API.")
            print(f"        Se descarga por estado=publicada y se filtra en memoria.{NC}")
            iterador = client.iter_oportunidades(fecha_desde=fecha_str)
        elif tipo == "licitacion":
            iterador = client.iter_licitaciones(fecha_desde=fecha_str)
        elif tipo == "orden_compra":
            iterador = client.iter_ordenes_compra(fecha_desde=fecha_str)
        else:
            print(f"  {R}Tipo desconocido: {tipo}{NC}"); continue

        lote      = []
        n_vistos  = 0
        n_limite  = 0   # cuántos pasaron el filtro de fecha
        detenido  = False

        print(f"  Descargando desde {fecha_str}…")

        try:
            for raw in iterador:
                n_vistos += 1

                entidad = normalizar_entidad(raw, tipo)
                if not entidad or not entidad.get("codigo"):
                    totales["errores"] += 1
                    continue

                lote.append(entidad)
                n_limite += 1

                # Progreso cada 100 ítems
                if n_limite % 100 == 0:
                    print(f"  \r  {barra(n_limite)} {n_limite:,} registros…", end="", flush=True)

                # Commit cada 500 en BD
                if len(lote) >= 500 and not dry_run:
                    _guardar_lote(lote, tipo, totales)
                    lote = []

                # Parar al alcanzar el límite
                if n_limite >= limite:
                    print(f"\n  {Y}Límite de {limite:,} alcanzado — deteniendo descarga.{NC}")
                    print(f"  Para más registros usa: --max {limite*2} o --dias {dias-1}")
                    detenido = True
                    break

        except QuotaExhaustedException:
            print(f"\n  {R}Cuota diaria de API agotada. Reintenta mañana.{NC}")
            break
        except KeyboardInterrupt:
            print(f"\n  {Y}Interrumpido por usuario (Ctrl+C).{NC}")
            break

        # Último lote
        if lote:
            if dry_run:
                totales["insertados"] += len(lote)
                print(f"\n  {Y}[DRY RUN] Se insertarían/actualizarían {len(lote)} más.{NC}")
            else:
                _guardar_lote(lote, tipo, totales)

        estado_str = f"{G}✅{NC}" if not detenido else f"{Y}⚠️ {NC}"
        print(f"\n  {estado_str} {tipo}: {n_limite:,} procesados ({n_vistos:,} descargados de la API)\n")

    # Resumen
    print(f"{B}{'═'*58}{NC}")
    print(f"{B}  RESUMEN{NC}")
    print(f"{'─'*58}")
    print(f"  {G}Insertados  : {totales['insertados']:,}{NC}")
    print(f"  {C}Actualizados: {totales['actualizados']:,}{NC}")
    print(f"  Omitidos    : {totales['omitidos']:,}")
    if totales["errores"]:
        print(f"  {R}Errores     : {totales['errores']:,}{NC}")
    with get_db() as db:
        total_bd = db.query(LicitacionSnapshot).count()
    print(f"  {B}Total en BD : {total_bd:,} registros{NC}")
    print(f"{'═'*58}")

    if not dry_run and totales["insertados"] > 0:
        print(f"\n  {G}BD poblada. Ejecuta las reglas desde el dashboard")
        print(f"  o con: python main.py --once{NC}\n")
    elif totales["insertados"] == 0:
        print(f"\n  {Y}No se insertaron datos. Verifica el ticket y los parámetros.{NC}\n")


def _guardar_lote(lote: list, tipo: str, totales: dict):
    from datetime import datetime as dt
    with get_db() as db:
        for entidad in lote:
            codigo = entidad.get("codigo", "")
            if not codigo:
                totales["omitidos"] += 1
                continue
            existing = db.query(LicitacionSnapshot).filter_by(codigo=codigo).first()
            fp = None
            if entidad.get("fecha_publicacion"):
                try:
                    fp = dt.strptime(entidad["fecha_publicacion"], "%Y-%m-%d")
                except Exception:
                    pass
            if existing:
                existing.datos      = entidad
                existing.region     = entidad.get("region")
                existing.monto_clp  = entidad.get("monto_clp")
                existing.estado     = entidad.get("estado")
                if fp:
                    existing.fecha_publicacion = fp
                totales["actualizados"] += 1
            else:
                db.add(LicitacionSnapshot(
                    codigo=codigo, tipo=tipo, datos=entidad,
                    region=entidad.get("region"),
                    monto_clp=entidad.get("monto_clp"),
                    estado=entidad.get("estado"),
                    fecha_publicacion=fp,
                ))
                totales["insertados"] += 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Poblar BD con datos reales de Mercado Público",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/poblar_bd.py                          # hoy, límite 5000 OC / 20000 Lic
  python scripts/poblar_bd.py --dias 7 --max 10000     # 7 días, hasta 10000 por tipo
  python scripts/poblar_bd.py --tipo licitacion        # solo licitaciones
  python scripts/poblar_bd.py --dry-run                # ver cuánto bajaría sin escribir
        """
    )
    p.add_argument("--dias",    type=int, default=1,
                   help="Días hacia atrás (default: 1 = solo hoy)")
    p.add_argument("--tipo",    nargs="+",
                   choices=["licitacion", "orden_compra", "compra_agil"],
                   default=["licitacion", "orden_compra"],
                   help="Tipos a sincronizar (default: licitacion orden_compra)")
    p.add_argument("--max",     type=int, default=0,
                   help="Máximo de registros por tipo (0 = usar límites por defecto)")
    p.add_argument("--dry-run", action="store_true",
                   help="Mostrar sin escribir en BD")
    args = p.parse_args()
    run(args.dias, args.tipo, args.max, args.dry_run)
