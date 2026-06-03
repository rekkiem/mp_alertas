# RUNBOOK — MP Alertas

Guía operacional completa: arranque local, AWS, monitoreo, troubleshooting y mantenimiento.

---

## Inicio rápido en 3 pasos

```bash
# 1. Configurar
cp .env.example .env && nano .env

# 2. Instalar y verificar
make install && make test

# 3. Arrancar
make demo        # con datos de ejemplo
# → http://localhost:5000   admin / (DASHBOARD_PASS en .env)
```

---

## Modos de ejecución

| Comando | Descripción |
|---|---|
| `make run` | Servidor Flask + scheduler en background |
| `make run-once` | Una sola pasada de reglas activas (sin servidor) |
| `make demo` | Seed de ejemplo + servidor |
| `make test` | Suite de 92 assertions |
| `make docker-up` | Docker Compose con SQLite |
| `make docker-up-pg` | Docker Compose con PostgreSQL |
| `make deploy-aws` | CI/CD completo a ECS Fargate |

---

## Variables de entorno requeridas

### Mínimas para funcionar
```env
TICKET_MERCADO_PUBLICO=TU_TICKET   # apis.mercadopublico.cl
DASHBOARD_PASS=clave_segura
SMTP_USER=tu@gmail.com
SMTP_PASSWORD=xxxx_xxxx_xxxx_xxxx  # Google App Password
```

### Completas (ver `.env.example`)
```env
# API
TICKET_MERCADO_PUBLICO=...
API_RATE_PER_SECOND=5
API_RATE_PER_DAY=10000

# BD
DATABASE_URL=sqlite:///./mercadopublico.db
# DATABASE_URL=postgresql://user:pass@host:5432/mp_alertas

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu@gmail.com
SMTP_PASSWORD=app_password
SMTP_FROM="MP Alertas <tu@gmail.com>"
SMTP_USE_TLS=true

# Dashboard
DASHBOARD_USER=admin
DASHBOARD_PASS=clave_muy_segura
SECRET_KEY=clave_aleatoria_larga

# Notificaciones admin
ADMIN_EMAIL=admin@tudominio.cl

# Scheduler
SCHEDULER_HOUR=6
SCHEDULER_MINUTE=0
TIMEZONE=America/Santiago

# Alertas
ALERT_DEDUP_DAYS=30
APP_BASE_URL=https://tudominio.cl
```

---

## Operaciones locales

### Primer arranque
```bash
git clone <repo> && cd mp_alertas
make install          # crea .venv e instala deps
# Editar .env
make init-db          # crea las tablas
make demo             # seed + servidor
```

### Operaciones diarias
```bash
# Ver logs en tiempo real
tail -f logs/app.log

# Ejecutar ciclo del scheduler ahora (sin esperar las 6 AM)
make run-once

# Ejecutar una sola regla (ID=3)
python -c "
from app.scheduler import ejecutar_regla_manualmente
print(ejecutar_regla_manualmente(3))
"

# Consultar alertas recientes (últimas 24h)
python -c "
from app.database import get_db
from app.models import AlertaGenerada
from datetime import datetime, timedelta, timezone
with get_db() as db:
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    alertas = db.query(AlertaGenerada).filter(AlertaGenerada.fecha_alerta >= desde).all()
    print(f'{len(alertas)} alertas en las últimas 24h')
    for a in alertas:
        print(f'  [{a.entidad_id}] {a.datos_resumen.get(\"titulo\",\"\")[:60]}')
"

# Agregar regla desde CLI (sin dashboard)
python -c "
from app.database import get_db, init_db
from app.models import ReglaUsuario
import json
init_db()
with get_db() as db:
    db.add(ReglaUsuario(
        nombre_regla='TI-RM-CLI',
        activa=True,
        tipo_entidad='licitacion',
        filtros={'region': 13, 'titulo_contains': 'software', 'monto_min': 500000},
        email_destino='tu@email.cl',
    ))
    print('Regla creada OK')
"
```

### Actualizar tasa de cambio USD/EUR/UF
```bash
python -c "
# Editar app/normalizer.py → _FX_RATES
from app.normalizer import _FX_RATES
_FX_RATES['USD'] = 950.0   # actualizar según mercado
_FX_RATES['UF']  = 38500.0
print(_FX_RATES)
"
# Luego reiniciar el servidor
```

