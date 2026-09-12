# Oracle server deployment runbook

**Audience:** an agent (or engineer) working **on the server machine** that will host Oracle.
**Goal:** a running Postgres and gateway that laptops on the network can post agent sessions to.
**Time:** about 15 minutes on a machine that already has Docker.

You are deploying phase 1 only: the database and the ingestion gateway. The Oracle mediator agent is phase 2 and is not part of this runbook. Background on what any of this is for: `docs/PRINCIPLES.md`. The full spec: `docs/PLAN.md`.

Work through the numbered steps in order. Each one ends with a check and the output you should see. **If a check fails, stop and read the matching row in section 9 rather than improvising.** At the end, section 8 lists exactly what to report back.

---

## 0. What you are installing

Two containers, defined in `db/docker-compose.yml`:

| Service | Image | Listens | Purpose |
|---|---|---|---|
| `db` | `postgres:16` | 5432 | The append-only session log. Data lives in the named volume `oracle_pgdata`. |
| `gateway` | built from `gateway/` | 8080 | HTTP API. Authenticates laptops, converts vendor session formats, writes to the log. |

Laptops talk only to the gateway. Nothing outside this machine needs to reach Postgres.

---

## 1. Check the prerequisites

```bash
docker --version            # 24+ expected
docker compose version      # v2; if this fails see 9.A
df -h /var/lib/docker        # want at least 5 GB free
```

Sessions are verbose. A busy engineer's laptop ships roughly 100-200 MB of JSON a week, and every event keeps its original payload on purpose (principle P3). 5 GB is comfortable for a hackathon; plan properly before a real deployment.

Note the machine's LAN address, you will need it repeatedly and will report it at the end:

```bash
hostname -I 2>/dev/null | awk '{print $1}' || ipconfig getifaddr en0
```

**Check:** you have a Docker version, a compose version, free space, and an IP address written down.

---

## 2. Get the code onto this machine

This project lives in the `oracle-gateway/` directory of a hackathon monorepo. Clone it on the server:

```bash
git clone https://github.com/XxMorganxX/BuilderBase-Hacks.git ~/builderbase
cd ~/builderbase/oracle-gateway
```

Everything below is relative to that directory, written here as `~/builderbase/oracle-gateway`.

The sibling `Oracle/` directory in that repo is a different component by another author, the delivery channel that will eventually carry Oracle's messages back into a live agent session. You do not need it for this deployment; ignore it.

If the server has no network access to GitHub, copy the directory from the laptop instead. **Run this from the laptop**, not from the server:

```bash
rsync -av --delete \
  --exclude .git --exclude .venv --exclude '.env' --exclude __pycache__ \
  ~/Development/hackathon_oracle/ USER@SERVER:~/builderbase/oracle-gateway/
```

**Check, on the server:**

```bash
cd ~/builderbase/oracle-gateway && ls
# expect: db  docs  fixtures  gateway  shippers  tasks  README.md
```

No `.env` should be present. It holds the laptop's own secrets, is gitignored, and is excluded from the rsync. You write a fresh one in the next step.

---

## 3. Write the configuration

Configuration lives in one file, `db/.env`, read by Docker Compose. It is gitignored and must never be committed.

```bash
cd ~/builderbase/oracle-gateway/db
cat > .env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
POSTGRES_PORT=5432
ORACLE_GATEWAY_PORT=8080
ORACLE_LOG_LEVEL=info
ORACLE_PASSWORD=oracle
EOF
chmod 600 .env
```

`ORACLE_PASSWORD` is the single password every laptop sends. `oracle` is the agreed value for this hackathon; change it here and tell everyone if you want something else. Unlike the Postgres password it is not generated, because every person has to type it.

`ORACLE_GATEWAY_PORT` is the port laptops will connect to. The container always listens on 8080 internally; change this value only if 8080 is already taken on the host. Same for `POSTGRES_PORT`: if something already uses 5432, set it to 5433 and remember it for step 5.

Every other setting has a working default. `.env.example` at the repo root lists the optional tuning knobs.

**Check:**

```bash
grep -c POSTGRES_PASSWORD .env    # expect 1
```

---

## 4. Start the stack

```bash
cd ~/builderbase/oracle-gateway/db
docker compose up -d --build
```

The first build pulls `python:3.12-slim` and `postgres:16` and takes a few minutes. When Postgres starts against an empty volume it applies `db/schema.sql` automatically.

```bash
docker compose ps
```

**Check:** both services are `running`, and `db` is `(healthy)`. If `gateway` is restarting, go to 9.C.

---

## 5. Confirm the schema

The automatic apply only happens on a fresh volume, so confirm it, and apply it by hand if the count is wrong. `schema.sql` is idempotent: running it again on an already-populated database is safe and changes nothing.

```bash
docker compose exec -T db psql -U oracle -d oracle -tAc \
  "select count(*) from pg_tables where schemaname='public'"
```

**Check:** prints `6` (`users`, `sessions`, `events`, `oracle_cursors`, `session_summaries`, `oracle_messages`).

If it prints anything else:

```bash
docker compose exec -T db psql -U oracle -d oracle < schema.sql
```

