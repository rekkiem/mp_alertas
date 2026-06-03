#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# deploy/aws/deploy.sh — CI/CD completo para AWS ECS Fargate
#
# Requisitos:
#   - AWS CLI v2 configurado (aws configure)
#   - Docker instalado
#   - Permisos IAM: ecr:*, ecs:*, cloudformation:*, iam:PassRole
#
# Uso:
#   ./deploy/aws/deploy.sh              # Deploy completo
#   ./deploy/aws/deploy.sh --build-only # Solo build y push de imagen
#   ./deploy/aws/deploy.sh --stack-only # Solo actualizar CloudFormation
#   ./deploy/aws/deploy.sh --rollback   # Rollback a la versión anterior
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

# ── Config (editar según tu entorno) ──────────────────────────
APP_NAME="mp-alertas"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${APP_NAME}"
STACK_NAME="${APP_NAME}-stack"
ENV="${ENVIRONMENT:-production}"

# ── Colores ────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()    { echo -e "\n${BLUE}══ $* ══${NC}"; }

# ── Herramientas requeridas ────────────────────────────────────
for cmd in aws docker jq; do
    command -v "$cmd" &>/dev/null || error "$cmd no está instalado"
done

# ── Banner ─────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║     MP Alertas — Deploy a AWS ECS Fargate         ║"
echo "╠═══════════════════════════════════════════════════╣"
echo "║  Cuenta:  ${AWS_ACCOUNT_ID}                       ║"
echo "║  Región:  ${AWS_REGION}                           ║"
echo "║  Stack:   ${STACK_NAME}                           ║"
echo "║  Env:     ${ENV}                                  ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

MODE="${1:-full}"

# ══════════════════════════════════════════════════════════════
# FASE 1: Tests
# ══════════════════════════════════════════════════════════════
if [[ "$MODE" != "--stack-only" ]]; then
    step "FASE 1: Ejecutando suite de tests"
    python test_mvp.py 2>&1 | tail -8
    
    if grep -q "Failed: 0" <<< "$(python test_mvp.py 2>&1)"; then
        info "✅ Tests: 100% pass rate"
    else
        error "Tests fallaron. No se puede hacer deploy."
    fi
fi

# ══════════════════════════════════════════════════════════════
# FASE 2: Build y Push de Docker Image
# ══════════════════════════════════════════════════════════════
if [[ "$MODE" != "--stack-only" ]]; then
    step "FASE 2: Build y Push Docker Image"

    # Tag basado en git commit o timestamp
    if git rev-parse --git-dir > /dev/null 2>&1; then
        IMAGE_TAG=$(git rev-parse --short HEAD)
    else
        IMAGE_TAG=$(date '+%Y%m%d-%H%M%S')
    fi

    info "Tag de imagen: $IMAGE_TAG"

    # Login a ECR
    info "Autenticando en ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$ECR_URI"

    # Crear repositorio si no existe
    aws ecr describe-repositories --repository-names "$APP_NAME" --region "$AWS_REGION" &>/dev/null || {
        info "Creando repositorio ECR..."
        aws ecr create-repository --repository-name "$APP_NAME" --region "$AWS_REGION"
    }

    # Build multi-plataforma (para Fargate ARM64 o x86_64)
    PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
    info "Building imagen para ${PLATFORM}..."
    docker buildx build \
        --platform "$PLATFORM" \
        --target runtime \
        -t "${ECR_URI}:${IMAGE_TAG}" \
        -t "${ECR_URI}:latest" \
        --push \
        .

    info "✅ Imagen pusheada: ${ECR_URI}:${IMAGE_TAG}"
    export DOCKER_IMAGE="${ECR_URI}:${IMAGE_TAG}"
fi

# ══════════════════════════════════════════════════════════════
# FASE 3: CloudFormation Stack
# ══════════════════════════════════════════════════════════════
if [[ "$MODE" != "--build-only" ]]; then
    step "FASE 3: Desplegando CloudFormation Stack"

    # Verificar archivo de parámetros
    PARAMS_FILE="deploy/aws/params.${ENV}.json"
    if [ ! -f "$PARAMS_FILE" ]; then
        warn "No se encontró $PARAMS_FILE. Usando params.example.json como guía."
        cat << 'PARAMS_EXAMPLE' > "deploy/aws/params.example.json"
