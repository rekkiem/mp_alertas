# MP Alertas 🔔

**Monitor inteligente de Mercado Público** — Detecta licitaciones, órdenes de compra y compra ágil según tus reglas de filtrado y envía alertas por email.

---

## ¿Qué hace?

| Característica | Detalle |
|---|---|
| 🎯 Motor de filtros | Región, monto min/max, texto en título/descripción, códigos de producto, estado |
| ⏰ Scheduler diario | Se ejecuta a las 06:00 AM (hora Chile) via APScheduler |
| 📧 Alertas por email | HTML profesional con datos clave + link directo a Mercado Público |
| 📊 Dashboard web | Gestión de reglas, historial de alertas, analytics con gráficos |
| 🔄 Sincronización inteligente | Descarga una vez por tipo, evalúa todas las reglas en memoria |
| 🐳 Dockerizado | `docker compose up -d` y listo |

---

## Inicio rápido

### 1. Prerrequisitos

- Python 3.11+ **o** Docker + Docker Compose
- Ticket de API de Mercado Público → [apis.mercadopublico.cl](https://apis.mercadopublico.cl/)
- Cuenta SMTP (Gmail con App Password, SendGrid, etc.)

### 2. Configuración

```bash
# Clonar / descomprimir el proyecto
cd mp_alertas

# Copiar variables de entorno
cp .env.example .env

# Editar .env con tus valores reales
nano .env   # o code .env
```

Variables **mínimas** para funcionar:

```env
TICKET_MERCADO_PUBLICO=TU_TICKET
SMTP_HOST=smtp.gmail.com
SMTP_USER=tu@gmail.com
SMTP_PASSWORD=xxxx_xxxx_xxxx_xxxx
SMTP_FROM=tu@gmail.com
DASHBOARD_PASS=una_clave_segura
```

### 3a. Ejecución con Python

```bash
# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Inicializar BD y datos de demo
python main.py --demo

# Arrancar servidor
python main.py
```

Dashboard disponible en: **http://localhost:5000**
Usuario: `admin` / Contraseña: (la de `DASHBOARD_PASS`)

### 3b. Ejecución con Docker

```bash
# Construir imagen
docker compose build

# Arrancar (SQLite, recomendado para empezar)
docker compose up -d

# Ver logs
docker compose logs -f app

# Con PostgreSQL
docker compose --profile postgres up -d
```

---

## Comandos CLI

```bash
python main.py                  # Servidor web + scheduler (modo normal)
python main.py --once           # Una pasada inmediata de todas las reglas activas
python main.py --init-db        # Solo crear tablas BD y salir
python main.py --demo           # Insertar reglas de ejemplo + arrancar servidor
```

---

## Estructura del proyecto

```
mp_alertas/
├── main.py                     # Punto de entrada único
├── config.py                   # Settings (pydantic-settings + .env)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
└── app/
    ├── __init__.py
    ├── database.py             # SQLAlchemy engine + session
    ├── models.py               # ORM: ReglaUsuario, AlertaGenerada, LicitacionSnapshot
    ├── api_client.py           # Cliente API Mercado Público (rate limit + retry + paginación)
    ├── normalizer.py           # Transforma respuestas crudas a dict uniforme
    ├── filter_engine.py        # evaluar_regla() — motor de filtros en memoria
    ├── email_service.py        # Cola + worker SMTP
    ├── scheduler.py            # APScheduler + ciclo diario
    ├── analytics.py            # Insights y KPIs
    │
    └── dashboard/
        ├── __init__.py         # Flask factory
        ├── routes.py           # Todas las rutas (Basic Auth)
        └── templates/
            ├── base.html       # Layout + design system
            ├── index.html      # Lista de reglas
            ├── regla_form.html # Crear / editar regla
            ├── alertas.html    # Historial de alertas
            ├── alerta_detalle.html
            └── analytics.html  # Dashboard de analytics
```

---

## Modelo de filtros

El campo `filtros` de cada regla es un JSON con los siguientes operadores:

| Operador | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `region` | int | Región exacta (código) | `13` → RM |
| `region_in` | list[int] | Una de varias regiones | `[10, 14]` |
| `monto_min` | int | Monto mínimo en CLP | `1000000` |
| `monto_max` | int | Monto máximo en CLP | `50000000` |
| `titulo_contains` | str | Palabra(s) en el título | `"software"` |
| `descripcion_contains` | str | Palabra(s) en la descripción | `"consultoría"` |
| `organismo_contains` | str | Nombre del organismo | `"municipalidad"` |
| `codigo_producto` | str | Código de categoría exacto | `"30200000"` |
| `codigo_producto_in` | list[str] | Uno de varios códigos | `["30200000","48100000"]` |
| `estado` | str | Estado exacto | `"Publicada"` |
| `estado_in` | list[str] | Uno de varios estados | `["Publicada","Adjudicada"]` |
| `fecha_publicacion_desde` | str | Publicada desde (YYYY-MM-DD) | `"2024-01-01"` |
| `fecha_publicacion_hasta` | str | Publicada hasta (YYYY-MM-DD) | `"2024-12-31"` |

**Todos los criterios se combinan con AND lógico.**

### Ejemplo completo de filtros JSON

```json
{
  "region": 13,
  "titulo_contains": "software",
  "monto_min": 1000000,
  "monto_max": 50000000,
  "estado_in": ["Publicada", "Adjudicada"],
  "codigo_producto_in": ["30200000", "43230000"]
}
```

---

## Regiones de Chile

| Código | Nombre |
|---|---|
| 1 | Tarapacá |
| 2 | Antofagasta |
| 3 | Atacama |
| 4 | Coquimbo |
| 5 | Valparaíso |
| 6 | O'Higgins |
| 7 | Maule |
| 8 | Biobío |
| 9 | Araucanía |
| 10 | Los Lagos |
| 11 | Aysén |
| 12 | Magallanes |
| 13 | Metropolitana |
| 14 | Los Ríos |
| 15 | Arica y Parinacota |
| 16 | Ñuble |

---

## Integración con la API de Mercado Público

### Autenticación

La API v1 requiere un `ticket` en el query string:
```
GET https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json?ticket=TU_TICKET
```

### Rate limiting

El cliente respeta automáticamente:
- **5 requests/segundo** (configurable con `API_RATE_PER_SECOND`)
- **10,000 requests/día** (configurable con `API_RATE_PER_DAY`)
- Retry con backoff exponencial ante 429/503
- Detención automática al agotar la cuota diaria

### Estrategia de sincronización

Para minimizar el consumo de API cuando hay múltiples reglas:

1. El scheduler agrupa las reglas por tipo de entidad
2. Descarga **una sola vez** los datos del día para cada tipo
3. Persiste en `licitaciones_snapshot`
4. Evalúa **todas** las reglas sobre el conjunto en memoria

---

## Variables de entorno completas

Ver `.env.example` para la lista completa con descripciones.

---

## Producción

### Con Docker (recomendado)

```bash
# PostgreSQL + Nginx
docker compose --profile postgres --profile nginx up -d
```

### Sin Docker

```bash
# Usar gunicorn para Flask, junto al scheduler en el mismo proceso
python main.py
```

> **Nota de seguridad:** En producción, cambia `DASHBOARD_PASS` y `SECRET_KEY`.
> Usa HTTPS (nginx reverse proxy + Let's Encrypt).

---

## Troubleshooting

**No llegan emails:**
1. Verificar `SMTP_*` en `.env`
2. En Gmail: activar "Contraseñas de aplicación" en la cuenta Google
3. Revisar logs: `docker compose logs -f app | grep email`

**La API devuelve 401/403:**
1. Verificar `TICKET_MERCADO_PUBLICO`
2. El ticket puede expirar — solicitar uno nuevo en apis.mercadopublico.cl

**No se generan alertas:**
1. Ejecutar `python main.py --once` y revisar logs
2. Verificar que las reglas estén **activas** en el dashboard
3. Revisar que los filtros JSON sean válidos (el dashboard los valida)

---

## Licencia

MIT — Uso libre para proyectos comerciales y personales.
