# FLOODGUARD AI — production image
#
# Build context is the REPOSITORY ROOT, not backend/:
#     docker build -f backend/Dockerfile -t floodguard-api .
# The context must include data/ and models/ because the app resolves every
# dataset path against the repo root (Settings.resolve_data_path), not the CWD.
#
# Render runs this automatically when `dockerContext: .` is set in render.yaml.
#
# Python 3.13 rather than 3.14: requirements.txt records 3.14 as "verified
# working" locally, but rasterio / scipy / shapely / netCDF4 binary wheels are
# not published for 3.14 on every platform. 3.13 has a wheel for every one of
# them, so the image builds without a multi-minute source compile. Nothing in
# the code is 3.14-specific.
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libraries, all discovered by actually booting the image:
#   libpq5      asyncpg
#   libexpat1   the GDAL bundled inside the rasterio wheel imports libexpat.so.1
#               at `import rasterio`; debian-slim does not ship it, and the
#               failure only appears once flood_simulation is imported
#   libgomp1    OpenMP runtime required by GDAL and by scipy's BLAS
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 libexpat1 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Dependencies first: this layer is cached until requirements.txt changes, which
# matters when the build context carries ~100 MB of models and GeoJSON.
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Application + everything resolve_data_path() can reach. Layout matters:
#   /app/backend/app/core/config.py  -> parents[2] = /app/backend  (_BACKEND_ROOT)
#   /app                              = repo root (_REPO_ROOT)
# so data/, models/ and config/ MUST sit beside backend/ exactly as they do
# locally, or every dataset lookup silently resolves to a missing file and the
# flood engine refuses to run.
#
# --chown on every COPY rather than a trailing `chown -R /app`: the recursive
# chown wrote a second copy of the 94 MB of model artefacts into its own layer,
# costing ~116 MB of the image for nothing. The non-root USER below is created
# first so it has a uid to chown to.
RUN useradd --create-home --uid 10001 floodguard

COPY --chown=floodguard:floodguard backend/ /app/backend/
COPY --chown=floodguard:floodguard config/ /app/config/
COPY --chown=floodguard:floodguard data/osm/ /app/data/osm/
COPY --chown=floodguard:floodguard data/terrain/ /app/data/terrain/
COPY --chown=floodguard:floodguard data/rainfall/ /app/data/rainfall/
COPY --chown=floodguard:floodguard models/flood/ /app/models/flood/

USER floodguard

ENV PORT=8000 \
    APP_ENV=production \
    PYTHONPATH=/app/backend

EXPOSE 8000

# Liveness probe. Kept dependency-free on purpose — no curl in the image — and
# it only calls /healthz, which touches no database and no third party. The
# full /health (DB + Redis + Open-Meteo) is a diagnostic, not a gate.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys;\
url=f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/healthz\";\
sys.exit(0 if urllib.request.urlopen(url,timeout=4).status==200 else 1)"]

# Single worker on purpose.
#
# The async engines (auto-forecast every 60 s, alert engine every 30 s) are
# per-process and are NOT idempotent across workers: four workers would write
# four forecast rows per cycle and raise duplicate alerts.
# Socket.IO fan-out across workers is already handled by the Redis message queue
# configured in app/services/websocket.py, so raising this number is safe for
# *traffic* - but leave it at 1 unless you also move the engines to a Render
# Background Worker.
#
# The per-device telemetry rate limit in app/services/rate_limit.py is also
# per-process, so it is exact only at one worker. Move it to Redis first.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]