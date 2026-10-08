# FLOODGUARD AI — Azure Deployment Guide

This guide covers deploying the FLOODGUARD AI backend to **Azure Container Apps** with **Azure Database for PostgreSQL Flexible Server** (PostGIS) and **Azure Cache for Redis**.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AZURE CLOUD                                         │
│  ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐   │
│  │  Azure Container │    │  Azure Database for │    │  Azure Cache for │   │
│  │      Registry    │    │   PostgreSQL        │    │      Redis       │   │
│  │  (floodguardacr) │    │   Flexible Server   │    │  (floodguard-    │   │
│  └────────┬─────────┘    │   (PostGIS enabled) │    │   redis)         │   │
│           │              └──────────┬──────────┘    └────────┬─────────┘   │
│           │                         │                        │             │
│           ▼                         ▼                        ▼             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    AZURE CONTAINER APPS ENVIRONMENT                 │   │
│  │  ┌─────────────────────────────────────────────────────────────┐   │   │
│  │  │              CONTAINER APP: floodguard-api                  │   │   │
│  │  │  • 1-3 replicas (auto-scaling)                             │   │   │
│  │  │  • 1 CPU / 2 GiB memory                                     │   │   │
│  │  │  • Port 8000 (HTTP + WebSocket)                            │   │   │
│  │  │  • Health checks: /healthz, /health                        │   │   │
│  │  └─────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL CLIENTS                                    │
│  • Frontend (Next.js on Vercel/Static Web Apps)                             │
│  • IoT Devices (ESP32 via HTTPS)                                            │
│  • API Consumers                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **Azure Subscription** with Owner/Contributor access
2. **Azure CLI** installed and logged in (`az login`)
3. **Docker** installed locally (for testing)
4. **GitHub Repository** with Actions enabled
5. **Service Principal** for GitHub Actions:
   ```bash
   az ad sp create-for-rbac --name "floodguard-github" \
     --role contributor \
     --scopes /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/floodguard-rg \
     --sdk-auth
   ```

## Quick Start (Automated)

### Option 1: Bash Script (Simplest)

```bash
# Make executable and run
chmod +x infra/azure-setup.sh
./infra/azure-setup.sh
```

This creates all resources and outputs connection details. Save the secrets!

### Option 2: Bicep (Infrastructure as Code)

```bash
# Deploy with Azure CLI
az deployment group create \
  --resource-group floodguard-rg \
  --template-file infra/main.bicep \
  --parameters pgAdminPassword="<secure-password>" \
               redisPassword="<secure-password>" \
               jwtSecret="<secure-secret>" \
               iotRegistrationToken="<secure-token>" \
               frontendOrigin="https://your-frontend.vercel.app"
```

## GitHub Actions CI/CD Setup

### 1. Add Repository Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|--------|-------------|
| `AZURE_CREDENTIALS` | Output from `az ad sp create-for-rbac --sdk-auth` |
| `ACR_USERNAME` | ACR admin username (from `az acr credential show`) |
| `ACR_PASSWORD` | ACR admin password |
| `DB_PASSWORD` | PostgreSQL admin password |
| `REDIS_PASSWORD` | Redis access key |
| `JWT_SECRET` | 32-byte hex string (`openssl rand -hex 32`) |
| `IOT_REG_TOKEN` | 16-byte hex string (`openssl rand -hex 16`) |
| `FRONTEND_ORIGIN` | Your frontend URL (e.g., `https://floodguard.vercel.app`) |

### 2. Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `build-and-push.yml` | Push to main / PR | Build & push Docker image to ACR |
| `azure-deploy.yml` | Push to main | Deploy image to Container Apps |
| `infra-deploy.yml` | Manual | Deploy/update Azure infrastructure |

## Manual Deployment Steps

### 1. Build & Push Image

```bash
# Login to ACR
az acr login --name floodguardacr

# Build from repo root (context includes models/, data/, config/)
docker build -f backend/Dockerfile -t floodguardacr.azurecr.io/floodguard-api:latest .

# Push
docker push floodguardacr.azurecr.io/floodguard-api:latest
```

### 2. Run Database Migrations

```bash
# Option A: Via Container App console
az containerapp exec \
  --resource-group floodguard-rg \
  --name floodguard-api \
  --command "python -m scripts.release"

# Option B: Locally against Azure DB
export DATABASE_URL="postgresql://floodguard:<password>@<host>:5432/floodguard?sslmode=require"
export REDIS_URL="rediss://:<password>@<host>:6380/0"
cd backend && python -m scripts.release
```

### 3. Verify Deployment

```bash
# Health checks
curl https://<app-url>.azurecontainerapps.io/healthz
curl https://<app-url>.azurecontainerapps.io/health

# API root
curl https://<app-url>.azurecontainerapps.io/api

# WebSocket test (requires Socket.IO client)
```

## Environment Variables Reference

### Required for Production

