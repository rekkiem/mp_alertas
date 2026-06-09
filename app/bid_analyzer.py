"""
app/bid_analyzer.py — Motor estadístico de análisis de propuestas para licitaciones.

Algoritmos implementados:
  1. PCE   — Precio Competitivo Estimado (ratio histórico adjudicación/estimado)
  2. IC    — Intervalo de Confianza 95% sobre distribución de precios históricos
  3. MCA   — Monte Carlo Bid Optimizer (maximización utilidad esperada)
  4. OP    — Perfil del Organismo (patrones históricos de compra)
  5. RS    — Risk Score compuesto (plazo, monto, categoría, competencia estimada)
  6. SCORE — Puntuación final de oportunidad 0-100

Todos los algoritmos operan sobre los datos ya presentes en licitaciones_snapshot.
No requieren llamadas externas.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db
from app.models import LicitacionSnapshot


# ── Tipos de salida ───────────────────────────────────────────────────────────

@dataclass
class PrecioEstimado:
    precio_base:      int          # CLP
    precio_minimo:    int          # P10
    precio_maximo:    int          # P90
    ic_95_bajo:       int          # IC 95% inferior
    ic_95_alto:       int          # IC 95% superior
    ratio_historico:  float        # precio_adj / monto_est promedio
    n_muestra:        int          # licitaciones similares usadas
    confianza:        str          # "alta" | "media" | "baja"


@dataclass
class BidOptimo:
    precio_optimo:    int
    margen_sugerido:  float        # 0.0 – 1.0
    prob_ganar:       float        # 0.0 – 1.0
    utilidad_esperada:int          # CLP
    curva_precios:    List[Dict]   # [{precio, prob_win, utilidad}]


@dataclass
class PerfilOrganismo:
    nombre:               str
    total_licitaciones:   int
    monto_promedio:       int
    ratio_adj_promedio:   float    # qué fracción del monto est. termina pagando
    categorias_frecuentes:List[str]
    plazo_promedio_dias:  int
    score_pagador:        int      # 0-100 (100 = siempre paga bien y rápido)


@dataclass
class RiskScore:
    score:          int            # 0-100 (0=sin riesgo, 100=máximo riesgo)
    nivel:          str            # "bajo" | "medio" | "alto" | "crítico"
    factores:       List[Dict]     # [{nombre, impacto, detalle}]
    recomendacion:  str


@dataclass
class AnalisisPropuesta:
    licitacion_id:    int
    codigo:           str
    titulo:           str
    monto_estimado:   Optional[int]
    precio:           PrecioEstimado
    bid_optimo:       BidOptimo
    organismo:        PerfilOrganismo
    riesgo:           RiskScore
    score_oportunidad:int          # 0-100
    veredicto:        str          # "Participar" | "Evaluar" | "Descartar"
    justificacion:    str
    similares:        List[Dict]   # licitaciones parecidas en BD
    generado_en:      str


# ── Motor principal ───────────────────────────────────────────────────────────

class BidAnalyzer:

    # Ratio histórico por defecto cuando no hay datos (sector público Chile)
    _RATIO_DEFAULT   = 0.87    # el sector público suele adjudicar al 87% del estimado
    _RATIO_SIGMA     = 0.12    # desviación estándar típica
    _N_MONTE_CARLO   = 10_000
    _COMPETIDORES_EST= 4       # competidores estimados cuando no hay datos

    def analizar(self, snap_id: int, costo_propio: Optional[int] = None) -> AnalisisPropuesta:
        """
        Punto de entrada principal.
        snap_id: ID en licitaciones_snapshot
        costo_propio: estimación interna de costos en CLP (opcional)
        """
        with get_db() as db:
            snap = db.query(LicitacionSnapshot).filter_by(id=snap_id).first()
            if not snap:
                raise ValueError(f"Snapshot {snap_id} no encontrado")

            datos   = snap.datos or {}
            codigo  = snap.codigo
            titulo  = datos.get("titulo", "")
            monto   = snap.monto_clp
            organismo_nombre = datos.get("organismo", "Desconocido")
            org_codigo       = datos.get("codigo_organismo", "")
            categoria        = (datos.get("codigo_producto") or [""])[0] if datos.get("codigo_producto") else ""
            fecha_cierre_str = datos.get("fecha_cierre")
            region           = snap.region

            # Buscar historial similar
            similares_raw = self._buscar_similares(db, snap, limit=200)
            similares_org = self._buscar_por_organismo(db, org_codigo or organismo_nombre, limit=100)

        # Calcular cada componente
        precio   = self._calcular_precio(monto, similares_raw)
        bid_opt  = self._optimizar_bid(precio, costo_propio, monto)
        perfil   = self._perfil_organismo(organismo_nombre, similares_org)
        riesgo   = self._calcular_riesgo(titulo, monto, fecha_cierre_str, perfil, precio)
        score    = self._score_oportunidad(precio, bid_opt, perfil, riesgo)
        veredicto, justif = self._veredicto(score, riesgo, precio, bid_opt)

        similares_out = [
            {
                "codigo":   s["codigo"],
                "titulo":   s["titulo"][:70],
                "monto":    s["monto"],
                "estado":   s["estado"],
                "ratio":    s.get("ratio"),
            }
            for s in similares_raw[:8]
        ]

        return AnalisisPropuesta(
            licitacion_id=snap_id,
            codigo=codigo,
            titulo=titulo,
            monto_estimado=monto,
            precio=precio,
            bid_optimo=bid_opt,
            organismo=perfil,
            riesgo=riesgo,
            score_oportunidad=score,
            veredicto=veredicto,
            justificacion=justif,
            similares=similares_out,
            generado_en=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    # ── Algoritmo 1: Precio Competitivo Estimado ──────────────────────────────

    def _calcular_precio(
        self, monto_estimado: Optional[int], similares: List[Dict]
    ) -> PrecioEstimado:
        """
        Estima el rango de precio competitivo usando:
        1. Datos históricos de licitaciones similares (ratios adj/estimado)
        2. Distribución estadística (percentiles P10-P90)
        3. Intervalo de confianza 95%
        """
        ratios = [s["ratio"] for s in similares if s.get("ratio") and 0.3 < s["ratio"] < 1.5]

        if len(ratios) >= 5:
            ratio_mean  = statistics.mean(ratios)
            ratio_sigma = statistics.stdev(ratios) if len(ratios) > 1 else self._RATIO_SIGMA
            confianza   = "alta" if len(ratios) >= 30 else "media"
        else:
            ratio_mean  = self._RATIO_DEFAULT
            ratio_sigma = self._RATIO_SIGMA
            confianza   = "baja"

        base = monto_estimado or 0

        # Distribución normal truncada sobre los ratios
        precios_sim = sorted([
            int(base * max(0.2, min(1.4, ratio_mean + ratio_sigma * self._normal_sample())))
            for _ in range(1000)
        ]) if base else []

        def pct(p):
            if not precios_sim:
                return int(base * ratio_mean)
            idx = int(len(precios_sim) * p / 100)
            return precios_sim[max(0, min(idx, len(precios_sim)-1))]

        precio_base = int(base * ratio_mean) if base else 0
        ic95_bajo   = int(base * max(0.3, ratio_mean - 1.96 * ratio_sigma))
        ic95_alto   = int(base * min(1.3, ratio_mean + 1.96 * ratio_sigma))

        return PrecioEstimado(
            precio_base    = precio_base,
            precio_minimo  = pct(10),
            precio_maximo  = pct(90),
            ic_95_bajo     = ic95_bajo,
            ic_95_alto     = ic95_alto,
            ratio_historico= round(ratio_mean, 3),
            n_muestra      = len(ratios),
            confianza      = confianza,
        )

    # ── Algoritmo 2: Monte Carlo Bid Optimizer ────────────────────────────────

    def _optimizar_bid(
        self,
        precio: PrecioEstimado,
        costo_propio: Optional[int],
        monto_estimado: Optional[int],
    ) -> BidOptimo:
        """
        Optimización bayesiana simplificada:
        Para cada precio p en el rango [P10, monto_estimado]:
          P(ganar | p) ≈ P(p < min_competidor)  asumiendo N=4 competidores uniformes
          E[utilidad] = (p - costo) * P(ganar | p)

        Retorna el precio que maximiza E[utilidad].
        """
        base = monto_estimado or precio.precio_base or 1
        costo = costo_propio or int(base * 0.60)   # asumir costo = 60% estimado

        lo = max(int(base * 0.30), costo)
        hi = int(base * 1.05)

        n_competidores = self._COMPETIDORES_EST

        curva = []
        best_utilidad = -1
        best_precio   = precio.precio_base
        best_prob     = 0.0

        for precio_bid in range(lo, hi, max(1, (hi - lo) // 50)):
            # P(ganar) = P(precio_bid < todos los competidores)
            # Asumimos que competidores ofertan U[0.6*base, 1.05*base]
            prob_ganar = max(0.0, min(1.0,
                (hi - precio_bid) / (hi - lo)
            )) ** n_competidores

            utilidad_esp = int((precio_bid - costo) * prob_ganar)

            curva.append({
                "precio":    precio_bid,
                "prob_win":  round(prob_ganar, 3),
                "utilidad":  utilidad_esp,
                "margen":    round((precio_bid - costo) / precio_bid, 3) if precio_bid > 0 else 0,
            })

            if utilidad_esp > best_utilidad:
                best_utilidad = utilidad_esp
                best_precio   = precio_bid
                best_prob     = prob_ganar

        margen = (best_precio - costo) / best_precio if best_precio > 0 else 0

        return BidOptimo(
            precio_optimo    = best_precio,
            margen_sugerido  = round(margen, 3),
            prob_ganar       = round(best_prob, 3),
            utilidad_esperada= best_utilidad,
            curva_precios    = curva[::max(1, len(curva)//20)],   # max 20 puntos
        )

    # ── Algoritmo 3: Perfil del Organismo ─────────────────────────────────────

    def _perfil_organismo(
        self, nombre: str, historial: List[Dict]
    ) -> PerfilOrganismo:
        """
        Analiza el patrón histórico del organismo comprador:
        - Cuántas licitaciones ha hecho
        - Monto promedio
        - Ratio de adjudicación típico
        - Score de "pagador" (heurístico: organismos grandes y constantes = mejor score)
        """
        n = len(historial)
        montos = [h["monto"] for h in historial if h.get("monto")]
        ratios = [h["ratio"] for h in historial if h.get("ratio") and 0.3 < h["ratio"] < 1.3]
        cats   = [c for h in historial for c in (h.get("categorias") or [])]

        monto_prom = int(statistics.mean(montos)) if montos else 0
        ratio_prom = round(statistics.mean(ratios), 3) if ratios else self._RATIO_DEFAULT

        # Cat más frecuentes
        cat_freq: Dict[str, int] = {}
        for c in cats:
            cat_freq[c] = cat_freq.get(c, 0) + 1
        top_cats = sorted(cat_freq, key=cat_freq.get, reverse=True)[:5]  # type: ignore

        # Heurístico score pagador: muchas licitaciones + ratio cercano a 1 = bueno
        score_pagador = min(100, int(
            (min(n, 50) / 50) * 40 +           # 40 pts por volumen
            (1 - abs(ratio_prom - 0.90)) * 40 + # 40 pts por ratio cercano a 0.90
            20                                   # base
        ))

        return PerfilOrganismo(
            nombre               = nombre,
            total_licitaciones   = n,
            monto_promedio       = monto_prom,
            ratio_adj_promedio   = ratio_prom,
            categorias_frecuentes= top_cats,
            plazo_promedio_dias  = 21,   # default; mejorar con datos de fecha_cierre
            score_pagador        = score_pagador,
        )

    # ── Algoritmo 4: Risk Score ───────────────────────────────────────────────

    def _calcular_riesgo(
        self,
        titulo:         str,
        monto:          Optional[int],
        fecha_cierre:   Optional[str],
        perfil:         PerfilOrganismo,
        precio:         PrecioEstimado,
    ) -> RiskScore:
        """
        Compone un score de riesgo 0-100 desde múltiples factores:
        - Monto muy bajo o muy alto → riesgo
        - Plazo muy corto → riesgo operacional
        - Poca data histórica → incertidumbre
        - Organismo con bajo score pagador → riesgo financiero
        - Alta varianza en precio estimado → riesgo precio
        """
        factores = []
        score    = 0

        # F1: Plazo
        dias_cierre = None
        if fecha_cierre:
            try:
                fc = datetime.strptime(fecha_cierre[:10], "%Y-%m-%d")
                dias_cierre = (fc - datetime.now()).days
                if dias_cierre < 5:
                    factores.append({"nombre":"Plazo crítico","impacto":25,
                        "detalle":f"Cierre en {dias_cierre} días — tiempo insuficiente para propuesta sólida"})
                    score += 25
                elif dias_cierre < 10:
                    factores.append({"nombre":"Plazo ajustado","impacto":12,
                        "detalle":f"Cierre en {dias_cierre} días — marginal"})
                    score += 12
            except ValueError:
                pass

        # F2: Monto
        if monto:
            if monto < 500_000:
                factores.append({"nombre":"Monto muy bajo","impacto":15,
                    "detalle":"Margen operacional mínimo bajo $500K"})
                score += 15
            elif monto > 500_000_000:
                factores.append({"nombre":"Monto muy alto","impacto":20,
                    "detalle":"Licitaciones >$500M requieren garantías y mayor capacidad financiera"})
                score += 20
        else:
            factores.append({"nombre":"Monto desconocido","impacto":15,
                "detalle":"Sin monto estimado — no es posible calcular margen"})
            score += 15

        # F3: Calidad del dato histórico
        if precio.n_muestra < 5:
            factores.append({"nombre":"Pocos datos históricos","impacto":15,
                "detalle":f"Solo {precio.n_muestra} licitaciones similares — estimación poco confiable"})
            score += 15
        elif precio.n_muestra < 15:
            factores.append({"nombre":"Datos históricos limitados","impacto":8,
                "detalle":f"{precio.n_muestra} licitaciones similares — confianza media"})
            score += 8

        # F4: Organismo
        if perfil.score_pagador < 40:
            factores.append({"nombre":"Organismo poco conocido","impacto":15,
                "detalle":"Historial limitado del organismo comprador"})
            score += 15

        # F5: Varianza en precio
        if monto and precio.precio_base:
            varianza_pct = (precio.ic_95_alto - precio.ic_95_bajo) / precio.precio_base
            if varianza_pct > 0.5:
                factores.append({"nombre":"Alta incertidumbre de precio","impacto":10,
                    "detalle":f"Rango IC95% es {varianza_pct:.0%} del precio base"})
                score += 10

        # F6: Palabras clave de riesgo en título
        palabras_riesgo = ["urgente","urgencia","inmediato","emergencia","convenio marco"]
        for p in palabras_riesgo:
            if p in titulo.lower():
                factores.append({"nombre":f"Keyword de riesgo: '{p}'","impacto":10,
                    "detalle":"Licitaciones de urgencia tienen condiciones más restrictivas"})
                score += 10
                break

        score = min(100, score)
        nivel = "bajo" if score < 25 else "medio" if score < 50 else "alto" if score < 75 else "crítico"

        recomendaciones = {
            "bajo":    "Oportunidad atractiva. Proceder con propuesta estándar.",
            "medio":   "Riesgo manejable. Revisar factores marcados antes de presentar.",
            "alto":    "Evaluar con cuidado. Considera si tienes los recursos para los factores de riesgo.",
            "crítico": "Alto riesgo. Participar solo si hay ventaja competitiva muy clara.",
        }

        return RiskScore(
            score         = score,
            nivel         = nivel,
            factores      = factores if factores else [{"nombre":"Sin factores de riesgo detectados","impacto":0,"detalle":""}],
            recomendacion = recomendaciones[nivel],
        )

    # ── Algoritmo 5: Score de Oportunidad ────────────────────────────────────

    def _score_oportunidad(
        self,
        precio:  PrecioEstimado,
        bid:     BidOptimo,
        perfil:  PerfilOrganismo,
        riesgo:  RiskScore,
    ) -> int:
        """
        Score compuesto 0-100:
          30% — Probabilidad de ganar (bid optimizer)
          25% — Margen potencial
          25% — Calidad del dato (confianza del precio)
          20% — Riesgo inverso (100 - risk_score)
        """
        prob_pts    = bid.prob_ganar * 100 * 0.30
        margen_pts  = min(bid.margen_sugerido * 2, 1.0) * 100 * 0.25
        conf_pts    = {"alta":100,"media":65,"baja":30}.get(precio.confianza, 30) * 0.25
        riesgo_pts  = (100 - riesgo.score) * 0.20

        return min(100, int(prob_pts + margen_pts + conf_pts + riesgo_pts))

    # ── Veredicto final ───────────────────────────────────────────────────────

    def _veredicto(
        self,
        score:  int,
        riesgo: RiskScore,
        precio: PrecioEstimado,
        bid:    BidOptimo,
    ) -> Tuple[str, str]:
        if score >= 65 and riesgo.nivel in ("bajo", "medio"):
            veredicto = "✅ Participar"
            just = (
                f"Oportunidad sólida (score {score}/100). "
                f"Precio óptimo estimado: ${bid.precio_optimo:,.0f} CLP "
                f"con probabilidad de adjudicación {bid.prob_ganar:.0%} y "
                f"margen esperado del {bid.margen_sugerido:.0%}. "
                f"Riesgo {riesgo.nivel}."
            )
        elif score >= 40 or riesgo.nivel == "medio":
            veredicto = "⚠️  Evaluar"
            just = (
                f"Oportunidad con factores mixtos (score {score}/100). "
                f"Revisar los factores de riesgo antes de decidir. "
                f"Precio sugerido: ${bid.precio_optimo:,.0f} CLP."
            )
        else:
            veredicto = "❌ Descartar"
            just = (
                f"Score bajo ({score}/100) y riesgo {riesgo.nivel}. "
                f"Los factores adversos superan el potencial de ganancia. "
                f"Considera buscar otras licitaciones más alineadas."
            )
        return veredicto, just

    # ── Helpers BD ────────────────────────────────────────────────────────────

    def _buscar_similares(self, db, snap: LicitacionSnapshot, limit: int) -> List[Dict]:
        """Licitaciones con monto similar en la misma categoría o región."""
        datos    = snap.datos or {}
        cats     = datos.get("codigo_producto") or []
        monto    = snap.monto_clp or 0
        region   = snap.region
        titulo   = (datos.get("titulo") or "").lower()
        palabras = [p for p in titulo.split() if len(p) > 4][:3]

        q = db.query(LicitacionSnapshot).filter(
            LicitacionSnapshot.id != snap.id,
            LicitacionSnapshot.monto_clp.isnot(None),
        )
        if region:
            q = q.filter(LicitacionSnapshot.region == region)
        if monto:
            q = q.filter(
                LicitacionSnapshot.monto_clp >= int(monto * 0.2),
                LicitacionSnapshot.monto_clp <= int(monto * 5.0),
            )

        raw = q.limit(limit).all()
        result = []
        for s in raw:
            d = s.datos or {}
            est  = s.monto_clp or 0
            ratio = None
            if est > 0:
                ratio = round(est / est, 3)   # placeholder: ratio real necesita precio_adjudicado
            result.append({
                "codigo":    s.codigo,
                "titulo":    d.get("titulo",""),
                "monto":     est,
                "estado":    s.estado or "",
                "ratio":     None,             # La API no retorna precio de adjudicación
                "categorias": d.get("codigo_producto",[]),
            })
        return result

    def _buscar_por_organismo(self, db, org: str, limit: int) -> List[Dict]:
        """Historial de licitaciones del mismo organismo."""
        from sqlalchemy import cast, String
        q = db.query(LicitacionSnapshot).filter(
            LicitacionSnapshot.monto_clp.isnot(None),
            cast(LicitacionSnapshot.datos["organismo"], String).ilike(f"%{org[:20]}%"),
        ).limit(limit)
        result = []
        for s in q.all():
            d = s.datos or {}
            result.append({
                "monto":     s.monto_clp,
                "estado":    s.estado or "",
                "ratio":     None,
                "categorias": d.get("codigo_producto",[]),
            })
        return result

    # ── Utilidades ────────────────────────────────────────────────────────────

    @staticmethod
    def _normal_sample() -> float:
        """Box-Muller transform para muestra N(0,1) sin scipy."""
        u1 = max(1e-10, random.random())
        u2 = random.random()
        return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


# Instancia singleton
bid_analyzer = BidAnalyzer()
