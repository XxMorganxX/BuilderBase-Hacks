# Oracle developer handoff

This service transports your selected messages to Claude Code. Oracle owns content selection, authorization, reasoning, and assignment decisions. It does not send raw client prompts or answers back to you.

## Message v1

```json
{
  "version": 1,
  "id": "unique-stable-message-id",
  "recipient": "audio-agent",
  "sessionId": "optional-exact-claude-session-id",
  "kind": "task",
  "priority": "normal",
  "createdAt": "2026-09-12T15:00:00Z",
  "expiresAt": "2026-09-12T18:00:00Z",
  "body": "Add a compatibility test for interface v2."
}
```

Unknown fields are rejected. `version` must equal 1. `id` is 1–128 characters; recipient is 1–64 alphanumeric, underscore or hyphen characters and starts alphanumeric. `sessionId` is an optional exact string of 1–200 characters. Dates are ISO 8601 UTC timestamps. `kind` is `context` or `task`; priority defaults to `normal`. Body is nonempty Markdown, at most 6,000 characters. Omit optional fields rather than sending null. The exact validator is `src/schema.ts`.

An ID is immutable within its recipient's queue. Retry the same normalized message with the same ID; `priority` omission and `normal` are equivalent. A new task or corrected context needs a new ID. Messages with no `sessionId` are delivered once per recipient/session pair; new sessions can receive retained messages again. Use expiry and/or explicit targeting when appropriate. No session discovery endpoint is implemented: configure recipient names out of band, or obtain session IDs through your separately owned integration.

## Send

`POST /messages`

Header: `Authorization: Bearer <sender token>`

Body: the message object. Request body limit: 64 KiB.

Successful first submission and identical retries both return HTTP 201:

```json
{
  "sequence": 1,
  "message": {
    "version": 1,
    "id": "unique-stable-message-id",
    "recipient": "audio-agent",
    "kind": "context",
    "priority": "normal",
    "createdAt": "2026-09-12T15:00:00Z",
    "body": "Use interface v2."
  }
}
```

401 means invalid credentials. 400 is a generic rejection for malformed input, unregistered recipient, altered ID reuse, cursor inconsistency, or queue failure; error responses do not echo content. A network error or timeout has an uncertain outcome: retry the unchanged ID. Clients' read tokens cannot submit messages or read other recipients.

The OpenClaw tool `oracle_send_to_client` wraps this POST. It accepts the same complete message object and returns `{ "id": "...", "status": "queued" }`. This is not a completion acknowledgement. Existing Oracle code can use the POST directly or import `sendMessage` from built `dist/network.js`.

## Client pull

`GET /clients/audio-agent/messages?after=0`

Header: `Authorization: Bearer <audio client token>`

```json
{
  "messages": [{ "sequence": 1, "message": { "...": "complete v1 message" } }],
  "cursor": 1
}
```

The example above abbreviates the message for readability. Real payloads contain the complete validated object. Sequences are contiguous per recipient, starting at 1, in queue acceptance order. At most 100 records are returned. An empty page preserves the requested cursor. The client saves all records before committing the cursor. It rejects recipient mismatches, sequence gaps, and cursor jumps. A cursor ahead of a reset queue fails rather than silently skipping future messages.

Polling is transport-only: no prompt content, answers, or task status is uploaded. There is no acknowledgement/deletion API. Queues retain their messages; the client cursor tracks transport progress. Expired messages still cross the transport so sequences remain contiguous, but hooks discard them. Queue storage and client-local expiry are not the authoritative business-unit policy boundary.

## Filesystem alternative / local writer

Use `writeMessage(inbox, message)` from `dist/files.js`, or `node dist/cli.js write CLIENT_CONFIG MESSAGE.json`, for local fixture writes. It validates, takes the inbox lock, writes a mode-0600 temporary file, and renames it to `<sha256(id)>.json`. Identical writes are no-ops; changed content under the same ID fails. Do not overwrite published files. Only the delivery service should allocate network sequence records; do not hand-edit its queue.

The polling receiver owns local inbox writes; the hook only reads messages and writes separate local receipts. Keep `.oracle/` and machine-specific configs out of Git. Filesystem permissions must prevent unrelated OS users from changing inboxes or tokens; this application is not an OS sandbox.

## Integration acceptance

1. Register two recipients and provision distinct read tokens plus the sender token.
2. Send a context update through Oracle's actual OpenClaw tool.
3. Verify only the addressed client downloads it.
4. At a prompt/tool boundary, verify Claude receives the update without a session restart.
5. Send a normal task; verify it waits until a natural Stop. An urgent task should arrive at the next prompt/tool boundary.
6. Repeat the same ID and reconnect the receiver; neither should create another normal emission for that session.

The delivery gate uses response completion as the approximation for work completion. Semantic scheduling, cancelling or replacing assignments, and reporting execution results remain with Oracle's owner.