| Variable | Source | Example |
|----------|--------|---------|
| `DATABASE_URL` | Azure PostgreSQL | `postgresql://user:pass@host:5432/db?sslmode=require` |
| `REDIS_URL` | Azure Redis | `rediss://:pass@host:6380/0` |
| `JWT_SECRET` | Generated | `openssl rand -hex 32` |
| `IOT_REGISTRATION_TOKEN` | Generated | `openssl rand -hex 16` |
| `FRONTEND_ORIGIN` | Your frontend | `https://app.example.com` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SOCKETIO_MESSAGE_QUEUE` | `true` | Enable Redis pub/sub for multi-replica WebSocket |
| `RELEASE_LOAD_OSM` | `true` | Load bundled OSM layers during deployment |
| `ALLOW_SYNTHETIC_TERRAIN` | `false` | Fail loudly if DEM missing |
| `APP_ENV` | `production` | Controls CORS, logging |
| `CESIUM_ION_TOKEN` | `` | For 3D terrain (optional) |

## Scaling Configuration

The Container App auto-scales based on HTTP concurrency:

```yaml
scale:
  minReplicas: 1
  maxReplicas: 3
  rules:
    - name: http-scaling
      http:
        metadata:
          concurrentRequests: "50"
```

Adjust based on load testing. For WebSocket-heavy workloads, consider:
- Higher `minReplicas` (2-3)
- Session affinity (sticky sessions) for Socket.IO

## Monitoring & Observability

### Health Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /healthz` | Liveness probe (no dependencies) |
| `GET /health` | Readiness probe (checks DB, Redis, Open-Meteo) |

### Logging

Structured JSON logs via `structlog`. Integrate with:
- **Azure Monitor / Log Analytics** (recommended)
- **Application Insights** (add `opentelemetry-instrument` to Dockerfile)

### Metrics

Key metrics to monitor:
- Container CPU/Memory
- Request latency (p50, p95, p99)
- Error rate (5xx)
- WebSocket connections
- Database connection pool usage
- Redis memory/hit rate

## Troubleshooting

### Container App Won't Start

```bash
# Check logs
az containerapp logs show --resource-group floodguard-rg --name floodguard-api --follow

# Common issues:
# 1. DATABASE_URL format - must be postgresql:// with sslmode=require
# 2. Redis TLS - must use rediss:// on port 6380
# 3. Missing PostGIS extension - run CREATE EXTENSION postgis
# 4. CORS - FRONTEND_ORIGIN must match exactly (no trailing slash)
```

### Database Connection Failed

```bash
# Test from local machine
psql "postgresql://floodguard:<pass>@<host>:5432/floodguard?sslmode=require" -c "SELECT 1;"

# Check firewall rules
az postgres flexible-server firewall-rule list --resource-group floodguard-rg --server-name floodguard-db
```

### WebSocket Not Working

1. Verify `SOCKETIO_MESSAGE_QUEUE=true` (required for multi-replica)
2. Check `FRONTEND_ORIGIN` matches exactly (protocol + domain + port)
3. Ensure ingress allows WebSocket upgrade headers
4. Test with `wscat -c wss://<app-url>/socket.io/?EIO=4&transport=websocket`

## Cost Optimization

| Resource | Tier | Est. Monthly (INR) |
|----------|------|-------------------|
| Container Registry | Basic | ~₹400 |
| PostgreSQL Flexible | B1ms (1 vCore, 2 GiB) | ~₹2,500 |
| Redis Cache | Basic C0 (250 MB) | ~₹1,200 |
| Container Apps | 1-3 replicas (1 CPU/2GiB) | ~₹3,000-9,000 |
| **Total** | | **~₹7,000-13,000/month** |

### Reduce Costs
- Use **Dev/Test** pricing for PostgreSQL
- Scale to 0 replicas during off-hours (Container Apps supports this)
- Use **Standard** tier Redis only if needed
- Enable **zone redundancy** only for production

## Security Checklist

- [ ] Store all secrets in **Azure Key Vault** (not in code/env files)
- [ ] Enable **Private Endpoints** for PostgreSQL and Redis
- [ ] Configure **VNet integration** for Container Apps
- [ ] Enable **Microsoft Defender for Cloud** on subscription
- [ ] Set up **Diagnostic Settings** to Log Analytics
- [ ] Configure **WAF** (Web Application Firewall) if using Front Door
- [ ] Rotate secrets regularly (Key Vault rotation policies)
- [ ] Enable **Azure AD authentication** for PostgreSQL (managed identity)

## Rollback Procedure

```bash
# List revisions
az containerapp revision list --resource-group floodguard-rg --name floodguard-api -o table

# Rollback to previous revision
az containerapp revision activate \
  --resource-group floodguard-rg \
  --name floodguard-api \
  --revision <previous-revision-name>
```

## Support

- **Azure Documentation**: https://learn.microsoft.com/azure/container-apps/
- **PostgreSQL Flexible Server**: https://learn.microsoft.com/azure/postgresql/flexible-server/
- **Azure Cache for Redis**: https://learn.microsoft.com/azure/azure-cache-for-redis/
- **GitHub Actions for Azure**: https://github.com/Azure/actions