---

## Operaciones Docker local

```bash
# Arrancar
docker compose up -d

# Logs
docker compose logs -f app

# Shell dentro del contenedor
docker compose exec app python main.py --once

# Backup de la BD
docker compose exec app sqlite3 /data/mercadopublico.db .dump > backup_$(date +%Y%m%d).sql

# Detener
docker compose down

# Con PostgreSQL
docker compose --profile postgres up -d
docker compose exec db psql -U mp_user -d mp_alertas -c "SELECT COUNT(*) FROM alertas_generadas;"
```

---

## Despliegue en AWS

### Prerequisitos
```bash
# Instalar AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install

# Configurar credenciales
aws configure
# AWS Access Key ID: AKIA...
# AWS Secret Access Key: ...
# Default region: us-east-1
# Default output format: json

# Verificar permisos mínimos necesarios:
# ecr:*, ecs:*, cloudformation:*, iam:PassRole, secretsmanager:*
# rds:*, ec2:DescribeVpcs/Subnets/SecurityGroups
# elasticloadbalancing:*, logs:*
```

### Primera vez
```bash
# 1. Crear el archivo de parámetros
cp deploy/aws/params.example.json deploy/aws/params.production.json
nano deploy/aws/params.production.json

# Campos obligatorios a completar:
# - VpcId: vpc-xxxxxxxxxxxxxxxxx
# - SubnetIds: subnet-aaa,subnet-bbb   (subnets PÚBLICAS, mín 2 AZ)
# - PrivateSubnetIds: subnet-ccc,subnet-ddd (subnets PRIVADAS para RDS)
# - DbPassword: mín 16 caracteres
# - DashboardPass: clave del dashboard
# - TicketMercadoPublico: tu ticket de API
# - SmtpPassword: App Password de Gmail
# - SmtpUser: tu@gmail.com
# - AdminEmail: quien recibe resúmenes del scheduler

# 2. Deploy completo (tests + build + CloudFormation + ECS)
make deploy-aws

# El proceso tarda ~15-20 min la primera vez (crea RDS + ECS + ALB)
# Al final muestra la URL del dashboard
```

### Actualizaciones posteriores
```bash
# Código cambiado → rebuild + redeploy (rolling, sin downtime)
git add . && git commit -m "fix: ..."
make deploy-aws

# Solo actualizar parámetros de CloudFormation (sin rebuild de imagen)
make deploy-aws-stack

# Solo rebuild de imagen (sin tocar infraestructura)
make deploy-aws-build
```

### Rollback
```bash
# Revertir al deployment anterior
make rollback-aws

# O manualmente seleccionar una task definition específica
aws ecs list-task-definitions --family-prefix mp-alertas-task --region us-east-1
aws ecs update-service --cluster mp-alertas-cluster \
    --service mp-alertas-service \
    --task-definition mp-alertas-task:3    # ← versión anterior
    --region us-east-1
```

---

## Monitoreo en AWS

### Logs en tiempo real
```bash
# CloudWatch Logs
aws logs tail /ecs/mp-alertas --follow --region us-east-1

# Solo errores
aws logs tail /ecs/mp-alertas --follow \
    --filter-pattern "[ERROR]" --region us-east-1

# Últimas 100 líneas
aws logs tail /ecs/mp-alertas \
    --since 1h --region us-east-1 | tail -100
```

### Estado del servicio ECS
```bash
# Estado general
aws ecs describe-services \
    --cluster mp-alertas-cluster \
    --services mp-alertas-service \
    --region us-east-1 \
    --query "services[0].{Status:status,Running:runningCount,Desired:desiredCount,Health:healthCheckGracePeriodSeconds}"

# Tareas en ejecución
aws ecs list-tasks \
    --cluster mp-alertas-cluster \
    --service-name mp-alertas-service \
    --region us-east-1

# Forzar nueva tarea (equivalente a restart)
aws ecs update-service \
    --cluster mp-alertas-cluster \
    --service mp-alertas-service \
    --force-new-deployment \
    --region us-east-1
```

