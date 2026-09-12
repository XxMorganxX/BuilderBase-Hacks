import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { once } from 'node:events';
import { deliveryServer } from './network.js';
import { registerSender, type SenderApi } from './openclaw.js';
import { ClientSchema, ServerSchema, MessageSchema } from './schema.js';
import { pollOnce } from './poller.js';
import { runHook } from './hooks.js';

const dir = await mkdtemp(join(tmpdir(), 'oracle-demo-'));
for (const name of ['DEMO_SENDER_TOKEN', 'DEMO_AUDIO_TOKEN', 'DEMO_CHIP_TOKEN']) process.env[name] = randomBytes(32).toString('hex');
const server = deliveryServer(ServerSchema.parse({
  queue: join(dir, 'queue'), port: 0, senderTokenEnv: 'DEMO_SENDER_TOKEN',
  clients: { audio: 'DEMO_AUDIO_TOKEN', chip: 'DEMO_CHIP_TOKEN' },
}));
server.listen(0, '127.0.0.1'); await once(server, 'listening');
try {
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  let tool: Parameters<SenderApi['registerTool']>[0] | undefined;
  registerSender({ pluginConfig: { oracleUrl: url, senderTokenEnv: 'DEMO_SENDER_TOKEN', allowInsecureHttp: true }, registerTool(t) { tool = t; } });
  const base = { version: 1, recipient: 'audio', createdAt: new Date().toISOString() };
  await tool!.execute('demo-1', MessageSchema.parse({ ...base, id: 'interface-update', kind: 'context', body: 'The approved audio interface is v2.' }));
  await tool!.execute('demo-2', MessageSchema.parse({ ...base, id: 'compatibility-test', kind: 'task', body: 'Add a compatibility test for interface v2.' }));
  console.log('1. OpenClaw sender contract queued context and a task over HTTP.');
  for (const recipient of ['audio', 'chip']) {
    const config = ClientSchema.parse({ recipient, inbox: join(dir, recipient, 'inbox'), state: join(dir, recipient, 'state'), oracleUrl: url, allowInsecureHttp: true });
    await pollOnce(config, process.env[recipient === 'audio' ? 'DEMO_AUDIO_TOKEN' : 'DEMO_CHIP_TOKEN']!);
    const outputs: unknown[] = [];
    const emit = async (o: unknown) => { outputs.push(o); };
    await runHook(config, { session_id: 'demo-session', hook_event_name: 'PostToolUse' }, emit);
    await runHook(config, { session_id: 'demo-session', hook_event_name: 'Stop', stop_hook_active: false }, emit);
    await runHook(config, { session_id: 'demo-session', hook_event_name: 'Stop', stop_hook_active: true }, emit);
    if (recipient === 'audio') {
      assert.match(JSON.stringify(outputs[0]), /interface-update/);
      assert.doesNotMatch(JSON.stringify(outputs[0]), /compatibility-test/);
      assert.match(JSON.stringify(outputs[1]), /compatibility-test/);
      assert.deepEqual(outputs[2], {});
      console.log('2. Audio hook emitted context after a tool, then the normal task at Stop.');
    } else {
      assert.deepEqual(outputs, [{}, {}, {}]);
      console.log('3. Chip client received neither message.');
    }
  }
  console.log('PASS: real loopback HTTP and files; simulated OpenClaw registration and Claude hook inputs. No model called.');
} finally {
  await new Promise<void>(resolve => server.close(() => resolve()));
  await rm(dir, { recursive: true, force: true });
}
