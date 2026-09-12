import { afterEach, beforeEach, expect, it } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { atomicJson, writeMessage } from '../src/files.js';
import { ClientSchema, MessageSchema } from '../src/schema.js';

let dir: string;
beforeEach(async () => { dir = await mkdtemp(join(tmpdir(), "oracle codex 'test-")); });
afterEach(async () => { await rm(dir, { recursive: true, force: true }); });

it.each(['UserPromptSubmit', 'PostToolUse', 'Stop'])('generated Codex %s command emits correct output with real subprocesses', async event => {
  const config = ClientSchema.parse({ recipient: 'audio', inbox: join(dir, 'inbox'), state: join(dir, 'codex-state') });
  const path = join(dir, 'client.json');
  await atomicJson(path, config);
  await writeMessage(config.inbox, MessageSchema.parse({
    version: 1, id: 'codex-test', recipient: 'audio', sessionId: 'codex-session',
    kind: event === 'Stop' ? 'task' : 'context',
    createdAt: '2026-09-12T15:00:00Z', body: 'ORACLE_CODEX_OK',
  }));
  const generated = spawnSync(process.execPath, [resolve('dist/cli.js'), 'codex-settings', path], { encoding: 'utf8' });
  expect(generated.status).toBe(0);
  const hooks = JSON.parse(generated.stdout).hooks;
  const handler = hooks[event][0].hooks[0];
  expect(handler.additionalContextLimit).toBe(event === 'Stop' ? undefined : 0);
  const invoke = (session: string, active = false) => spawnSync('/bin/sh', ['-c', handler.command], {
    encoding: 'utf8', cwd: tmpdir(), input: JSON.stringify({
      hook_event_name: event, session_id: session, turn_id: 'codex-turn',
      model: 'test-model', transcript_path: null, permission_mode: 'default',
      tool_name: 'apply_patch', tool_use_id: 'test-call', tool_input: {}, tool_response: {},
      prompt: 'test only', stop_hook_active: active, last_assistant_message: null,
    }),
  });
  expect(JSON.parse(invoke('different-session').stdout)).toEqual({});
  if (event === 'Stop') expect(JSON.parse(invoke('codex-session', true).stdout)).toEqual({});
  const emitted = invoke('codex-session');
  expect(emitted.status).toBe(0);
  const output = JSON.parse(emitted.stdout);
  if (event === 'Stop') {
    expect(output.decision).toBe('block'); expect(output.reason).toContain('ORACLE_CODEX_OK');
  } else {
    expect(output.hookSpecificOutput.hookEventName).toBe(event);
    expect(output.hookSpecificOutput.additionalContext).toContain('ORACLE_CODEX_OK');
  }
  expect(JSON.parse(invoke('codex-session').stdout)).toEqual({});
});