[
  {"ParameterKey": "AppName",              "ParameterValue": "mp-alertas"},
  {"ParameterKey": "Environment",          "ParameterValue": "production"},
  {"ParameterKey": "DockerImage",          "ParameterValue": "123456789.dkr.ecr.us-east-1.amazonaws.com/mp-alertas:latest"},
  {"ParameterKey": "VpcId",               "ParameterValue": "vpc-xxxxxxxxx"},
  {"ParameterKey": "SubnetIds",            "ParameterValue": "subnet-aaa,subnet-bbb"},
  {"ParameterKey": "PrivateSubnetIds",     "ParameterValue": "subnet-ccc,subnet-ddd"},
  {"ParameterKey": "DbPassword",           "ParameterValue": "CAMBIAR_PASSWORD_SEGURO_16CHARS"},
  {"ParameterKey": "DashboardPass",        "ParameterValue": "CAMBIAR_DASHBOARD_PASS"},
  {"ParameterKey": "TicketMercadoPublico", "ParameterValue": "TU_TICKET_API"},
  {"ParameterKey": "SmtpPassword",         "ParameterValue": "TU_SMTP_PASSWORD"},
  {"ParameterKey": "SmtpHost",             "ParameterValue": "smtp.gmail.com"},
  {"ParameterKey": "SmtpUser",             "ParameterValue": "tu@gmail.com"},
  {"ParameterKey": "AdminEmail",           "ParameterValue": "admin@tudominio.cl"},
  {"ParameterKey": "CertificateArn",       "ParameterValue": ""},
  {"ParameterKey": "ContainerCpu",         "ParameterValue": "256"},
  {"ParameterKey": "ContainerMemory",      "ParameterValue": "512"}
]
PARAMS_EXAMPLE
        error "Crea el archivo $PARAMS_FILE con tus valores (ver params.example.json)"
    fi

    # Inyectar imagen si fue buildeada en esta sesión
    if [ -n "${DOCKER_IMAGE:-}" ]; then
        info "Actualizando DockerImage en parámetros: $DOCKER_IMAGE"
        PARAMS=$(cat "$PARAMS_FILE" | jq --arg img "$DOCKER_IMAGE" \
            'map(if .ParameterKey == "DockerImage" then .ParameterValue = $img else . end)')
        PARAMS_ARG="$PARAMS"
    else
        PARAMS_ARG=$(cat "$PARAMS_FILE")
    fi

    # Verificar si el stack existe
    STACK_STATUS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" \
        --query "Stacks[0].StackStatus" \
        --output text 2>/dev/null || echo "DOES_NOT_EXIST")

    if [ "$STACK_STATUS" = "DOES_NOT_EXIST" ]; then
        info "Creando stack nuevo..."
        aws cloudformation create-stack \
            --stack-name "$STACK_NAME" \
            --template-body file://deploy/aws/cloudformation.yml \
            --parameters "$PARAMS_ARG" \
            --capabilities CAPABILITY_NAMED_IAM \
            --region "$AWS_REGION"
        
        info "Esperando creación del stack (puede tardar 15-20 min)..."
        aws cloudformation wait stack-create-complete \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION"
    else
        info "Actualizando stack existente (status: $STACK_STATUS)..."
        aws cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body file://deploy/aws/cloudformation.yml \
            --parameters "$PARAMS_ARG" \
            --capabilities CAPABILITY_NAMED_IAM \
            --region "$AWS_REGION" || {
            
            # Verificar si no hay cambios (no es un error real)
            MSG=$(aws cloudformation describe-stack-events \
                --stack-name "$STACK_NAME" \
                --region "$AWS_REGION" \
                --query "StackEvents[0].ResourceStatusReason" \
                --output text 2>/dev/null || echo "")
            
            if [[ "$MSG" == *"No updates"* ]]; then
                info "Stack sin cambios de infraestructura."
            else
                error "Error actualizando stack: $MSG"
            fi
        }

        if aws cloudformation wait stack-update-complete \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION" 2>/dev/null; then
            info "Stack actualizado correctamente."
        fi
    fi

    # ── Outputs ──────────────────────────────────────────────────
    step "Outputs del Stack"
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" \
        --query "Stacks[0].Outputs" \
        --output table
fi

# ══════════════════════════════════════════════════════════════
# FASE 4: Forzar nueva deployment en ECS (rolling update)
# ══════════════════════════════════════════════════════════════
if [[ "$MODE" == "full" || "$MODE" == "--stack-only" ]]; then
    step "FASE 4: Rolling deploy ECS"

    CLUSTER=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" \
        --output text 2>/dev/null || echo "${APP_NAME}-cluster")

    SERVICE=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='ServiceName'].OutputValue" \
        --output text 2>/dev/null || echo "${APP_NAME}-service")

    info "Forzando nueva deployment en ${CLUSTER}/${SERVICE}..."
    aws ecs update-service \
        --cluster "$CLUSTER" \
        --service "$SERVICE" \
        --force-new-deployment \
        --region "$AWS_REGION" > /dev/null

    info "Esperando deployment estable (max 5 min)..."
    aws ecs wait services-stable \
        --cluster "$CLUSTER" \
        --services "$SERVICE" \
        --region "$AWS_REGION"

    info "✅ Deployment estable."
fi

# ══════════════════════════════════════════════════════════════
# ROLLBACK
# ══════════════════════════════════════════════════════════════
if [[ "$MODE" == "--rollback" ]]; then
    step "ROLLBACK — Revirtiendo a revisión anterior"
    
    CLUSTER="${APP_NAME}-cluster"
    SERVICE="${APP_NAME}-service"
    
    # Obtener task definition anterior
    PREV_TD=$(aws ecs describe-services \
        --cluster "$CLUSTER" \
        --services "$SERVICE" \
        --region "$AWS_REGION" \
        --query "services[0].deployments[1].taskDefinition" \
        --output text 2>/dev/null || echo "")

    if [ -z "$PREV_TD" ] || [ "$PREV_TD" = "None" ]; then
        error "No hay deployment anterior para rollback"
    fi

    warn "Revirtiendo a: $PREV_TD"
    aws ecs update-service \
        --cluster "$CLUSTER" \
        --service "$SERVICE" \
        --task-definition "$PREV_TD" \
        --region "$AWS_REGION" > /dev/null

    aws ecs wait services-stable \
        --cluster "$CLUSTER" \
        --services "$SERVICE" \
        --region "$AWS_REGION"

    info "✅ Rollback completado."
fi

# ── Resumen final ──────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║          Deploy completado exitosamente           ║"
echo "╚═══════════════════════════════════════════════════╝"

DASHBOARD_URL=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" \
    --output text 2>/dev/null || echo "Ver outputs del stack")

echo ""
echo "  Dashboard: $DASHBOARD_URL"
echo "  Logs:      aws logs tail /ecs/${APP_NAME} --follow"
echo ""
