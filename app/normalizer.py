"""
app/normalizer.py — Transformadores de entidades crudas de la API
a dicts normalizados con campos consistentes.

Esquema de salida normalizado (todos los tipos de entidad):
{
    "tipo":              str,   # licitacion | orden_compra | compra_agil
    "codigo":            str,   # identificador único
    "titulo":            str,
    "descripcion":       str,
    "estado":            str,
    "region":            int | None,    # código numérico de región
    "nombre_region":     str | None,
    "organismo":         str | None,
    "codigo_organismo":  str | None,
    "fecha_publicacion": str | None,    # ISO 8601 YYYY-MM-DD
    "fecha_cierre":      str | None,
    "monto_clp":         int | None,
    "moneda":            str,           # "CLP" siempre (ya convertido)
    "codigo_producto":   list[str],     # lista de códigos de categoría
    "link_detalle":      str,
    "_raw":              dict           # datos originales (para debug)
}
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# URL de detalle en el portal Mercado Público
_LIC_DETAIL_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
    "DetailsAcquisition.aspx?qs={codigo}"
)
_OC_DETAIL_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/PO/"
    "DetailsPurchaseOrder.aspx?codigoOC={codigo}"
)
_OPP_DETAIL_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
    "DetailsAcquisition.aspx?qs={codigo}"
)

# Tasas FX configurables (fallback si no se pasa config externa)
_FX_RATES: Dict[str, float] = {
    "CLP": 1.0,
    "USD": 900.0,   # actualizar mediante env var o tabla de tasas
    "EUR": 980.0,
    "UF":  37_500.0,
    "UTM": 65_000.0,
}


def _to_clp(amount: Optional[float], currency: str) -> Optional[int]:
    """Convierte un monto a CLP usando la tabla de tasas configurables."""
    if amount is None:
        return None
    rate = _FX_RATES.get(currency.upper(), 1.0)
    return int(amount * rate)


def _parse_date(raw: Optional[str]) -> Optional[str]:
    """
    Intenta parsear la fecha desde distintos formatos que devuelve la API
    y retorna ISO 8601 YYYY-MM-DD o None.
    Formatos observados: "2024-01-15T00:00:00", "/Date(1705276800000)/", "15-01-2024"
    """
    if not raw:
        return None

    # Formato .NET JSON: /Date(timestamp)/
    ms_match = re.match(r"/Date\((-?\d+)\)/", raw)
    if ms_match:
        ts_ms = int(ms_match.group(1))
        try:
            return datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return None

    # Formatos ISO / similares — probar el string completo (no raw[:len(fmt)])
    clean = raw.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        for fragment in (clean[:19], clean[:16], clean[:10]):
            try:
                return datetime.strptime(fragment, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

    logger.debug("No se pudo parsear fecha: %s", raw)
    return None


def _extract_region(comprador: Dict) -> tuple[Optional[int], Optional[str]]:
    """Extrae código y nombre de región desde el dict Comprador."""
    codigo = comprador.get("CodigoRegion") or comprador.get("Region")
    nombre = comprador.get("NombreRegion")
    try:
        codigo = int(codigo) if codigo is not None else None
    except (ValueError, TypeError):
        codigo = None
    return codigo, nombre


def _extract_productos(items_raw: Any) -> List[str]:
    """Extrae lista de CodigoProducto desde el campo Items de la API."""
    codigos = []
    if not items_raw:
        return codigos

    listado = []
    if isinstance(items_raw, dict):
        listado = items_raw.get("Listado") or []
    elif isinstance(items_raw, list):
        listado = items_raw

    for item in listado:
        if isinstance(item, dict):
            c = item.get("CodigoProducto") or item.get("CodigoCategoria")
            if c:
                codigos.append(str(c))

    return codigos


# ── Normalizadores por tipo ───────────────────────────────────────────────────

def normalizar_licitacion(raw: Dict) -> Dict:
    """
    Normaliza una licitación devuelta por la API v1.

    La API de listado (endpoint con ?fecha=...) devuelve SOLO:
      CodigoExterno, Nombre, CodigoEstado, FechaCierre
    La API de detalle (endpoint con ?codigo=...) devuelve el objeto completo
      con Comprador, MontoEstimado, FechaPublicacion, Items, etc.
    Este normalizador maneja ambos casos.
    """
    comprador = raw.get("Comprador") or {}
    region, nombre_region = _extract_region(comprador)

    # Monto — solo disponible en respuesta de detalle
    monto_raw = raw.get("MontoEstimado")
    moneda_raw = raw.get("Moneda") or "CLP"
    try:
        monto_raw = float(monto_raw) if monto_raw is not None else None
    except (ValueError, TypeError):
        monto_raw = None
    monto_clp = _to_clp(monto_raw, moneda_raw)

    codigo = raw.get("CodigoExterno") or raw.get("Codigo") or ""

    # Fecha publicación — solo en detalle; en listado no viene
    fecha_pub = _parse_date(
        raw.get("FechaPublicacion") or raw.get("FechaCreacion")
    )
    # Fecha cierre — disponible en listado y detalle
    fecha_cierre = _parse_date(raw.get("FechaCierre"))

    return {
        "tipo": "licitacion",
        "codigo": codigo,
        "titulo": raw.get("Nombre") or "",
        "descripcion": raw.get("Descripcion") or "",
        "estado": _resolve_estado(raw),
        "region": region,
        "nombre_region": nombre_region,
        "organismo": comprador.get("NombreOrganismo"),
        "codigo_organismo": comprador.get("CodigoOrganismo"),
        "fecha_publicacion": fecha_pub,
        "fecha_cierre": fecha_cierre,
        "monto_clp": monto_clp,
        "moneda": "CLP",
        "codigo_producto": _extract_productos(raw.get("Items")),
        "link_detalle": _LIC_DETAIL_URL.format(codigo=codigo),
        # Indicador para saber si los datos son del listado (mínimos) o del detalle (completos)
        "_enriquecido": bool(comprador or monto_clp or fecha_pub),
        "_raw": raw,
    }


def normalizar_orden_compra(raw: Dict) -> Dict:
    """Normaliza una orden de compra devuelta por la API."""
    comprador = raw.get("Comprador") or {}
    region, nombre_region = _extract_region(comprador)

    monto_raw = raw.get("MontoTotal") or raw.get("Monto")
    moneda_raw = raw.get("Moneda") or "CLP"
    try:
        monto_raw = float(monto_raw) if monto_raw is not None else None
    except (ValueError, TypeError):
        monto_raw = None
    monto_clp = _to_clp(monto_raw, moneda_raw)

    codigo = raw.get("Numero") or raw.get("CodigoExterno") or raw.get("Codigo") or ""

    return {
        "tipo": "orden_compra",
        "codigo": codigo,
        "titulo": raw.get("Nombre") or raw.get("Descripcion") or "",
        "descripcion": raw.get("Descripcion") or "",
        "estado": _resolve_estado(raw, es_oc=True),
        "region": region,
        "nombre_region": nombre_region,
        "organismo": comprador.get("NombreOrganismo"),
        "codigo_organismo": comprador.get("CodigoOrganismo"),
        "fecha_publicacion": _parse_date(
            raw.get("FechaEnvio") or raw.get("FechaCreacion")
        ),
        "fecha_cierre": None,
        "monto_clp": monto_clp,
        "moneda": "CLP",
        "codigo_producto": _extract_productos(raw.get("Items")),
        "link_detalle": _OC_DETAIL_URL.format(codigo=codigo),
        "_raw": raw,
    }


def normalizar_oportunidad(raw: Dict) -> Dict:
    """Normaliza una oportunidad de compra ágil."""
    # Las oportunidades comparten estructura con licitaciones (tipo CO)
    normalizado = normalizar_licitacion(raw)
    normalizado["tipo"] = "compra_agil"
    normalizado["link_detalle"] = _OPP_DETAIL_URL.format(codigo=normalizado["codigo"])
    return normalizado


# ── Mapa CodigoEstado → texto legible ───────────────────────────────────────
# La API de listado devuelve solo CodigoEstado (int/str) sin el campo Estado
# CodigoEstado para LICITACIONES
_ESTADO_LIC_MAP = {
    "1": "Borrador",
    "2": "Publicada",
    "3": "Cerrada",
    "4": "Desierta",
    "5": "Adjudicada",
    "6": "Revocada",
    "7": "Suspendida",
    "8": "Publicada",
    "9": "Publicada",
    "16": "Desierta (Admin)",
    "17": "Revocada (Admin)",
    "18": "Suspendida (Admin)",
}

# CodigoEstado para ÓRDENES DE COMPRA (códigos distintos a licitaciones)
_ESTADO_OC_MAP = {
    "1":  "Enviada",
    "2":  "Aceptada",
    "3":  "Rechazada",
    "4":  "Cancelada",
    "5":  "Enviada",
    "6":  "Aceptada",
    "7":  "Parcialmente Recibida",
    "8":  "Recibida",
    "9":  "Cerrada",
    "10": "Cancelada",
    "11": "Pendiente",
    "12": "Procesando",
    "13": "Enviada al Proveedor",
    "14": "Entregada",
    "15": "Pagada",
}

# Unificado: si no está en ninguno, retorna el código para investigar
_ESTADO_MAP = {**_ESTADO_LIC_MAP}

def _resolve_estado(raw: Dict, es_oc: bool = False) -> str:
    """
    Resuelve el estado desde Estado (texto) o CodigoEstado (int/str).
    La API de listado solo devuelve CodigoEstado; la de detalle devuelve Estado.
    Usa el mapa correcto según si es OC o licitación.
    """
    estado = raw.get("Estado") or ""
    if estado:
        return estado
    codigo = str(raw.get("CodigoEstado") or "").strip()
    if not codigo:
        return ""
    mapa = _ESTADO_OC_MAP if es_oc else _ESTADO_LIC_MAP
    return mapa.get(codigo, _ESTADO_MAP.get(codigo, f"Estado {codigo}"))


# ── Dispatcher ────────────────────────────────────────────────────────────────

def normalizar_entidad(raw: Dict, tipo: str) -> Optional[Dict]:
    """
    Punto de entrada único.
    tipo: 'licitacion' | 'orden_compra' | 'compra_agil'
    Retorna None si el raw está vacío o el tipo no es reconocido.
    """
    if not raw:
        return None

    # Detectar tipo desde _api_tipo si viene incrustado
    tipo_real = raw.get("_api_tipo") or tipo

    try:
        if tipo_real in ("licitacion", "L1", "LE", "LP", "LQ", "LR", "LS"):
            return normalizar_licitacion(raw)
        elif tipo_real == "orden_compra":
            return normalizar_orden_compra(raw)
        elif tipo_real in ("compra_agil", "CO"):
            return normalizar_oportunidad(raw)
        else:
            logger.warning("Tipo de entidad desconocido: %s", tipo_real)
            return normalizar_licitacion(raw)  # Fallback
    except Exception as e:
        logger.exception("Error normalizando entidad (tipo=%s): %s", tipo, e)
        return None


def datos_resumen(entidad: Dict) -> Dict:
    """
    Extrae solo los campos relevantes para guardar en alertas_generadas.datos_resumen.
    """
    return {
        "tipo":              entidad.get("tipo"),
        "codigo":            entidad.get("codigo"),
        "titulo":            entidad.get("titulo"),
        "organismo":         entidad.get("organismo"),
        "monto_clp":         entidad.get("monto_clp"),
        "moneda":            entidad.get("moneda"),
        "estado":            entidad.get("estado"),
        "region":            entidad.get("region"),
        "nombre_region":     entidad.get("nombre_region"),
        "fecha_publicacion": entidad.get("fecha_publicacion"),
        "fecha_cierre":      entidad.get("fecha_cierre"),
        "link_detalle":      entidad.get("link_detalle"),
        "codigo_producto":   entidad.get("codigo_producto", []),
    }
