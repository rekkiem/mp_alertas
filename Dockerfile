# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Instalar dependencias de compilación
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="tu@email.com"
LABEL description="MP Alertas — Monitor de Mercado Público con alertas por email"
LABEL version="1.0.0"

# Variables de entorno de producción
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    DATABASE_URL=sqlite:////data/mercadopublico.db \
    TZ=America/Santiago

# Runtime deps mínimas (para PostgreSQL si se usa)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl tzdata && \
    rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios
RUN useradd -m -u 1001 appuser

# Copiar paquetes desde el builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copiar código fuente
COPY --chown=appuser:appuser . .

# Directorio para datos persistentes y logs
RUN mkdir -p /data /app/logs && \
    chown -R appuser:appuser /data /app/logs

USER appuser

# Healthcheck: verifica que el dashboard responde
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f -u "${DASHBOARD_USER:-admin}:${DASHBOARD_PASS:-admin}" \
        http://localhost:${PORT:-5000}/ || exit 1

EXPOSE ${PORT:-5000}

# Punto de entrada: gunicorn en producción, python main.py en desarrollo
ENTRYPOINT ["python", "main.py"]
CMD []
