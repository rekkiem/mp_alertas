"""
app/models.py — Modelos ORM (SQLAlchemy).

Tablas:
  reglas_usuario       → reglas de filtrado configuradas por el usuario
  alertas_generadas    → histórico de coincidencias detectadas
  licitaciones_snapshot→ caché incremental de licitaciones (sincronización diaria)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── ReglaUsuario ─────────────────────────────────────────────────────────────

class ReglaUsuario(Base):
    __tablename__ = "reglas_usuario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    nombre_regla: Mapped[str] = mapped_column(String(255), nullable=False)
    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Valores válidos: licitacion | orden_compra | compra_agil
    tipo_entidad: Mapped[str] = mapped_column(String(50), nullable=False)

    # JSON con los criterios: region, monto_min, monto_max, titulo_contains, etc.
    filtros: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Uno o varios emails separados por coma
    email_destino: Mapped[str] = mapped_column(String(500), nullable=False)

    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    fecha_ultima_ejecucion: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Relación 1→N con alertas
    alertas: Mapped[list[AlertaGenerada]] = relationship(
        "AlertaGenerada", back_populates="regla", lazy="dynamic", cascade="all, delete-orphan"
    )

    @property
    def emails_lista(self) -> list[str]:
        """Retorna lista de emails (split por coma)."""
        return [e.strip() for e in self.email_destino.split(",") if e.strip()]

    def __repr__(self) -> str:
        return f"<ReglaUsuario id={self.id} nombre='{self.nombre_regla}'>"


# ── AlertaGenerada ───────────────────────────────────────────────────────────

class AlertaGenerada(Base):
    __tablename__ = "alertas_generadas"
    __table_args__ = (
        UniqueConstraint("regla_id", "entidad_id", name="uq_regla_entidad"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    regla_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reglas_usuario.id", ondelete="CASCADE"), nullable=False
    )
    # Código único de la entidad (ej. "1234567-1-LE24")
    entidad_id: Mapped[str] = mapped_column(String(150), nullable=False, index=True)

    # Resumen con: numero, titulo, monto, fecha_publicacion, link_detalle, tipo, etc.
    datos_resumen: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    fecha_alerta: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, index=True
    )

    enviado_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mostrado_dashboard: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relación inversa
    regla: Mapped[ReglaUsuario] = relationship("ReglaUsuario", back_populates="alertas")

    def __repr__(self) -> str:
        return f"<AlertaGenerada id={self.id} entidad='{self.entidad_id}'>"


# ── LicitacionSnapshot ───────────────────────────────────────────────────────

class LicitacionSnapshot(Base):
    """
    Caché incremental diaria de licitaciones para reducir llamadas a la API.
    Estrategia: guardar solo las últimas 24-48 h; purgar con cron semanal.
    """
    __tablename__ = "licitaciones_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # CodigoExterno de la licitación
    codigo: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)

    tipo: Mapped[str] = mapped_column(String(50), nullable=False, default="licitacion")

    # Datos normalizados completos en JSON
    datos: Mapped[dict] = mapped_column(JSON, nullable=False)

    fecha_publicacion: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    estado: Mapped[str | None] = mapped_column(String(80), nullable=True)
    region: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monto_clp: Mapped[int | None] = mapped_column(Integer, nullable=True)

    fecha_sincronizacion: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<LicitacionSnapshot codigo='{self.codigo}'>"