Then re-run the count. Three of those seven tables (`oracle_cursors`, `session_summaries`, `oracle_messages`) are empty by design. They belong to the phase-2 Oracle agent and exist now so that phase needs no migration.

---

## 6. Check the gateway is alive

```bash
curl -s http://localhost:8080/healthz
```

**Check:** exactly this, allowing for key order:

```json
{"ok":true,"db":true,"adapters":["claude_code","codex"]}
```

`"db":false` means the gateway is up but cannot reach Postgres: go to 9.D.

---

## 7. Understand who is who

There is nothing to run in this step. It is here because the next one will not make sense without it.

Authentication is one shared password, the `ORACLE_PASSWORD` you set in step 3. Every laptop sends the same one. There are no per-person keys to generate, distribute, or revoke.

Identity is separate. Each request carries an `X-Oracle-User` header saying whose session it is, and a user row appears the first time anyone ships under that identity. So a laptop needs three values and no provisioning:

| Value | Example | Who sets it |
|---|---|---|
| Gateway URL | `http://192.168.1.40:8080` | you, from step 1 |
| Password | `oracle` | you, from step 3 |
| Identity | `alice@example.com` | each person, once, and then never changes it |

The tradeoff, stated plainly so you can decide whether it is acceptable on your network: anyone holding the password can claim to be anyone. It suits a room of people at one hackathon. It does not suit anything else, which is the point of section 13.

## 8. Prove the whole path works

Run this acceptance script on the server. It posts a session, posts it again, and confirms the second attempt writes nothing. Idempotency is the property the laptop shippers depend on: they re-send freely and the server absorbs it (principle P4).

```bash
export G=http://localhost:8080
export T=oracle                       # whatever you set as ORACLE_PASSWORD
export U=deploy-check@example.com     # any identity; this one is a throwaway

cat > /tmp/batch.json <<'JSON'
{"session":{"external_id":"deploy-check-1","agent_kind":"custom","workspace":"/tmp/demo"},
 "events":[{"external_id":"e1","type":"user_message","role":"user",
            "content":[{"type":"text","text":"hello oracle"}],
            "occurred_at":"2026-01-01T00:00:00Z"}]}
JSON

echo "first  :" $(curl -s -H "Authorization: Bearer $T" -H "X-Oracle-User: $U" -H 'Content-Type: application/json' --data @/tmp/batch.json $G/v1/ingest)
echo "second :" $(curl -s -H "Authorization: Bearer $T" -H "X-Oracle-User: $U" -H 'Content-Type: application/json' --data @/tmp/batch.json $G/v1/ingest)
echo "feed   :" $(curl -s -H "Authorization: Bearer $T" -H "X-Oracle-User: $U" "$G/v1/events?after_id=0&limit=5")
```

**Check, and this is the acceptance test for the whole deployment:**

- first: `"inserted":1,"duplicates":0`
- second: `"inserted":0,"duplicates":1` and the **same** `session_id`
- feed: one event whose `content_text` is `hello oracle`

Then remove the probe so it does not clutter the demo:

```bash
docker compose exec -T db psql -U oracle -d oracle -c \
  "delete from sessions where external_session_id = 'deploy-check-1';
   delete from users where email = 'deploy-check@example.com';"
```

### Now let a laptop reach it

On a laptop, with `SERVER` set to the address from step 1:

```bash
curl -s http://SERVER:8080/healthz
```

If that hangs or refuses, the port is not open to the network. Open it:

```bash
# ufw (Debian/Ubuntu)
sudo ufw allow 8080/tcp

# firewalld (RHEL/Fedora)
sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload
```

Do **not** open 5432 to the network. Nothing outside this machine needs Postgres.

Then ship a real session from the laptop and watch it land:

```bash
# on the laptop
export T=oracle
export U=your.name@example.com
f=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
curl -s -H "Authorization: Bearer $T" -H "X-Oracle-User: $U" \
     -H 'Content-Type: application/x-ndjson' \
     --data-binary @"$f" http://SERVER:8080/v1/ingest/raw/claude_code
curl -s -H "Authorization: Bearer $T" -H "X-Oracle-User: $U" "http://SERVER:8080/v1/sessions?limit=5"
```

A healthy response reports a large `inserted` and a large `skipped`. Skipped is not an error: a session file is mostly UI chrome, and only conversation lines are stored.

---

## 9. Troubleshooting