### Alertas CloudWatch (recomendado configurar)
```bash
# Alarma si la tarea ECS falla
aws cloudwatch put-metric-alarm \
    --alarm-name "mp-alertas-task-count" \
    --alarm-description "Alerta si no hay tareas ECS corriendo" \
    --metric-name RunningTaskCount \
    --namespace ECS/ContainerInsights \
    --dimensions Name=ClusterName,Value=mp-alertas-cluster \
                 Name=ServiceName,Value=mp-alertas-service \
    --statistic Average \
    --period 300 \
    --evaluation-periods 2 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:mp-alertas-alerts \
    --region us-east-1
```

---

## Troubleshooting

### No llegan emails

```bash
# 1. Verificar SMTP en .env
grep SMTP .env

# 2. Test de conexión SMTP manual
python -c "
import smtplib, os
from dotenv import load_dotenv
load_dotenv()
with smtplib.SMTP(os.getenv('SMTP_HOST'), int(os.getenv('SMTP_PORT'))) as smtp:
    smtp.ehlo()
    smtp.starttls()
    smtp.login(os.getenv('SMTP_USER'), os.getenv('SMTP_PASSWORD'))
    print('SMTP OK')
"

# 3. Gmail: verificar que tenés App Password (no la contraseña normal)
# → myaccount.google.com/apppasswords

# 4. Revisar logs del worker de email
grep "email\|SMTP\|ERROR" logs/app.log | tail -30
```

### La API de Mercado Público devuelve 401/403
```bash
# Ticket expirado o inválido
curl -s "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json?ticket=TU_TICKET&fecha=01062024" | python -m json.tool | head -20

# Si devuelve {"Cantidad":0,"FechaCreacion":...,"Listado":[]} → ticket válido
# Si devuelve error → solicitar nuevo ticket en apis.mercadopublico.cl
```

### No se generan alertas (reglas activas pero 0 resultados)

```bash
# Diagnóstico paso a paso
python -c "
from app.database import get_db, init_db
from app.models import ReglaUsuario, LicitacionSnapshot, AlertaGenerada
from app.filter_engine import evaluar_regla

init_db()
with get_db() as db:
    reglas = db.query(ReglaUsuario).filter_by(activa=True).all()
    snaps  = db.query(LicitacionSnapshot).all()
    alertas= db.query(AlertaGenerada).all()
    print(f'Reglas activas:  {len(reglas)}')
    print(f'Snapshots en BD: {len(snaps)}')
    print(f'Alertas en BD:   {len(alertas)}')
    
    for regla in reglas:
        matches = [s for s in snaps if evaluar_regla(regla, s.datos)]
        print(f'  Regla [{regla.id}] \"{regla.nombre_regla}\": {len(matches)} matches en {len(snaps)} snaps')
        print(f'    Filtros: {regla.filtros}')
"
```

### La BD SQLite está bloqueada (Concurrent Access)
```bash
# Verificar WAL mode activo
sqlite3 mercadopublico.db "PRAGMA journal_mode;"
# Debería retornar: wal

# Si hay lock, esperar o reiniciar
lsof mercadopublico.db

# Migrar a PostgreSQL para mayor concurrencia:
# DATABASE_URL=postgresql://user:pass@localhost:5432/mp_alertas
```

### Cuota de API agotada (10,000 req/día)
```bash
# Ver en logs cuántas requests se hicieron
grep "Cuota\|quota\|10000\|rate" logs/app.log

# Reducir el número de reglas que consumen API
# O reducir la frecuencia: ajustar SCHEDULER_HOUR para que corra menos veces

# Optimización: el scheduler ya agrupa por tipo de entidad,
# pero si hay muchas reglas del mismo tipo, revisa que los filtros
# de API (region, fecha) sean lo más restrictivos posible
```

### Tareas ECS que no arrancan (AWS)
```bash
# Ver eventos del servicio
aws ecs describe-services \
    --cluster mp-alertas-cluster \
    --services mp-alertas-service \
    --query "services[0].events[:5]" \
    --region us-east-1

# Ver logs de la tarea que falló
TASK_ARN=$(aws ecs list-tasks \
    --cluster mp-alertas-cluster \
    --desired-status STOPPED \
    --region us-east-1 \
    --query "taskArns[0]" --output text)

aws ecs describe-tasks \
    --cluster mp-alertas-cluster \
    --tasks $TASK_ARN \
    --query "tasks[0].containers[0].reason" \
    --region us-east-1
```

---

## Mantenimiento

### Purgar datos viejos (recomendado mensualmente)

