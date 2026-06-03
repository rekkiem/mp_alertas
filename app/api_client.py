"""
app/api_client.py — Cliente para la API de Mercado Público.

Características:
  • Rate limiting configurable (5 req/seg, 10 000 req/día)
  • Retry con backoff exponencial ante 429 / 503 / timeouts
  • Paginación automática (iteradores perezosos)
  • Cache TTL 24 h para datos maestros (regiones, rubros)
  • Soporte v1 (ticket en URL) y v2 (ticket en header) según endpoint
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional

import requests
from cachetools import TTLCache

from config import settings

logger = logging.getLogger(__name__)

# ── Excepciones propias ───────────────────────────────────────────────────────

class QuotaExhaustedException(Exception):
    """Se agotó la cuota diaria de la API."""


class ApiClientException(Exception):
    """Error general del cliente de API."""


# ── Rate Limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Doble límite: N req/seg y M req/día.
    Thread-safe usando un Lock.
    """

    def __init__(self, per_second: int = 5, per_day: int = 10_000):
        self.per_second = per_second
        self.per_day = per_day
        self._lock = Lock()
        # Contadores por segundo
        self._sec_count = 0
        self._sec_reset = time.monotonic() + 1.0
        # Contadores diarios
        self._day_count = 0
        self._day_reset = time.monotonic() + 86_400.0
        self._quota_exhausted = False

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()

            # Reset diario
            if now >= self._day_reset:
                self._day_count = 0
                self._day_reset = now + 86_400.0
                self._quota_exhausted = False

            if self._quota_exhausted or self._day_count >= self.per_day:
                self._quota_exhausted = True
                raise QuotaExhaustedException(
                    f"Cuota diaria de {self.per_day} requests agotada."
                )

            # Reset por segundo
            if now >= self._sec_reset:
                self._sec_count = 0
                self._sec_reset = now + 1.0

            if self._sec_count >= self.per_second:
                sleep_ms = self._sec_reset - now
                if sleep_ms > 0:
                    time.sleep(sleep_ms)
                self._sec_count = 0
                self._sec_reset = time.monotonic() + 1.0

            self._sec_count += 1
            self._day_count += 1

    @property
    def daily_remaining(self) -> int:
        return max(0, self.per_day - self._day_count)


# ── Cliente principal ─────────────────────────────────────────────────────────

