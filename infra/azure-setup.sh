#!/bin/bash
# FLOODGUARD AI — Azure Infrastructure Setup Script
# Run this script to create all required Azure resources for deployment

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — EDIT THESE VALUES
# ─────────────────────────────────────────────────────────────────────────────
RESOURCE_GROUP="floodguard-rg"
LOCATION="centralindia"  # Closest to Mumbai
ACR_NAME="floodguardacr"
POSTGRES_SERVER="floodguard-db"
POSTGRES_DB="floodguard"
POSTGRES_ADMIN="floodguard"
REDIS_NAME="floodguard-redis"
CONTAINER_APP_ENV="floodguard-env"
CONTAINER_APP_NAME="floodguard-api"
CONTAINER_APP_PUBLIC="true"

# Generate secure passwords (or provide your own)
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)}"
REDIS_PASSWORD="${REDIS_PASSWORD:-$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)}"
JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 32)}"
IOT_REG_TOKEN="${IOT_REG_TOKEN:-$(openssl rand -hex 16)}"

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────
log() { echo -e "\033[1;32m[$(date +'%H:%M:%S')]\033[0m $*"; }
warn() { echo -e "\033[1;33m[$(date +'%H:%M:%S')] WARNING:\033[0m $*"; }
error() { echo -e "\033[1;31m[$(date +'%H:%M:%S')] ERROR:\033[0m $*" >&2; exit 1; }

check_az() {
    if ! command -v az &> /dev/null; then
        error "Azure CLI not found. Install from https://aka.ms/installazurecli"
    fi
    if ! az account show &> /dev/null; then
        error "Not logged in to Azure. Run 'az login' first."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Main Setup
# ─────────────────────────────────────────────────────────────────────────────
check_az

log "Creating resource group: $RESOURCE_GROUP"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

# ─────────────────────────────────────────────────────────────────────────────
# Azure Container Registry
# ─────────────────────────────────────────────────────────────────────────────
log "Creating Azure Container Registry: $ACR_NAME"
az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled true \
    --output none

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query passwords[0].value -o tsv)

# ─────────────────────────────────────────────────────────────────────────────
# Azure Database for PostgreSQL Flexible Server (with PostGIS)
# ─────────────────────────────────────────────────────────────────────────────
log "Creating PostgreSQL Flexible Server: $POSTGRES_SERVER"
az postgres flexible-server create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$POSTGRES_SERVER" \
    --location "$LOCATION" \
    --admin-user "$POSTGRES_ADMIN" \
    --admin-password "$DB_PASSWORD" \
    --sku-name Standard_B1ms \
    --tier Burstable \
    --version 16 \
    --storage-size 32 \
    --high-availability Disabled \
    --vnet "$RESOURCE_GROUP-vnet" \
    --subnet "postgres-subnet" \
    --private-dns-zone "$POSTGRES_SERVER.privatelink.postgres.database.azure.com" \
    --output none

log "Enabling PostGIS extension"
az postgres flexible-server parameter set \
    --resource-group "$RESOURCE_GROUP" \
    --server-name "$POSTGRES_SERVER" \
    --name "azure.extensions" \
    --value "postgis" \
    --output none

# Create database
log "Creating database: $POSTGRES_DB"
az postgres flexible-server db create \
    --resource-group "$RESOURCE_GROUP" \
    --server-name "$POSTGRES_SERVER" \
    --database-name "$POSTGRES_DB" \
    --output none

# Configure firewall for Azure services
az postgres flexible-server firewall-rule create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$POSTGRES_SERVER" \
    --rule-name "AllowAzureServices" \
    --start-ip-address 0.0.0.0 \
    --end-ip-address 0.0.0.0 \
    --output none

POSTGRES_HOST=$(az postgres flexible-server show --resource-group "$RESOURCE_GROUP" --name "$POSTGRES_SERVER" --query fullyQualifiedDomainName -o tsv)

# ─────────────────────────────────────────────────────────────────────────────
# Azure Cache for Redis
# ─────────────────────────────────────────────────────────────────────────────
log "Creating Azure Cache for Redis: $REDIS_NAME"
az redis create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$REDIS_NAME" \
    --location "$LOCATION" \
    --sku Basic \
    --vm-size C0 \
    --enable-non-ssl-port false \
    --minimum-tls-version "1.2" \
    --output none

