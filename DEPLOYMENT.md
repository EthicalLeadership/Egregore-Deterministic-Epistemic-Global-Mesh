# Egregore Deployment Guide

## Local development stack (npm-first)

This repository is orchestrated with npm. It assumes Postgres, Redis, and NATS are already installed and running on their standard localhost ports (`5432`, `6379`, `4222`).

```bash
# 1. Copy environment template and adjust as needed
cp .env.example .env

# 2. Install Node dependencies and bootstrap the Python virtual environment
npm install

# 3. Verify that Postgres, Redis, and NATS are reachable
npm run services:check

# 4. Start the gateway (3000), Python core (8002), and Vite dashboard (5173)
npm run dev
```

With the stack running:

| Endpoint | URL | Notes |
|----------|-----|-------|
| Gateway | http://localhost:3000 | Proxies `/api/*` to the Python core |
| Core (direct) | http://localhost:8002 | FastAPI/uvicorn |
| Dashboard | http://localhost:5173 | Vite React dev server |
| Gateway health | http://localhost:3000/health | Gateway + core reachability |
| Core readiness | http://localhost:8002/ready | Checks DB, Redis, and NATS |

Run a quick end-to-end smoke test:

```bash
npm run smoke
```

### Running tests

```bash
npm test
```

This runs the Python test suite through the project-managed virtual environment (`.venv`).

### Full verification gate

```bash
./scripts/cleanup_artifacts.sh
./scripts/verify_health.sh
```

## Docker (alternative)

A Docker Compose stack is also available and uses non-standard host ports by default so it does not collide with the npm workflow's assumption of standard localhost ports.

```bash
# 1. Generate local secrets (example only — use a real HSM/vault in production)
mkdir -p secrets
openssl rand -hex 32 > secrets/signing_key.pem
openssl rand -out secrets/cluster_kek.bin 32

# 2. Build and start the full stack
docker compose up -d --wait

# 3. Check liveness and readiness
curl http://localhost:18000/health
curl http://localhost:18000/ready

# 4. Smoke test against the compose stack
GATEWAY_URL=http://localhost:18000 CORE_URL=http://localhost:18000 \
  WORKFLOW_PATH=/workflows/test-health ./scripts/smoke_test.sh
```

### Port configuration

`docker-compose.yml` publishes services on non-standard host ports by default. Override any port in `.env` without editing the compose file:

| Service | Env variable | Default host port | Container port |
|---------|--------------|-------------------|----------------|
| HTTP API | `API_HOST_PORT` | `18000` | `8000` |
| Prometheus metrics | `METRICS_HOST_PORT` | `19000` | `9000` |
| Postgres | `POSTGRES_HOST_PORT` | `15432` | `5432` |
| Redis | `REDIS_HOST_PORT` | `16379` | `6379` |
| NATS client | `NATS_CLIENT_HOST_PORT` | `14222` | `4222` |
| NATS monitoring | `NATS_MONITOR_HOST_PORT` | `18222` | `8222` |

Inside the container the API always listens on `0.0.0.0:8000`, and service-to-service URLs (e.g., `BLACKSTAR_DB_URL`) use the Docker service names (`postgres`, `redis`, `nats`).

## Notes

- `tests/test_canon_yaml_schema.py` requires an external fixture file that is not present in this repo; it is excluded from the automated verification gate.
- `/health` is a lightweight liveness probe.
- `/ready` is a readiness probe that actually connects to Postgres, Redis, and NATS. The container health check uses `/ready`, and `docker compose up -d --wait` will not consider the API healthy until all dependencies are reachable.
