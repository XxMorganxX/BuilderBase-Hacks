# oracle-gateway

The HTTP service that ingests agent sessions into the Oracle log.
Contract and design: `../docs/PLAN.md`. Principles: `../docs/PRINCIPLES.md`.
Deploying it on the server: `../docs/SERVER-DEPLOYMENT.md`.

```
oracle_gateway/
  config.py    every env-derived setting, read once
  models.py    the canonical contract (PLAN section 4)
  db.py        asyncpg pool and every SQL statement
  auth.py      shared password, plus the header that says who you are
  ingest.py    the single write path
  main.py      routes only
  adapters/    vendor formats stop here
```

Local development:

```bash
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
export DATABASE_URL=postgresql://oracle:oracle@localhost:5432/oracle
.venv/bin/pytest -q
.venv/bin/uvicorn oracle_gateway.main:app --reload --port 8080

curl -H "Authorization: Bearer oracle" -H "X-Oracle-User: you@example.com" \
     http://localhost:8080/v1/me
```