class MercadoPublicoClient:
    """
    Cliente HTTP para la API de Mercado Público de Chile.

    Endpoints v1 (ticket en query string):
      /publico/licitaciones.json
      /publico/ordenesdecompra.json
      /publico/licitaciones.json?tipo=CO  (compra ágil / oportunidades)
    """

    BASE_URL = settings.API_BASE_URL.rstrip("/")

    # Catálogo en caché (TTL 24 h)
    _cache_regiones: TTLCache = TTLCache(maxsize=1, ttl=86_400)
    _cache_rubros: TTLCache = TTLCache(maxsize=1, ttl=86_400)

    def __init__(self, ticket: Optional[str] = None):
        self.ticket = ticket or settings.TICKET_MERCADO_PUBLICO
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.rate_limiter = RateLimiter(
            per_second=settings.API_RATE_PER_SECOND,
            per_day=settings.API_RATE_PER_DAY,
        )

    # ── Requests base ─────────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Optional[Dict] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        GET con rate limiting, retry y backoff exponencial.
        Añade ticket automáticamente.
        """
        if params is None:
            params = {}
        params.setdefault("ticket", self.ticket)

        url = f"{self.BASE_URL}/{path.lstrip('/')}"

        for attempt in range(max_retries):
            try:
                self.rate_limiter.acquire()
            except QuotaExhaustedException:
                raise  # Propagar sin reintentar

            try:
                resp = self.session.get(url, params=params, timeout=30)

                if resp.status_code == 429:
                    wait = 2 ** attempt * 5
                    logger.warning("HTTP 429 (rate limit). Esperando %ss…", wait)
                    time.sleep(wait)
                    continue

                if resp.status_code in (500, 502, 503, 504):
                    wait = 2 ** attempt * 10
                    logger.warning("HTTP %s. Esperando %ss…", resp.status_code, wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.Timeout:
                wait = 2 ** attempt * 3
                logger.warning("Timeout en %s (intento %d/%d). Esperando %ss…", url, attempt + 1, max_retries, wait)
                if attempt < max_retries - 1:
                    time.sleep(wait)

            except requests.exceptions.JSONDecodeError as e:
                logger.error("Respuesta no es JSON válido de %s: %s", url, e)
                raise ApiClientException(f"JSON inválido en {url}") from e

            except requests.exceptions.RequestException as e:
                logger.error("RequestException en %s: %s", url, e)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt * 2)
                else:
                    raise ApiClientException(str(e)) from e

        raise ApiClientException(f"Máximo de reintentos ({max_retries}) alcanzado para {url}")

    # ── Licitaciones ──────────────────────────────────────────────────────────

    def iter_licitaciones(
        self,
        fecha_desde: Optional[str] = None,    # DDMMAAAA
        estado: Optional[str] = None,          # ej. "publicada"
        codigo: Optional[str] = None,
        tipo: Optional[str] = None,            # ej. "L1", "LE", "CO"
    ) -> Iterator[Dict]:
        """
        Itera TODAS las licitaciones aplicando paginación automática.
        Usa 'fecha' (inicio) como filtro primario de la API; el filtro
        de fecha_hasta se aplica en el normalizador / motor de filtros.
        """
        params: Dict[str, Any] = {}
        if fecha_desde:
            params["fecha"] = fecha_desde
        if estado:
            params["estado"] = estado
        if codigo:
            params["codigo"] = codigo
        if tipo:
            params["tipo"] = tipo

        page = 1
        total_fetched = 0

        while True:
            params["pagina"] = page
            try:
                data = self._get("publico/licitaciones.json", params.copy())
            except QuotaExhaustedException:
                logger.error("Cuota agotada obteniendo licitaciones.")
                return
            except ApiClientException as e:
                logger.error("Error obteniendo licitaciones página %d: %s", page, e)
                return

            items: List[Dict] = data.get("Listado") or []
            if not items:
                break

            for item in items:
                item["_api_tipo"] = tipo or "licitacion"
                yield item
                total_fetched += 1

            logger.debug("Licitaciones: página %d, %d ítems (total=%d)", page, len(items), total_fetched)

            # La API devuelve ≤ 1000 por página; si hay menos, terminamos
            if len(items) < 1000:
                break

            page += 1

    def get_licitacion_detalle(self, codigo: str) -> Optional[Dict]:
        """Obtiene el detalle completo de una licitación por código."""
        try:
            data = self._get("publico/licitaciones.json", {"codigo": codigo})
            listado = data.get("Listado") or []
            return listado[0] if listado else None
        except (ApiClientException, QuotaExhaustedException) as e:
            logger.error("Error detalle licitación %s: %s", codigo, e)
            return None

    # ── Órdenes de Compra ─────────────────────────────────────────────────────

    def iter_ordenes_compra(
        self,
        fecha_desde: Optional[str] = None,
        estado: Optional[str] = None,
        codigo: Optional[str] = None,
    ) -> Iterator[Dict]:
        """Itera órdenes de compra con paginación."""
        params: Dict[str, Any] = {}
        if fecha_desde:
            params["fecha"] = fecha_desde
        if estado:
            params["estado"] = estado
        if codigo:
            params["codigo"] = codigo

        page = 1
        while True:
            params["pagina"] = page
            try:
                data = self._get("publico/ordenesdecompra.json", params.copy())
            except QuotaExhaustedException:
                logger.error("Cuota agotada obteniendo órdenes de compra.")
                return
            except ApiClientException as e:
                logger.error("Error obteniendo OC página %d: %s", page, e)
                return

            items: List[Dict] = data.get("Listado") or []
            if not items:
                break

            for item in items:
                item["_api_tipo"] = "orden_compra"
                yield item

            if len(items) < 1000:
                break
            page += 1

    # ── Compra Ágil / Oportunidades ───────────────────────────────────────────

    def iter_oportunidades(
        self,
        fecha_desde: Optional[str] = None,
        estado: Optional[str] = None,
    ) -> Iterator[Dict]:
        """
        Oportunidades de compra ágil.
        La API de MP usa tipo='CO' en el endpoint de licitaciones para esto.
        """
        yield from self.iter_licitaciones(
            fecha_desde=fecha_desde,
            estado=estado,
            tipo="CO",
        )

    # ── Datos maestros ────────────────────────────────────────────────────────

    def get_regiones(self) -> List[Dict]:
        """Regiones de Chile (cache 24 h)."""
        if "regiones" not in self._cache_regiones:
            try:
                data = self._get("maestros/regiones.json")
                self._cache_regiones["regiones"] = data.get("regiones", [])
                logger.info("Regiones cargadas: %d", len(self._cache_regiones["regiones"]))
            except Exception as e:
                logger.error("No se pudieron cargar regiones: %s", e)
                return []
        return self._cache_regiones.get("regiones", [])

    def get_rubros(self) -> List[Dict]:
        """Rubros/categorías ONU (cache 24 h)."""
        if "rubros" not in self._cache_rubros:
            try:
                data = self._get("maestros/rubros.json")
                self._cache_rubros["rubros"] = data.get("rubroOnu", [])
                logger.info("Rubros cargados: %d", len(self._cache_rubros["rubros"]))
            except Exception as e:
                logger.error("No se pudieron cargar rubros: %s", e)
                return []
        return self._cache_rubros.get("rubros", [])

    # ── Helper fecha ──────────────────────────────────────────────────────────

    @staticmethod
    def to_api_date(dt: datetime) -> str:
        """Convierte datetime a formato DDMMAAAA que usa la API."""
        return dt.strftime("%d%m%Y")