| # | Symptom | Cause and fix |
|---|---|---|
| A | `docker compose` is not a command | Old Docker with the standalone binary. Use `docker-compose` throughout, or install the compose plugin. If Docker is absent entirely, use section 10. |
| B | `POSTGRES_PASSWORD variable is not set` | You are not in `~/builderbase/oracle-gateway/db`, or `.env` was not written. Re-do step 3 in that directory. |
| C | `gateway` restarts in a loop | `docker compose logs gateway`. A traceback ending in `ConnectionRefusedError` means Postgres is not up yet: wait and re-check. A `ModuleNotFoundError` means a partial build: `docker compose build --no-cache gateway`. |
| D | `/healthz` returns `"db":false` | Password mismatch between the two services. This happens if you changed `POSTGRES_PASSWORD` after the volume was created: the database keeps the original. Either restore the old password in `.env`, or wipe and start over with 9.H. |
| E | `port is already allocated` | Something else uses 8080 or 5432. Change `ORACLE_GATEWAY_PORT` or `POSTGRES_PORT` in `.env`, then `docker compose up -d`. |
| F | `401 wrong password` | The laptop is not sending the `ORACLE_PASSWORD` from step 3. Check for a stale value, and note the header is `Authorization: Bearer <password>`. |
| G | `400 no adapter for agent_kind '...'` | That vendor has no adapter yet. Known kinds are in the `/healthz` output. Agents that convert their own format can POST canonical JSON to `/v1/ingest` instead. |
| H | You want to start completely over | `docker compose down -v` **destroys all ingested sessions**, then `docker compose up -d --build` and redo steps 5 to 7. Never run this once the demo data matters. |
| J | Sessions all land under `unattributed@oracle.local` | The laptop is not sending `X-Oracle-User`. Harmless, but Oracle cannot route to a person until it does. |
| I | `413` from a laptop | One batch exceeded 10 MB or 1000 events. The shipper should send smaller batches; the limit is `ORACLE_MAX_BODY_BYTES` in `.env` if it genuinely needs raising. |

---

## 10. Fallback: no Docker on this machine

Only if section 1 showed no usable Docker.

```bash
# Postgres 16, Debian/Ubuntu
sudo apt update && sudo apt install -y postgresql-16 python3.12-venv
sudo -u postgres psql -c "CREATE USER oracle WITH PASSWORD 'CHOOSE_ONE';"
sudo -u postgres psql -c "CREATE DATABASE oracle OWNER oracle;"
psql "postgresql://oracle:CHOOSE_ONE@localhost:5432/oracle" -f ~/builderbase/oracle-gateway/db/schema.sql

# gateway
cd ~/builderbase/oracle-gateway/gateway
python3.12 -m venv .venv && .venv/bin/pip install .
export DATABASE_URL="postgresql://oracle:CHOOSE_ONE@localhost:5432/oracle"
export ORACLE_GATEWAY_PORT=8080
export ORACLE_PASSWORD=oracle
nohup .venv/bin/python -m oracle_gateway > ~/oracle-gateway.log 2>&1 &
```

Then continue from step 6, translating the container commands as you go: `docker compose exec -T gateway python -m X` becomes `.venv/bin/python -m X`, and `docker compose exec -T db psql -U oracle -d oracle` becomes `psql "$DATABASE_URL"`. For anything longer-lived than a hackathon, write a systemd unit instead of `nohup`.

---

## 11. Day-to-day operations

```bash
cd ~/builderbase/oracle-gateway/db

docker compose logs -f gateway          # follow the ingest log, one line per batch
docker compose restart gateway          # after a config change
docker compose up -d --build gateway    # after a code change
docker compose down                     # stop, keeping all data
```

Useful queries:

```bash
docker compose exec -T db psql -U oracle -d oracle -c \
  "select agent_kind, count(*) sessions, sum(event_count) events from sessions group by 1;"

docker compose exec -T db psql -U oracle -d oracle -c \
  "select u.email, s.title, s.event_count, s.last_event_at
     from sessions s join users u on u.id = s.user_id
    order by s.last_event_at desc nulls last limit 10;"
```

Back up before the demo, and again before anyone changes anything:

```bash
docker compose exec -T db pg_dump -U oracle oracle | gzip > ~/oracle-$(date +%FT%H%M).sql.gz
```

Restore:

```bash
gunzip -c ~/oracle-TIMESTAMP.sql.gz | docker compose exec -T db psql -U oracle -d oracle
```

---

## 12. Report back

Send the person who handed you this runbook:

1. **Gateway URL** laptops should use: `http://<address from step 1>:<ORACLE_GATEWAY_PORT>`
2. **The password**, once, to the group. Everyone uses the same one, and each person picks their own `X-Oracle-User` identity.
3. **The step-8 result**, as the literal two lines of output (first `inserted:1`, second `inserted:0`).
4. **Anything you had to change** from this runbook: a different port, the fallback in section 10, a troubleshooting row you hit. This matters more than it looks, because the next person debugs against what the runbook says, not against what you did.
5. **Whether the firewall step was needed**, and which tool you used.

Then tell them the shipper instructions are in `shippers/README.md` and that each laptop needs the gateway URL, the password, and an identity of its own choosing.

---

## 13. What this deployment is not

Stated plainly so nobody mistakes the hackathon setup for a product (principle P10):

- **No TLS.** Tokens and source code cross the network in the clear. Trusted LAN only.
- **No secret redaction.** Whatever an agent saw, including anything in a `.env` a tool printed, is now in this database.
- **No visibility rules, and no real authentication.** One password reads and writes everything, and anyone holding it can claim to be any identity. Cross-session reading is what the Oracle agent needs; the weak identity is a hackathon shortcut, not a design.
- **No retention limit.** Nothing is ever deleted.

Do not point this at a production network or ingest sessions from people who have not agreed to it.
