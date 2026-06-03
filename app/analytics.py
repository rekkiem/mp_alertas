"""
app/analytics.py — Motor de insights estratégicos.

Analiza los datos sincronizados en licitaciones_snapshot para generar:
  1. Top categorías de producto por volumen y monto
  2. Organismos compradores recurrentes y su comportamiento
  3. Análisis de estados / reclamos (órdenes de compra)
  4. Correlaciones temporales, geográficas y por monto
  5. Detección de nichos de mercado
  6. KPIs ejecutivos globales
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text, func, and_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LicitacionSnapshot, AlertaGenerada, ReglaUsuario

logger = logging.getLogger(__name__)


# ── Nombre de regiones de Chile (código → nombre) ─────────────────────────────

REGIONES_CHILE: Dict[int, str] = {
    1: "Tarapacá",
    2: "Antofagasta",
    3: "Atacama",
    4: "Coquimbo",
    5: "Valparaíso",
    6: "O'Higgins",
    7: "Maule",
    8: "Biobío",
    9: "La Araucanía",
    10: "Los Lagos",
    11: "Aysén",
    12: "Magallanes",
    13: "Región Metropolitana",
    14: "Los Ríos",
    15: "Arica y Parinacota",
    16: "Ñuble",
}

# Categorías ONU conocidas (para enriquecer labels)
CATEGORIAS_ONU: Dict[str, str] = {
    "43200000": "Tecnología de la Información",
    "43210000": "Computadores y periféricos",
    "43220000": "Software",
    "72000000": "Servicios TI",
    "80100000": "Educación",
    "80110000": "Enseñanza primaria",
    "85000000": "Servicios de salud",
    "72500000": "Servicios computacionales",
    "30200000": "Equipos de oficina",
    "48100000": "Plataformas industriales",
    "77100000": "Servicios agrícolas",
    "72600000": "Soporte TI",
    "45000000": "Construcción",
    "92000000": "Servicios comunitarios",
    "95000000": "Tierras y edificios",
}


# ── Helper ─────────────────────────────────────────────────────────────────────

def _dias_atras(dias: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)


def _monto_fmt(monto: Optional[int]) -> str:
    if monto is None:
        return "N/D"
    if monto >= 1_000_000_000:
        return f"${monto / 1_000_000_000:.1f}B"
    if monto >= 1_000_000:
        return f"${monto / 1_000_000:.1f}M"
    if monto >= 1_000:
        return f"${monto / 1_000:.0f}K"
    return f"${monto:,}"


def _safe_rows(db: Session, stmt) -> List:
    """Ejecuta una consulta y retorna filas; captura errores silenciosamente."""
    try:
        return db.execute(stmt).fetchall()
    except Exception as e:
        logger.error("Analytics query error: %s", e)
        return []


# ── AnalyticsEngine ───────────────────────────────────────────────────────────

class AnalyticsEngine:
    """
    Centraliza todos los análisis estratégicos.
    Cada método retorna datos listos para serializar a JSON (solo tipos primitivos).
    """

    # ── KPIs globales ─────────────────────────────────────────────────────────

    def resumen_ejecutivo(self, dias: int = 30) -> Dict:
        """
        KPIs globales: totales, montos, variación respecto al período anterior.
        """
        with get_db() as db:
            fecha_desde = _dias_atras(dias)
            fecha_anterior = _dias_atras(dias * 2)

            def _totales(desde: datetime, hasta: datetime) -> Tuple[int, int, int]:
                rows = _safe_rows(db, text("""
                    SELECT
                        COUNT(*)                                         AS total,
                        COALESCE(SUM(monto_clp), 0)                     AS monto,
                        COUNT(DISTINCT json_extract(datos, '$.organismo')) AS organismos
                    FROM licitaciones_snapshot
                    WHERE fecha_sincronizacion BETWEEN :desde AND :hasta
                """).bindparams(
                    desde=desde.isoformat(),
                    hasta=hasta.isoformat()
                ))
                if rows:
                    r = rows[0]
                    return int(r[0] or 0), int(r[1] or 0), int(r[2] or 0)
                return 0, 0, 0

            ahora = datetime.now(timezone.utc).replace(tzinfo=None)
            total, monto, orgs = _totales(fecha_desde, ahora)
            total_ant, monto_ant, orgs_ant = _totales(fecha_anterior, fecha_desde)

            def _var(a, b):
                if b == 0:
                    return None
                return round((a - b) / b * 100, 1)

            # Alertas generadas en el período
            alertas = db.query(func.count(AlertaGenerada.id)).filter(
                AlertaGenerada.fecha_alerta >= fecha_desde
            ).scalar() or 0

            # Reglas activas
            reglas = db.query(func.count(ReglaUsuario.id)).filter(
                ReglaUsuario.activa == True
            ).scalar() or 0

            # Última sincronización
            ultima_sinc = db.query(func.max(LicitacionSnapshot.fecha_sincronizacion)).scalar()

            return {
                "periodo_dias": dias,
                "total_entidades": total,
                "monto_total_clp": monto,
                "monto_total_fmt": _monto_fmt(monto),
                "organismos_activos": orgs,
                "alertas_periodo": alertas,
                "reglas_activas": reglas,
                "ultima_sincronizacion": ultima_sinc.isoformat() if ultima_sinc else None,
                "variacion_entidades_pct": _var(total, total_ant),
                "variacion_monto_pct": _var(monto, monto_ant),
                "variacion_organismos_pct": _var(orgs, orgs_ant),
            }

    # ── Top categorías ────────────────────────────────────────────────────────

    def top_categorias(
        self,
        limit: int = 15,
        tipo: Optional[str] = None,
        dias: int = 365,
    ) -> List[Dict]:
        """
        Categorías de producto más demandadas por el Estado.
        Extrae `codigo_producto` del JSON datos y agrega por código.
        """
        with get_db() as db:
            fecha_desde = _dias_atras(dias)

            filtro_tipo = "AND tipo = :tipo" if tipo else ""
            sql = text(f"""
                SELECT
                    prod.value                                       AS codigo,
                    COUNT(*)                                         AS conteo,
                    COALESCE(SUM(s.monto_clp), 0)                   AS monto_total,
                    COALESCE(AVG(s.monto_clp), 0)                   AS monto_promedio,
                    COUNT(DISTINCT json_extract(s.datos, '$.organismo')) AS organismos_distintos
                FROM licitaciones_snapshot s,
                     json_each(s.datos, '$.codigo_producto') AS prod
                WHERE s.fecha_publicacion >= :desde
                  AND prod.value IS NOT NULL
                  AND prod.value != ''
                  {filtro_tipo}
                GROUP BY prod.value
                ORDER BY conteo DESC
                LIMIT :limit
            """).bindparams(
                desde=fecha_desde.isoformat(),
                limit=limit,
                **({"tipo": tipo} if tipo else {}),
            )

            rows = _safe_rows(db, sql)

            result = []
            for r in rows:
                codigo = str(r[0])
                result.append({
                    "codigo": codigo,
                    "nombre": CATEGORIAS_ONU.get(codigo, f"Categoría {codigo}"),
                    "conteo": int(r[1]),
                    "monto_total": int(r[2]),
                    "monto_total_fmt": _monto_fmt(int(r[2])),
                    "monto_promedio": int(r[3]),
                    "monto_promedio_fmt": _monto_fmt(int(r[3])),
                    "organismos_distintos": int(r[4]),
                })

            # Fallback Python si json_each no está disponible (SQLite < 3.38)
            if not result:
                result = self._top_categorias_python(db, fecha_desde, tipo, limit)

            return result

    def _top_categorias_python(
        self, db: Session, fecha_desde: datetime, tipo: Optional[str], limit: int
    ) -> List[Dict]:
        """Fallback: agrega categorías en Python cuando json_each no está disponible."""
        q = db.query(LicitacionSnapshot).filter(
            LicitacionSnapshot.fecha_publicacion >= fecha_desde
        )
        if tipo:
            q = q.filter(LicitacionSnapshot.tipo == tipo)

        categorias: Dict[str, Dict] = defaultdict(
            lambda: {"conteo": 0, "monto_total": 0, "organismos": set()}
        )

        for snap in q.yield_per(500):
            datos = snap.datos or {}
            for cod in (datos.get("codigo_producto") or []):
                cod = str(cod)
                categorias[cod]["conteo"] += 1
                categorias[cod]["monto_total"] += snap.monto_clp or 0
                org = datos.get("organismo")
                if org:
                    categorias[cod]["organismos"].add(org)

        result = []
        for cod, agg in sorted(categorias.items(), key=lambda x: -x[1]["conteo"])[:limit]:
            monto_prom = (agg["monto_total"] // agg["conteo"]) if agg["conteo"] else 0
            result.append({
                "codigo": cod,
                "nombre": CATEGORIAS_ONU.get(cod, f"Categoría {cod}"),
                "conteo": agg["conteo"],
                "monto_total": agg["monto_total"],
                "monto_total_fmt": _monto_fmt(agg["monto_total"]),
                "monto_promedio": monto_prom,
                "monto_promedio_fmt": _monto_fmt(monto_prom),
                "organismos_distintos": len(agg["organismos"]),
            })
        return result

    # ── Top organismos compradores ────────────────────────────────────────────

    def top_organismos(
        self,
        limit: int = 15,
        tipo: Optional[str] = None,
        dias: int = 365,
    ) -> List[Dict]:
        """
        Organismos públicos con mayor volumen de compra.
        Detecta compradores recurrentes y su comportamiento.
        """
        with get_db() as db:
            fecha_desde = _dias_atras(dias)
            filtro_tipo = "AND tipo = :tipo" if tipo else ""

            sql = text(f"""
                SELECT
                    json_extract(datos, '$.organismo')              AS organismo,
                    COUNT(*)                                         AS conteo,
                    COALESCE(SUM(monto_clp), 0)                     AS monto_total,
                    COALESCE(AVG(monto_clp), 0)                     AS monto_promedio,
                    COALESCE(MIN(monto_clp), 0)                     AS monto_min,
                    COALESCE(MAX(monto_clp), 0)                     AS monto_max,
                    MAX(fecha_publicacion)                           AS ultima_compra,
                    region
                FROM licitaciones_snapshot
                WHERE fecha_publicacion >= :desde
                  AND json_extract(datos, '$.organismo') IS NOT NULL
                  {filtro_tipo}
                GROUP BY organismo
                ORDER BY monto_total DESC
                LIMIT :limit
            """).bindparams(
                desde=fecha_desde.isoformat(),
                limit=limit,
                **({"tipo": tipo} if tipo else {}),
            )

            rows = _safe_rows(db, sql)

            if rows:
                return [
                    {
                        "organismo": r[0],
                        "conteo": int(r[1]),
                        "monto_total": int(r[2]),
                        "monto_total_fmt": _monto_fmt(int(r[2])),
                        "monto_promedio": int(r[3]),
                        "monto_promedio_fmt": _monto_fmt(int(r[3])),
                        "monto_min": int(r[4]),
                        "monto_max": int(r[5]),
                        "ultima_compra": str(r[6]) if r[6] else None,
                        "region": r[7],
                        "nombre_region": REGIONES_CHILE.get(r[7], "Desconocida") if r[7] else None,
                        "frecuencia_diaria": round(int(r[1]) / max(dias, 1), 2),
                    }
                    for r in rows
                ]

            # Fallback Python
            return self._top_organismos_python(db, fecha_desde, tipo, limit)

    def _top_organismos_python(
        self, db: Session, fecha_desde: datetime, tipo: Optional[str], limit: int
    ) -> List[Dict]:
        q = db.query(LicitacionSnapshot).filter(
            LicitacionSnapshot.fecha_publicacion >= fecha_desde
        )
        if tipo:
            q = q.filter(LicitacionSnapshot.tipo == tipo)

        orgs: Dict[str, Dict] = defaultdict(
            lambda: {"conteo": 0, "monto_total": 0, "montos": [], "ultima": None, "region": None}
        )

        for snap in q.yield_per(500):
            datos = snap.datos or {}
            org = datos.get("organismo")
            if not org:
                continue
            orgs[org]["conteo"] += 1
            orgs[org]["monto_total"] += snap.monto_clp or 0
            if snap.monto_clp:
                orgs[org]["montos"].append(snap.monto_clp)
            if snap.fecha_publicacion:
                if not orgs[org]["ultima"] or snap.fecha_publicacion > orgs[org]["ultima"]:
                    orgs[org]["ultima"] = snap.fecha_publicacion
            if snap.region:
                orgs[org]["region"] = snap.region

        result = []
        for org, agg in sorted(orgs.items(), key=lambda x: -x[1]["monto_total"])[:limit]:
            mts = agg["montos"]
            prom = int(sum(mts) / len(mts)) if mts else 0
            result.append({
                "organismo": org,
                "conteo": agg["conteo"],
                "monto_total": agg["monto_total"],
                "monto_total_fmt": _monto_fmt(agg["monto_total"]),
                "monto_promedio": prom,
                "monto_promedio_fmt": _monto_fmt(prom),
                "monto_min": min(mts) if mts else 0,
                "monto_max": max(mts) if mts else 0,
                "ultima_compra": agg["ultima"].isoformat() if agg["ultima"] else None,
                "region": agg["region"],
                "nombre_region": REGIONES_CHILE.get(agg["region"]) if agg["region"] else None,
            })
        return result

    # ── Distribución regional ─────────────────────────────────────────────────

    def distribucion_regional(
        self,
        tipo: Optional[str] = None,
        dias: int = 365,
    ) -> List[Dict]:
        """Distribución de licitaciones y montos por región."""
        with get_db() as db:
            fecha_desde = _dias_atras(dias)
            q = db.query(
                LicitacionSnapshot.region,
                func.count(LicitacionSnapshot.id),
                func.coalesce(func.sum(LicitacionSnapshot.monto_clp), 0),
                func.coalesce(func.avg(LicitacionSnapshot.monto_clp), 0),
            ).filter(
                LicitacionSnapshot.fecha_publicacion >= fecha_desde,
                LicitacionSnapshot.region.isnot(None),
            )
            if tipo:
                q = q.filter(LicitacionSnapshot.tipo == tipo)

            rows = q.group_by(LicitacionSnapshot.region).order_by(
                func.count(LicitacionSnapshot.id).desc()
            ).all()

            total_entidades = sum(r[1] for r in rows) or 1

            return [
                {
                    "region": r[0],
                    "nombre_region": REGIONES_CHILE.get(r[0], f"Región {r[0]}"),
                    "conteo": int(r[1]),
                    "pct_conteo": round(int(r[1]) / total_entidades * 100, 1),
                    "monto_total": int(r[2]),
                    "monto_total_fmt": _monto_fmt(int(r[2])),
                    "monto_promedio": int(r[3]),
                    "monto_promedio_fmt": _monto_fmt(int(r[3])),
                }
                for r in rows
            ]

    # ── Tendencia temporal ────────────────────────────────────────────────────

    def tendencia_temporal(
        self,
        tipo: Optional[str] = None,
        meses: int = 12,
    ) -> List[Dict]:
        """
        Serie temporal mensual de cantidad y monto.
        Útil para detectar estacionalidad y picos de compra.
        """
        with get_db() as db:
            fecha_desde = _dias_atras(meses * 30)

            sql = text("""
                SELECT
                    strftime('%Y-%m', fecha_publicacion) AS mes,
                    COUNT(*)                              AS conteo,
                    COALESCE(SUM(monto_clp), 0)          AS monto_total,
                    COALESCE(AVG(monto_clp), 0)          AS monto_promedio
                FROM licitaciones_snapshot
                WHERE fecha_publicacion >= :desde
                  AND fecha_publicacion IS NOT NULL
                GROUP BY mes
                ORDER BY mes ASC
            """).bindparams(desde=fecha_desde.isoformat())

            rows = _safe_rows(db, sql)

            result = [
                {
                    "mes": r[0],
                    "conteo": int(r[1]),
                    "monto_total": int(r[2]),
                    "monto_total_fmt": _monto_fmt(int(r[2])),
                    "monto_promedio": int(r[3]),
                }
                for r in rows
            ]

            # Calcular variación MoM
            for i in range(1, len(result)):
                prev = result[i - 1]["conteo"]
                curr = result[i]["conteo"]
                result[i]["variacion_conteo_pct"] = (
                    round((curr - prev) / prev * 100, 1) if prev else None
                )

            if result:
                result[0]["variacion_conteo_pct"] = None

            return result

    # ── Tendencia diaria (últimos 30 días) ────────────────────────────────────

    def tendencia_diaria(self, dias: int = 30) -> List[Dict]:
        """Serie diaria de los últimos N días (útil para heatmap de actividad)."""
        with get_db() as db:
            fecha_desde = _dias_atras(dias)
            sql = text("""
                SELECT
                    DATE(fecha_publicacion)   AS dia,
                    COUNT(*)                  AS conteo,
                    COALESCE(SUM(monto_clp), 0) AS monto_total
                FROM licitaciones_snapshot
                WHERE fecha_publicacion >= :desde
                  AND fecha_publicacion IS NOT NULL
                GROUP BY dia
                ORDER BY dia ASC
            """).bindparams(desde=fecha_desde.isoformat())

            rows = _safe_rows(db, sql)
            return [
                {"dia": r[0], "conteo": int(r[1]), "monto_total": int(r[2])}
                for r in rows
            ]

    # ── Análisis de estados / reclamos ────────────────────────────────────────

    def analisis_estados(
        self,
        tipo: str = "orden_compra",
        dias: int = 365,
    ) -> Dict:
        """
        Distribución de estados de órdenes de compra.
        Detecta posibles anomalías: altas tasas de cancelación, recepción conforme, etc.

        Estados típicos OC:
          Aceptada, Recepcionada, Cancelada, Pagada, Enviada al proveedor
        """
        with get_db() as db:
            fecha_desde = _dias_atras(dias)

            q = db.query(
                LicitacionSnapshot.estado,
                func.count(LicitacionSnapshot.id),
                func.coalesce(func.sum(LicitacionSnapshot.monto_clp), 0),
            ).filter(
                LicitacionSnapshot.fecha_publicacion >= fecha_desde,
                LicitacionSnapshot.tipo == tipo,
                LicitacionSnapshot.estado.isnot(None),
            ).group_by(
                LicitacionSnapshot.estado
            ).order_by(func.count(LicitacionSnapshot.id).desc())

            rows = q.all()

            total = sum(r[1] for r in rows) or 1
            estados = [
                {
                    "estado": r[0],
                    "conteo": int(r[1]),
                    "pct": round(int(r[1]) / total * 100, 1),
                    "monto_total": int(r[2]),
                    "monto_total_fmt": _monto_fmt(int(r[2])),
                    "es_problematico": self._es_estado_problematico(r[0]),
                }
                for r in rows
            ]

            # Score de salud: % de estados problemáticos sobre total
            problematicos = sum(e["conteo"] for e in estados if e["es_problematico"])
            score_salud = max(0, round(100 - (problematicos / total * 100), 1))

            return {
                "tipo": tipo,
                "total": total,
                "distribucion": estados,
                "score_salud_pct": score_salud,   # 100 = todo OK, 0 = todo problemático
                "recomendacion": self._recomendacion_salud(score_salud),
            }

    @staticmethod
    def _es_estado_problematico(estado: Optional[str]) -> bool:
        if not estado:
            return False
        keywords = ("cancelad", "rechazad", "impugnad", "recurso", "reclamo", "suspendid")
        return any(k in estado.lower() for k in keywords)

    @staticmethod
    def _recomendacion_salud(score: float) -> str:
        if score >= 85:
            return "Mercado saludable: baja tasa de cancelaciones y reclamos."
        if score >= 65:
            return "Mercado moderado: algunos contratos con problemas. Revisar organismos específicos."
        return "Alerta: alta tasa de cancelaciones o reclamos. Evaluar riesgo antes de participar."

    # ── Nichos de mercado ─────────────────────────────────────────────────────

    def nichos_mercado(
        self,
        dias: int = 180,
        monto_min: int = 500_000,
        limit: int = 10,
    ) -> List[Dict]:
        """
        Detecta nichos: categorías con alta demanda, montos significativos
        y relativamente pocos organismos distintos comprando
        (= mercado concentrado con potencial de posicionamiento).

        Score de nicho = conteo × monto_promedio / organismos_distintos
        """
        cats = self.top_categorias(limit=50, dias=dias)

        nichos = []
        for cat in cats:
            if cat["monto_total"] < monto_min:
                continue
            org_count = max(cat["organismos_distintos"], 1)
            # Score: alto cuando hay muchas licitaciones, montos grandes y pocos organismos
            score = (cat["conteo"] * cat["monto_promedio"]) / org_count
            nichos.append({**cat, "score_nicho": int(score), "concentracion": org_count})

        nichos.sort(key=lambda x: -x["score_nicho"])
        return nichos[:limit]

    # ── Correlaciones ─────────────────────────────────────────────────────────

    def correlacion_monto_region(self, dias: int = 365) -> List[Dict]:
        """
        Monto promedio por región — útil para identificar qué regiones
        gastan más en compras específicas.
        """
        return self.distribucion_regional(dias=dias)

    def estacionalidad_mensual(self) -> Dict:
        """
        Identifica qué meses concentran más licitaciones históricamente.
        Retorna ranking de meses con mayor actividad.
        """
        with get_db() as db:
            sql = text("""
                SELECT
                    CAST(strftime('%m', fecha_publicacion) AS INTEGER) AS mes_num,
                    COUNT(*)                                             AS conteo,
                    COALESCE(AVG(monto_clp), 0)                         AS monto_promedio
                FROM licitaciones_snapshot
                WHERE fecha_publicacion IS NOT NULL
                GROUP BY mes_num
                ORDER BY mes_num
            """)
            rows = _safe_rows(db, sql)

            nombres_mes = [
                "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
            ]

            if not rows:
                return {"meses": [], "mes_pico": None, "mes_minimo": None}

            meses = [
                {
                    "mes_num": r[0],
                    "mes_nombre": nombres_mes[r[0]] if r[0] and r[0] <= 12 else "?",
                    "conteo": int(r[1]),
                    "monto_promedio": int(r[2]),
                    "monto_promedio_fmt": _monto_fmt(int(r[2])),
                }
                for r in rows
            ]

            pico = max(meses, key=lambda x: x["conteo"])
            minimo = min(meses, key=lambda x: x["conteo"])

            return {
                "meses": meses,
                "mes_pico": pico["mes_nombre"],
                "mes_pico_num": pico["mes_num"],
                "mes_minimo": minimo["mes_nombre"],
                "recomendacion": (
                    f"El mayor volumen de licitaciones ocurre en {pico['mes_nombre']}. "
                    f"Prepara tus propuestas con anticipación."
                ),
            }

    # ── Reporte completo ──────────────────────────────────────────────────────

    def reporte_completo(self, dias: int = 90) -> Dict:
        """
        Genera un reporte completo con todos los análisis.
        Útil para exportar o mostrar en dashboard.
        """
        logger.info("Generando reporte completo (últimos %d días)…", dias)
        try:
            return {
                "generado_en": datetime.now(timezone.utc).isoformat(),
                "periodo_dias": dias,
                "resumen": self.resumen_ejecutivo(dias),
                "top_categorias": self.top_categorias(limit=10, dias=dias),
                "top_organismos": self.top_organismos(limit=10, dias=dias),
                "distribucion_regional": self.distribucion_regional(dias=dias),
                "tendencia_mensual": self.tendencia_temporal(meses=max(dias // 30, 3)),
                "nichos": self.nichos_mercado(dias=dias, limit=8),
                "estados_oc": self.analisis_estados(tipo="orden_compra", dias=dias),
                "estacionalidad": self.estacionalidad_mensual(),
            }
        except Exception as e:
            logger.exception("Error generando reporte completo: %s", e)
            return {"error": str(e), "generado_en": datetime.now(timezone.utc).isoformat()}


# ── Instancia singleton ────────────────────────────────────────────────────────
analytics = AnalyticsEngine()
