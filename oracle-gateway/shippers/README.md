# Shipping sessions off a laptop

A shipper finds native session files on an engineer's machine and posts them to the gateway. It stays deliberately dumb: the gateway de-duplicates, so a shipper may re-send anything at any time and never has to track what the server already has (principle P4).

Each laptop needs three things, and nothing else:

```bash
export ORACLE_GATEWAY_URL=http://SERVER:8080
export ORACLE_PASSWORD=oracle          # the same for everyone
export ORACLE_USER=you@example.com     # who you are; this is what Oracle routes to
```

There is no key to request. The password gets you in, and `ORACLE_USER` says whose session this is. Use the same value every time, or your work shows up as two different people.

## Today: ship with curl

This works right now, with no install. It posts every session file touched in the last day.

```bash
# Claude Code
for f in $(find ~/.claude/projects -name '*.jsonl' -mtime -1); do
  curl -s -H "Authorization: Bearer $ORACLE_PASSWORD" -H "X-Oracle-User: $ORACLE_USER" \
       -H 'Content-Type: application/x-ndjson' --data-binary @"$f" \
       "$ORACLE_GATEWAY_URL/v1/ingest/raw/claude_code"; echo
done

# Codex
for f in $(find ~/.codex/sessions -name 'rollout-*.jsonl' -mtime -1); do
  curl -s -H "Authorization: Bearer $ORACLE_PASSWORD" -H "X-Oracle-User: $ORACLE_USER" \
       -H 'Content-Type: application/x-ndjson' --data-binary @"$f" \
       "$ORACLE_GATEWAY_URL/v1/ingest/raw/codex"; echo
done
```

Each call prints how many events were received, written and skipped. A large `skipped` is normal: session files are mostly UI chrome and only conversation lines are stored. Run it on a loop (`watch -n 30`) and a live session keeps flowing in, because each run re-sends the whole file and the server keeps only what is new.

Files above 10 MB are rejected with a 413. Until the tailing shipper lands, split those or leave them.

## Next: the tailing shipper

`oracle_shipper.py` is specified in `docs/PLAN.md` section 8 and not built yet. It is standard-library Python only, so any laptop runs it without installing anything. It tails by byte offset instead of re-sending whole files, which is what makes it cheap enough to run every couple of seconds.

## Agents that are not Claude Code or Codex

Post the canonical shape to `/v1/ingest` and no adapter is needed. The contract is `docs/PLAN.md` section 4:

```bash
curl -H "Authorization: Bearer $ORACLE_PASSWORD" -H "X-Oracle-User: $ORACLE_USER" \
     -H 'Content-Type: application/json' \
     -d '{"session":{"external_id":"my-run-1","agent_kind":"custom"},
          "events":[{"external_id":"e1","type":"user_message","role":"user",
                     "content":[{"type":"text","text":"hello"}],
                     "occurred_at":"2026-09-12T18:00:00Z"}]}' \
     "$ORACLE_GATEWAY_URL/v1/ingest"
```

If instead you want that vendor's native format converted server-side like the other two, write an adapter: one module in `gateway/oracle_gateway/adapters/`, one line in the registry, one fixture, one test.