```bash
python -c "
from app.database import get_db
from app.models import AlertaGenerada, LicitacionSnapshot
from datetime import datetime, timedelta, timezone

with get_db() as db:
    # Purgar alertas de más de 90 días
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
    n = db.query(AlertaGenerada).filter(AlertaGenerada.fecha_alerta < cutoff).delete()
    print(f'Alertas eliminadas: {n}')
    
    # Purgar snapshots de más de 7 días
    cutoff7 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    n2 = db.query(LicitacionSnapshot).filter(LicitacionSnapshot.fecha_sincronizacion < cutoff7).delete()
    print(f'Snapshots eliminados: {n2}')
"
```

### Backup en AWS (RDS)
```bash
# Snapshot manual
aws rds create-db-snapshot \
    --db-instance-identifier mp-alertas-db \
    --db-snapshot-identifier mp-alertas-backup-$(date +%Y%m%d) \
    --region us-east-1

# Ver snapshots disponibles
aws rds describe-db-snapshots \
    --db-instance-identifier mp-alertas-db \
    --region us-east-1 \
    --query "DBSnapshots[*].{Id:DBSnapshotIdentifier,Status:Status,Date:SnapshotCreateTime}"
```

### Actualizar tasa de cambio (UF/USD)
```bash
# Editar app/normalizer.py → _FX_RATES
# Luego reiniciar el servidor (o el contenedor Docker/ECS)
docker compose restart app
# o en AWS:
aws ecs update-service --cluster mp-alertas-cluster \
    --service mp-alertas-service --force-new-deployment --region us-east-1
```

### CI/CD con GitHub Actions

Configurar estos secrets en tu repo (`Settings → Secrets → Actions`):

| Secret | Valor |
|---|---|
| `AWS_ROLE_ARN_STAGING` | ARN del IAM Role para staging |
| `AWS_ROLE_ARN_PROD` | ARN del IAM Role para producción |
| `VPC_ID_STAGING` / `VPC_ID_PROD` | VPC IDs |
| `SUBNET_IDS_STAGING` / `SUBNET_IDS_PROD` | Subnet IDs (comma-separated) |
| `PRIVATE_SUBNET_IDS_*` | Subnets privadas para RDS |
| `DB_PASSWORD_STAGING` / `DB_PASSWORD_PROD` | Contraseñas de BD |
| `DASHBOARD_PASS_STAGING` / `DASHBOARD_PASS_PROD` | Contraseñas del dashboard |
| `TICKET_MP_STAGING` / `TICKET_MP_PROD` | Tickets de API |
| `SMTP_PASSWORD` | App Password SMTP |
| `SMTP_USER` | Email SMTP |
| `ADMIN_EMAIL` | Email admin |
| `ACM_CERT_ARN_PROD` | ARN certificado HTTPS (producción) |

Flujo automático:
- Push a `main` → tests → deploy a **staging**
- Tag `v*` (ej. `v1.2.0`) → tests → staging → deploy a **producción**

```bash
git tag v1.0.0 && git push origin v1.0.0
```

---

## Arquitectura de referencia

```
┌─────────────────────────────────────────────────────────────┐
│                        mp_alertas                           │
│                                                             │
│  ┌──────────┐    ┌─────────────┐    ┌───────────────────┐  │
│  │ Flask    │    │ APScheduler │    │ Email Worker      │  │
│  │ :5000    │    │ 06:00 AM    │    │ (thread daemon)   │  │
│  └────┬─────┘    └──────┬──────┘    └────────┬──────────┘  │
│       │                 │                    │              │
│       └────────┬────────┘                    │              │
│                │                             │              │
│      ┌─────────▼─────────┐        ┌──────────▼──────────┐  │
│      │   SQLAlchemy ORM  │        │     smtplib SMTP    │  │
│      │   (SQLite / PG)   │        │   (Gmail/SendGrid)  │  │
│      └─────────┬─────────┘        └─────────────────────┘  │
│                │                                            │
└────────────────┼────────────────────────────────────────────┘
                 │
      ┌──────────▼──────────┐
      │   API Mercado       │
      │   Público           │
      │   (rate-limited)    │
      └─────────────────────┘

AWS Production:
  Route53 → ALB → ECS Fargate → RDS PostgreSQL
                              → Secrets Manager
                              → CloudWatch Logs
```
