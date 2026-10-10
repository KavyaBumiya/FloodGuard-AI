# FLOODGUARD AI

FLOODGUARD AI is an IoT-powered flood monitoring and prediction system for the
Mithi River corridor in Mumbai. It combines ESP32 water-level telemetry,
weather data, AI forecasts, geospatial flood estimates, alerts, and an
interactive map and digital twin.

Forecasts and mapped inundation are estimates for situational awareness, not
official flood warnings or validated hydraulic-model results. The interface
identifies data provenance and distinguishes live, historical, and simulated
information.

## Features

- Ingest water-level readings from IoT devices over an authenticated HTTP API.
- Monitor sensor readings, risk levels, forecasts, and alerts.
- Explore flood-related layers and affected features on a 2D map and 3D view.
- Run and compare what-if scenarios.
- Receive live updates over Socket.IO, with REST endpoints for application data.
- Check service health and explore the API through FastAPI's interactive docs.

## Technology

- **Frontend:** Next.js, React, TypeScript
- **Backend:** Python, FastAPI, SQLAlchemy, Alembic
- **Database and event services:** PostgreSQL with PostGIS, Redis
- **Geospatial and ML:** Rasterio, Shapely, NumPy, SciPy, scikit-learn
- **Local orchestration:** Docker Compose

## Run locally with Docker

Prerequisites: Docker Desktop with Compose support, Node.js (20.9 or later),
and npm.

From the repository root, start the backend and its database services:

```powershell
docker compose up --build
```

The API is available at <http://localhost:8000>. Open
<http://localhost:8000/docs> for the interactive API reference, or
<http://localhost:8000/healthz> to check that the API is running.

In a second terminal, start the frontend:

```powershell
cd Frontend
npm ci
npm run dev
```

Open <http://localhost:3000>. The development frontend is configured to use
the local API by default. To stop the services, press `Ctrl+C` in the Compose
terminal. Run `docker compose down` to stop and remove the containers while
preserving database volumes.

## Run the backend without Docker

You will need Python 3.11 or later, PostgreSQL with PostGIS, and Redis.

1. Start PostgreSQL/PostGIS and Redis. For local development, you can start
   just these services from the repository root with:

   ```powershell
   docker compose up -d db redis
   ```

2. Create and activate a virtual environment in `backend`, install the
   dependencies, and copy the example environment file:

   ```powershell
   cd backend
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Check the local database and Redis URLs in `backend/.env`, then apply
   migrations and start the API:

   ```powershell
   python -m scripts.release
   uvicorn app.main:app --reload
   ```

The API will be available at <http://localhost:8000>. Keep real credentials
and device tokens in local environment files; do not commit them.

## Configuration

- Backend settings are read from environment variables and local `.env` files.
  See [`backend/.env.example`](backend/.env.example) for available options.
- Frontend API settings use `NEXT_PUBLIC_API_BASE`. See
  [`Frontend/.env.example`](Frontend/.env.example); the development default
  points to `http://localhost:8000`.
- Shared study-area and risk settings live in [`config/`](config/).
- Forecast model files are stored in [`models/`](models/).
- Geospatial and rainfall inputs are stored in [`data/`](data/). Some inputs
  may need to be prepared or downloaded separately for the corresponding
  features.

## Tests and checks

Run the backend tests from `backend`:

```powershell
pytest
```

Run frontend tests and static checks from `Frontend`:

```powershell
npm test
npm run lint
npm run typecheck
```

## Project layout

```text
backend/       FastAPI application, database migrations, scripts, and tests
config/        Shared application configuration
data/          Rainfall, terrain, map, and other local data inputs
Frontend/      Next.js web application
infra/         Deployment and infrastructure resources
models/        Flood forecasting model files and metadata
```

For additional system design details, see
[`System-Architecture.md`](System-Architecture.md).
