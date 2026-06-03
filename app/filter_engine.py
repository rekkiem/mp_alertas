"""
app/filter_engine.py — Motor de evaluación de reglas.

Operadores soportados en el JSON de filtros:
  region            int       Igualdad de región (código numérico)
  region_in         list[int] Región en lista
  monto_min         int       Monto CLP >= valor
  monto_max         int       Monto CLP <= valor
  titulo_contains   str       Título contiene (case-insensitive)
  titulo_not_contains str     Título NO contiene (exclusión)
  descripcion_contains str    Descripción contiene
  codigo_producto_in list[str] Algún código de producto en lista
  organismo_contains str      Nombre organismo contiene
  estado            str       Igualdad de estado (case-insensitive)
  estado_in         list[str] Estado en lista

Todos los filtros deben cumplirse (AND lógico).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)


class FiltroInvalidoError(Exception):
    """El JSON de filtros tiene una clave o tipo inválido."""


# ── Evaluadores atómicos ───────────────────────────────────────────────────────

def _check_region(entidad: Dict, valor: int) -> bool:
    return entidad.get("region") == int(valor)


def _check_region_in(entidad: Dict, lista: list) -> bool:
    r = entidad.get("region")
    return r is not None and r in [int(x) for x in lista]


def _check_monto_min(entidad: Dict, valor: int) -> bool:
    m = entidad.get("monto_clp")
    return m is not None and m >= int(valor)


def _check_monto_max(entidad: Dict, valor: int) -> bool:
    m = entidad.get("monto_clp")
    return m is not None and m <= int(valor)


def _check_titulo_contains(entidad: Dict, texto: str) -> bool:
    t = entidad.get("titulo") or ""
    return texto.lower() in t.lower()


def _check_titulo_not_contains(entidad: Dict, texto: str) -> bool:
    t = entidad.get("titulo") or ""
    return texto.lower() not in t.lower()


def _check_descripcion_contains(entidad: Dict, texto: str) -> bool:
    d = entidad.get("descripcion") or ""
    return texto.lower() in d.lower()


def _check_codigo_producto_in(entidad: Dict, codigos: list) -> bool:
    entidad_codigos = [str(c) for c in (entidad.get("codigo_producto") or [])]
    filtro_codigos = [str(c) for c in codigos]
    return any(c in entidad_codigos for c in filtro_codigos)


def _check_organismo_contains(entidad: Dict, texto: str) -> bool:
    o = entidad.get("organismo") or ""
    return texto.lower() in o.lower()


def _check_estado(entidad: Dict, valor: str) -> bool:
    e = entidad.get("estado") or ""
    return e.lower() == valor.lower()


def _check_estado_in(entidad: Dict, lista: list) -> bool:
    e = (entidad.get("estado") or "").lower()
    return e in [s.lower() for s in lista]


# ── Mapa de operadores ─────────────────────────────────────────────────────────

_OPERADORES = {
    "region":               _check_region,
    "region_in":            _check_region_in,
    "monto_min":            _check_monto_min,
    "monto_max":            _check_monto_max,
    "titulo_contains":      _check_titulo_contains,
    "titulo_not_contains":  _check_titulo_not_contains,
    "descripcion_contains": _check_descripcion_contains,
    "codigo_producto_in":   _check_codigo_producto_in,
    "organismo_contains":   _check_organismo_contains,
    "estado":               _check_estado,
    "estado_in":            _check_estado_in,
}

FILTROS_VALIDOS = set(_OPERADORES.keys())
OPERADORES_VALIDOS = FILTROS_VALIDOS   # alias para compatibilidad


# ── Función principal ──────────────────────────────────────────────────────────

def evaluar_regla(regla: Any, entidad: Dict) -> bool:
    """
    Evalúa si una entidad normalizada cumple TODOS los criterios de una regla.

    Args:
        regla: instancia de ReglaUsuario (ORM) o dict con clave 'filtros'.
        entidad: dict normalizado por app.normalizer.normalizar_entidad().

    Returns:
        True si la entidad cumple todos los filtros; False si alguno falla.

    Raises:
        FiltroInvalidoError: si los filtros contienen claves desconocidas.
    """
    # Obtener filtros del objeto o dict
    if hasattr(regla, "filtros"):
        filtros: Dict = regla.filtros or {}
    elif isinstance(regla, dict):
        filtros = regla.get("filtros") or {}
    else:
        logger.error("evaluar_regla: tipo inesperado de regla: %s", type(regla))
        return False

    if not filtros:
        # Sin filtros → coincide con todo (útil para "alertarme de cualquier cosa")
        return True

    for clave, valor in filtros.items():
        # Ignorar filtros vacíos: None, "", lista vacía, dict vacío
        if valor is None or valor == "" or valor == [] or valor == {}:
            continue
        evaluador = _OPERADORES.get(clave)
        if evaluador is None:
            raise FiltroInvalidoError(
                f"Filtro desconocido: '{clave}'. Válidos: {sorted(FILTROS_VALIDOS)}"
            )
        try:
            if not evaluador(entidad, valor):
                logger.debug(
                    "Regla '%s' falló filtro '%s'=%r para entidad '%s'",
                    getattr(regla, "nombre_regla", "?"),
                    clave,
                    valor,
                    entidad.get("codigo", "?"),
                )
                return False
        except (TypeError, ValueError) as e:
            logger.warning("Error evaluando filtro '%s'=%r: %s", clave, valor, e)
            return False

    return True


def validar_filtros(filtros: Dict) -> list[str]:
    """
    Valida un dict de filtros y retorna lista de errores encontrados.
    Lista vacía = sin errores.
    """
    errores = []
    for clave, valor in filtros.items():
        if clave not in FILTROS_VALIDOS:
            errores.append(f"Filtro desconocido: '{clave}'")
            continue

        # Validaciones de tipo por operador
        if clave in ("region", "monto_min", "monto_max"):
            try:
                int(valor)
            except (TypeError, ValueError):
                errores.append(f"'{clave}' debe ser un número entero, recibido: {valor!r}")

        elif clave in ("region_in", "codigo_producto_in", "estado_in"):
            if not isinstance(valor, (list, tuple)):
                errores.append(f"'{clave}' debe ser una lista, recibido: {type(valor).__name__}")

        elif clave in (
            "titulo_contains", "titulo_not_contains",
            "descripcion_contains", "organismo_contains", "estado",
        ):
            if not isinstance(valor, str):
                errores.append(f"'{clave}' debe ser string, recibido: {type(valor).__name__}")

    return errores


def describe_filtros(filtros: Dict) -> str:
    """
    Genera una descripción legible de los filtros para mostrar en el dashboard.
    Ejemplo: "Región 13 · Monto ≥ $1.000.000 · Título contiene 'software'"
    """
    partes = []
    mapa_desc = {
        "region":               lambda v: f"Región {v}",
        "region_in":            lambda v: f"Región en {v}",
        "monto_min":            lambda v: f"Monto ≥ ${int(v):,}",
        "monto_max":            lambda v: f"Monto ≤ ${int(v):,}",
        "titulo_contains":      lambda v: f"Título contiene «{v}»",
        "titulo_not_contains":  lambda v: f"Título excluye «{v}»",
        "descripcion_contains": lambda v: f"Descripción contiene «{v}»",
        "codigo_producto_in":   lambda v: f"Producto en {v}",
        "organismo_contains":   lambda v: f"Organismo contiene «{v}»",
        "estado":               lambda v: f"Estado = {v}",
        "estado_in":            lambda v: f"Estado en {v}",
    }
    for clave, valor in filtros.items():
        fn = mapa_desc.get(clave)
        if fn:
            try:
                partes.append(fn(valor))
            except Exception:
                partes.append(f"{clave}={valor}")

    return " · ".join(partes) if partes else "Sin filtros (coincide con todo)"