REDIS_HOST=$(az redis show --resource-group "$RESOURCE_GROUP" --name "$REDIS_NAME" --query hostName -o tsv)

# ─────────────────────────────────────────────────────────────────────────────
# Azure Container Apps Environment
# ─────────────────────────────────────────────────────────────────────────────
log "Creating Container Apps Environment: $CONTAINER_APP_ENV"
az containerapp env create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP_ENV" \
    --location "$LOCATION" \
    --output none

# ─────────────────────────────────────────────────────────────────────────────
# Azure Container App
# ─────────────────────────────────────────────────────────────────────────────
log "Creating Container App: $CONTAINER_APP_NAME"

# Build the image first
log "Building and pushing Docker image..."
az acr build \
    --registry "$ACR_NAME" \
    --image "$CONTAINER_APP_NAME:latest" \
    --file backend/Dockerfile \
    .

IMAGE_URI="$ACR_LOGIN_SERVER/$CONTAINER_APP_NAME:latest"

# Create the container app
az containerapp create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP_NAME" \
    --environment "$CONTAINER_APP_ENV" \
    --image "$IMAGE_URI" \
    --target-port 8000 \
    --ingress external \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --min-replicas 1 \
    --max-replicas 3 \
    --cpu 1.0 \
    --memory 2Gi \
    --env-vars \
        DATABASE_URL="postgresql://$POSTGRES_ADMIN:$DB_PASSWORD@$POSTGRES_HOST:5432/$POSTGRES_DB?sslmode=require" \
        REDIS_URL="rediss://:$REDIS_PASSWORD@$REDIS_HOST:6380/0" \
        JWT_SECRET="$JWT_SECRET" \
        IOT_REGISTRATION_TOKEN="$IOT_REG_TOKEN" \
        APP_ENV="production" \
        SOCKETIO_MESSAGE_QUEUE="true" \
        FRONTEND_ORIGIN="https://$CONTAINER_APP_NAME.azurewebsites.net" \
    --output none

# ─────────────────────────────────────────────────────────────────────────────
# Output Connection Information
# ─────────────────────────────────────────────────────────────────────────────
APP_URL=$(az containerapp show --resource-group "$RESOURCE_GROUP" --name "$CONTAINER_APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)

log "✅ Azure infrastructure created successfully!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "RESOURCE SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Resource Group:        $RESOURCE_GROUP"
echo "Location:              $LOCATION"
echo ""
echo "Container Registry:    $ACR_LOGIN_SERVER"
echo "  Username:            $ACR_USERNAME"
echo "  Password:            $ACR_PASSWORD"
echo ""
echo "PostgreSQL Server:     $POSTGRES_SERVER"
echo "  Host:                $POSTGRES_HOST"
echo "  Database:            $POSTGRES_DB"
echo "  Admin User:          $POSTGRES_ADMIN"
echo "  Password:            $DB_PASSWORD"
echo ""
echo "Redis Cache:           $REDIS_NAME"
echo "  Host:                $REDIS_HOST"
echo "  Password:            $REDIS_PASSWORD"
echo "  Port:                6380 (TLS)"
echo ""
echo "Container App:         $CONTAINER_APP_NAME"
echo "  URL:                 https://$APP_URL"
echo "  Environment:         $CONTAINER_APP_ENV"
echo ""
echo "Application Secrets:"
echo "  JWT_SECRET:          $JWT_SECRET"
echo "  IOT_REG_TOKEN:       $IOT_REG_TOKEN"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "NEXT STEPS:"
echo "1. Save the secrets above securely (Key Vault recommended)"
echo "2. Configure GitHub secrets for CI/CD:"
echo "   - AZURE_CREDENTIALS (from 'az ad sp create-for-rbac')"
echo "   - ACR_USERNAME: $ACR_USERNAME"
echo "   - ACR_PASSWORD: $ACR_PASSWORD"
echo "3. Update FRONTEND_ORIGIN in Container App with your actual frontend URL"
echo "4. Run database migrations: az containerapp exec --resource-group $RESOURCE_GROUP --name $CONTAINER_APP_NAME --command 'python -m scripts.release'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"