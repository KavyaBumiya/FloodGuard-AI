@description('Resource group location')
param location string = 'centralindia'

@description('Unique name prefix for resources')
param namePrefix string = 'floodguard'

@description('PostgreSQL admin username')
param pgAdminUsername string = 'floodguard'

@description('PostgreSQL admin password')
@secure()
param pgAdminPassword string

@description('Redis password')
@secure()
param redisPassword string

@description('JWT secret for authentication')
@secure()
param jwtSecret string

@description('IoT registration token')
@secure()
param iotRegistrationToken string

@description('Frontend origin for CORS')
param frontendOrigin string = 'https://flood-guard-ai-theta.vercel.app'

// ─────────────────────────────────────────────────────────────────────────────
// Resource Names
// ─────────────────────────────────────────────────────────────────────────────
var acrName = '${namePrefix}acr${uniqueString(resourceGroup().id)}'
var pgServerName = '${namePrefix}-db'
var pgDbName = 'floodguard'
var redisName = '${namePrefix}-redis'
var containerAppEnvName = '${namePrefix}-env'
var containerAppName = '${namePrefix}-api'

// ─────────────────────────────────────────────────────────────────────────────
// Azure Container Registry
// ─────────────────────────────────────────────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// PostgreSQL Flexible Server with PostGIS
// ─────────────────────────────────────────────────────────────────────────────
resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: pgServerName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: pgAdminUsername
    administratorLoginPassword: pgAdminPassword
    version: '16'
    storage: {
      storageSizeGB: 32
    }
    highAvailability: {
      mode: 'Disabled'
    }
    createMode: 'Default'
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pgServer
  name: pgDbName
  properties: {
    charset: 'utf8'
    collation: 'en_US.utf8'
  }
}

resource pgFirewallRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pgServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Azure Cache for Redis
// ─────────────────────────────────────────────────────────────────────────────
resource redis 'Microsoft.Cache/Redis@2024-11-01' = {
  name: redisName
  location: location
  // The provider schema omits sku for this supported Redis API version.
  #disable-next-line BCP187
  sku: {
    name: 'Basic'
    family: 'C'
    capacity: 0
  }
  #disable-next-line BCP035
  properties: {
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      'maxmemory-policy': 'allkeys-lru'
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Container Apps Environment
// ─────────────────────────────────────────────────────────────────────────────
resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-11-02-preview' = {
  name: containerAppEnvName
  location: location
  properties: {
    // Log Analytics workspace would be added here in production
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Container App
// ─────────────────────────────────────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2023-11-02-preview' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        allowInsecure: false
        // Socket.IO falls back to HTTP long-polling when WebSocket upgrade is
        // unavailable. Keep a polling client on the same replica across
        // requests so reconnects do not receive "session ID unknown".
        stickySessions: {
          affinity: 'sticky'
        }
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      }
      registries: [
        {
          server: '${acrName}.azurecr.io'
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
        {
          name: 'db-password'
          value: pgAdminPassword
        }
        {
          name: 'redis-password'
          value: redisPassword
        }
        {
          name: 'jwt-secret'
          value: jwtSecret
        }
        {
          name: 'iot-reg-token'
          value: iotRegistrationToken
        }
      ]
    }
    template: {
      containers: [
        {
          name: containerAppName
          image: '${acrName}.azurecr.io/${containerAppName}:latest'
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-password'
              value: 'postgresql://${pgAdminUsername}:${pgAdminPassword}@${pgServerName}.postgres.database.azure.com:5432/${pgDbName}?sslmode=require'
            }
            {
              name: 'REDIS_URL'
              secretRef: 'redis-password'
              value: 'rediss://:${redisPassword}@${redisName}.redis.cache.windows.net:6380/0'
            }
            {
              name: 'JWT_SECRET'
              secretRef: 'jwt-secret'
            }
            {
              name: 'IOT_REGISTRATION_TOKEN'
              secretRef: 'iot-reg-token'
            }
            {
              name: 'APP_ENV'
              value: 'production'
            }
            {
              name: 'SOCKETIO_MESSAGE_QUEUE'
              value: 'true'
            }
            {
              name: 'FRONTEND_ORIGIN'
              value: frontendOrigin
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                // Matches the deployed value. The container runs one event loop,
                // so letting the platform queue far more requests than it can
                // serve just converts load into latency and dropped WebSockets.
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Outputs
// ─────────────────────────────────────────────────────────────────────────────
output acrLoginServer string = acr.properties.loginServer
output postgresHost string = pgServer.properties.fullyQualifiedDomainName
output redisHost string = redis.properties.hostName
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
