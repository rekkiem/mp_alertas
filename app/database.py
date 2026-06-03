"""
app/database.py — Motor SQLAlchemy, sesión y helpers de ciclo de vida.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from contextlib import contextmanager
import logging
import os

from config import settings

logger = logging.getLogger(__name__)

# ── Engine ───────────────────────────────────────────────────────────────────
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False,        # Cambiar a True para debug SQL
    pool_pre_ping=True,
)

# Habilitar WAL mode para SQLite (mejor concurrencia Flask + scheduler)
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    if settings.DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# ── Session factory ──────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


@contextmanager
def get_db() -> Session:
    """Context manager para obtener sesión de BD con cierre automático."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Crea todas las tablas si no existen."""
    from app import models  # noqa: F401 — importar para registrar los modelos
    Base.metadata.create_all(bind=engine)
    logger.info("Base de datos inicializada correctamente.")
