# MP Alertas — Inicio Rápido con Datos Reales

## Pre-requisitos
- Python 3.11+ instalado
- Tu ticket de API en `.env`
- Cuenta SMTP (Gmail recomendado)

---

## PASO 1 — Configurar `.env`

```bash
cp .env.example .env
```

Editar `.env` con tus valores reales:

```env
# ── OBLIGATORIO ──────────────────────────────────────────────
TICKET_MERCADO_PUBLICO=tu_ticket_aqui

# ── EMAIL (Gmail con App Password) ──────────────────────────
# Obtener App Password: myaccount.google.com → Seguridad → Contraseñas de aplicación
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu@gmail.com
SMTP_PASSWORD=xxxx_xxxx_xxxx_xxxx
SMTP_FROM=tu@gmail.com
SMTP_USE_TLS=true

# ── DASHBOARD ────────────────────────────────────────────────
DASHBOARD_USER=admin
DASHBOARD_PASS=tu_clave_segura

# ── NOTIFICACIONES ────────────────────────────────────────────
ADMIN_EMAIL=tu@gmail.com
```

---

## PASO 2 — Instalar y arrancar

```bash
# Crear entorno virtual e instalar dependencias
make install

# Verificar que todo está OK antes de continuar
make test
```

Deberías ver: `✅ Passed: 92/92`

---

## PASO 3 — Verificar conectividad con la API

```bash
python scripts/diagnostico.py
```

Salida esperada:
```
✅ Ticket cargado (36 chars): abc12345…xyz
✅ LICITACION fecha=03062026: 847 registros disponibles
✅ ORDEN_COMPRA fecha=03062026: 312 registros disponibles
✅ Licitación: normalizado OK
   codigo: '1234567-1-LE26'
   titulo: 'Adquisición de equipamiento...'
   region: 13
   monto_clp: 5000000
```

Si ves `401 Ticket inválido` → solicita nuevo ticket en apis.mercadopublico.cl

---

## PASO 4 — Arrancar el servidor

```bash
python main.py
```

Dashboard disponible en: **http://localhost:5000**
- Usuario: `admin` (o lo que pusiste en `.env`)
- Contraseña: la de `DASHBOARD_PASS`

> Deja este terminal abierto. Abre uno nuevo para los siguientes pasos.

---

## PASO 5 — Poblar la BD con datos reales

```bash
# Descargar últimos 7 días (recomendado para empezar)
python scripts/poblar_bd.py --dias 7

# O los últimos 30 días si quieres más historial
python scripts/poblar_bd.py --dias 30

# Solo licitaciones, 3 días (más rápido para probar)
python scripts/poblar_bd.py --tipo licitacion --dias 3
```

Salida esperada:
```
── LICITACION ───────────────────────────────────
  Descargando datos desde 27052026…
  [████████████████████████] 1847/1847 descargados…
  ✅ licitacion: 1847 descargados

── RESUMEN FINAL ────────────────────────────────
  Insertados:   1847
  Total en BD:  1847 registros
  🚀 BD poblada correctamente.
```

---

## PASO 6 — Crear reglas de alerta

**Opción A — Script automático (recomendado para empezar):**

```bash
python scripts/crear_reglas_ejemplo.py
```

Crea 8 reglas predefinidas: TI/Software en RM, consultoría, notebooks, Los Lagos/Los Ríos, OC grandes, compra ágil.

**Opción B — Desde el dashboard:**

1. Ir a http://localhost:5000
2. Clic en **"Nueva Regla"**
3. Completar formulario:
   - **Nombre:** Licitaciones Software RM
   - **Tipo:** Licitación
   - **Email destino:** tu@gmail.com
   - **Filtros JSON:**
     ```json
     {
       "region": 13,
       "titulo_contains": "software",
       "monto_min": 1000000
     }
     ```
4. Clic en **"Guardar"**

---

## PASO 7 — Ejecutar primer ciclo real

```bash
# Ejecuta todas las reglas activas contra los datos en BD
python main.py --once
```

Salida esperada:
```
2026-06-03 06:00:00 [INFO] Iniciando ciclo completo...
2026-06-03 06:00:01 [INFO] Reglas activas: 8
2026-06-03 06:00:01 [INFO] [Regla 1/8] 'Licitaciones TI/Software RM'
2026-06-03 06:00:01 [INFO]   → Evaluando 1847 entidades...
2026-06-03 06:00:02 [INFO]   → 12 coincidencias encontradas
2026-06-03 06:00:02 [INFO]   → 12 alertas nuevas generadas
2026-06-03 06:00:02 [INFO]   → 12 emails encolados para envío
...
2026-06-03 06:00:15 [INFO] Ciclo completado: 8 reglas, 47 alertas totales
```

**Las alertas aparecerán en:**
- El dashboard → sección "Alertas"
- Tu bandeja de entrada (los emails pueden tardar 1-2 min)

---

## PASO 8 — Verificar en el dashboard

1. Ir a **http://localhost:5000/alertas**
2. Ver las alertas generadas con datos reales
3. Clic en cualquier alerta para ver el detalle completo
4. El link "Ver en Mercado Público" lleva directamente a la licitación

---

## Scheduler automático (producción)

El servidor ya incluye el scheduler. Si dejas `python main.py` corriendo, ejecutará el ciclo automáticamente todos los días a las **06:00 AM hora Chile** sin intervención manual.

Para cambiar la hora de ejecución:
```env
SCHEDULER_HOUR=8    # 8:00 AM
SCHEDULER_MINUTE=30 # 8:30 AM
```

---

## Comandos útiles del día a día

```bash
# Ver logs en tiempo real
tail -f logs/app.log

# Ejecutar ciclo manual inmediato
python main.py --once

# Re-sincronizar datos frescos
python scripts/poblar_bd.py --dias 1

# Ver cuántas alertas hay en la BD
python -c "
from app.database import get_db, init_db; init_db()
from app.models import AlertaGenerada, ReglaUsuario
with get_db() as db:
    print(f'Reglas: {db.query(ReglaUsuario).count()}')
    print(f'Alertas: {db.query(AlertaGenerada).count()}')
"

# Limpiar snapshots viejos (>30 días)
python -c "
from app.database import get_db, init_db; init_db()
from app.models import LicitacionSnapshot
from datetime import datetime, timedelta
with get_db() as db:
    n = db.query(LicitacionSnapshot).filter(
        LicitacionSnapshot.fecha_sincronizacion < datetime.utcnow() - timedelta(days=30)
    ).delete()
    print(f'Eliminados: {n} snapshots viejos')
"
```

---

## Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `401 Ticket inválido` | Ticket expirado o incorrecto | Solicitar nuevo ticket en apis.mercadopublico.cl |
| No llegan emails | SMTP mal configurado | Verificar `SMTP_*` en `.env`. Gmail necesita App Password. |
| `0 alertas generadas` | Filtros muy restrictivos | Reducir `monto_min` o quitar filtros por región |
| Dashboard no carga | Puerto 5000 ocupado | `PORT=5001 python main.py` |
| `0 registros disponibles` | Fecha sin datos | Probar con `--dias 7` en lugar de `--dias 1` |

