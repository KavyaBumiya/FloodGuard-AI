<#
.SYNOPSIS
    Starts the Cloudflare Tunnel that exposes the Dockerised API to the internet.

.DESCRIPTION
    A Vercel-hosted frontend runs on the public internet, but this API lives on
    your private LAN address. There is no route between them. A tunnel fixes
    that by opening an outbound connection from your PC, so no router
    configuration or inbound port is needed.

    This script starts the tunnel, waits for Cloudflare to hand out a URL, then
    prints the exact Vercel environment variable to set.

.PARAMETER Stop
    Stop the tunnel instead of starting it.

.EXAMPLE
    .\scripts\start-tunnel.ps1
    .\scripts\start-tunnel.ps1 -Stop
#>
[CmdletBinding()]
param([switch]$Stop)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if ($Stop) {
    Write-Host "`nStopping tunnel..." -ForegroundColor Cyan
    docker compose --profile tunnel stop tunnel
    docker compose --profile tunnel rm -f tunnel
    Write-Host "Tunnel stopped.`n" -ForegroundColor Green
    exit 0
}

Write-Host "`nStarting FLOODGUARD Cloudflare Tunnel..." -ForegroundColor Cyan
Write-Host "Waiting for the API to be healthy first...`n"

docker compose --profile tunnel up -d tunnel

Write-Host "`nWaiting for Cloudflare to issue a public URL (this takes ~15 s)...`n" -ForegroundColor Cyan

$url = $null
for ($i = 1; $i -le 24 -and -not $url; $i++) {
    Start-Sleep -Seconds 5
    $logs = docker compose logs tunnel --no-log-prefix 2>&1 | Out-String
    $m = [regex]::Match($logs, 'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($m.Success) { $url = $m.Value }
}

if (-not $url) {
    Write-Host "Could not find a trycloudflare URL in the logs." -ForegroundColor Red
    Write-Host "Inspect them yourself with:  docker compose logs tunnel`n" -ForegroundColor Yellow
    exit 1
}

Write-Host "Tunnel is live." -ForegroundColor Green
Write-Host "  $url`n" -ForegroundColor Cyan

# Prove it end-to-end from outside, not just from the container network.
Write-Host "Verifying the public URL answers..." -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest "$url/healthz" -UseBasicParsing -TimeoutSec 25
    Write-Host "  GET /healthz -> $($r.StatusCode) $($r.Content)`n" -ForegroundColor Green
}
catch {
    Write-Host "  /healthz did not answer: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  The tunnel may still be negotiating; try again in 20 s.`n" -ForegroundColor Yellow
}

Write-Host "===============================================================" -ForegroundColor DarkCyan
Write-Host " Set this in Vercel:  Project -> Settings -> Environment Variables" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Name : NEXT_PUBLIC_API_BASE"
Write-Host "  Value: $url"
Write-Host ""
Write-Host "  Apply to: Production, Preview AND Development, then redeploy."
Write-Host "  NEXT_PUBLIC_ values are inlined at BUILD time, so an env change alone"
Write-Host "  does nothing until the project is rebuilt (Deployments -> redeploy)."
Write-Host ""
Write-Host "  Also set on the API side, then restart it:"
Write-Host '    docker compose stop backend && docker compose start backend'
Write-Host "  with this in a root .env:"
Write-Host "    FRONTEND_ORIGIN=https://your-app.vercel.app"
Write-Host ""
Write-Host "  Test with:  curl.exe $url/health" -ForegroundColor DarkGray
Write-Host "  Stop with:  .\scripts\start-tunnel.ps1 -Stop`n" -ForegroundColor DarkCyan

if ($url -match 'trycloudflare') {
    Write-Host "  NOTE: a quick-tunnel URL is RANDOM and changes every restart." -ForegroundColor Yellow
    Write-Host "  That means a Vercel redeploy every time you restart the tunnel." -ForegroundColor Yellow
    Write-Host "  For a stable URL, create a named tunnel and set CLOUDFLARE_TUNNEL_CMD" -ForegroundColor Yellow
    Write-Host "  + TUNNEL_TOKEN in a root .env - see docs/16-DEPLOYMENT.md." -ForegroundColor Yellow
    Write-Host ""
}