import { beforeEach, afterEach, expect, it } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { createServer, type Server } from 'node:http';
import { deliveryServer, sendMessage } from '../src/network.js';
import { pollOnce } from '../src/poller.js';
import { messages, hash, atomicJson } from '../src/files.js';
import { registerSender, type SenderApi } from '../src/openclaw.js';
import { ClientSchema, MessageSchema, ServerSchema } from '../src/schema.js';
import { enqueue } from '../src/queue.js';

let dir: string; let server: Server; let url: string;
const sender = 'sender-test-token-0123456789';
const audio = 'audio-test-token-0123456789';
const chip = 'chip-test-token-0123456789';
const msg = (id = 'm1', recipient = 'audio') => MessageSchema.parse({ version: 1, id, recipient, kind: 'context', createdAt: '2026-09-12T15:00:00Z', body: `Message ${id}` });
const config = () => ClientSchema.parse({ recipient: 'audio', inbox: join(dir, 'inbox'), state: join(dir, 'state'), oracleUrl: url, allowInsecureHttp: true });
beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'oracle-net-'));
  process.env.TEST_ORACLE_SENDER = sender; process.env.TEST_ORACLE_AUDIO = audio; process.env.TEST_ORACLE_CHIP = chip;
  server = deliveryServer(ServerSchema.parse({ queue: join(dir, 'queue'), senderTokenEnv: 'TEST_ORACLE_SENDER', clients: { audio: 'TEST_ORACLE_AUDIO', chip: 'TEST_ORACLE_CHIP' } }));
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterEach(async () => {
  await new Promise<void>(r => server.close(() => r()));
  await rm(dir, { recursive: true, force: true });
  for (const k of ['TEST_ORACLE_SENDER', 'TEST_ORACLE_AUDIO', 'TEST_ORACLE_CHIP']) delete process.env[k];
});
it('sends, polls, saves and resumes without duplicate files', async () => {
  await sendMessage(url, sender, msg(), true);
  await sendMessage(url, sender, msg('chip-only', 'chip'), true);
  expect(await pollOnce(config(), audio)).toBe(1);
  expect(await pollOnce(config(), audio)).toBe(0);
  await sendMessage(url, sender, msg('m2'), true);
  expect(await pollOnce(config(), audio)).toBe(1);
  expect((await messages(config().inbox)).map(m => m.id)).toEqual(['m1', 'm2']);
});
it('rejects client impersonation, unauthorized writes, and unknown recipients', async () => {
  const response = await fetch(`${url}/clients/chip/messages`, { headers: { Authorization: `Bearer ${audio}` } });
  expect(response.status).toBe(401);
  await expect(sendMessage(url, audio, msg(), true)).rejects.toThrow();
  await expect(sendMessage(url, sender, msg('x', 'unknown'), true)).rejects.toThrow();
  await expect(pollOnce(config(), 'wrong')).rejects.toThrow();
});
it('requires distinct configured tokens', () => {
  expect(() => deliveryServer(ServerSchema.parse({ queue: dir, senderTokenEnv: 'TEST_ORACLE_SENDER', clients: { audio: 'TEST_ORACLE_SENDER' } }))).toThrow('distinct');
});
it('rejects insecure transport unless explicitly enabled', async () => {
  await expect(sendMessage(url, sender, msg())).rejects.toThrow('HTTP');
});
it('reuses IDs idempotently and rejects altered retries', async () => {
  await Promise.all([sendMessage(url, sender, msg(), true), sendMessage(url, sender, msg(), true)]);
  expect(await pollOnce(config(), audio)).toBe(1);
  await expect(sendMessage(url, sender, { ...msg(), body: 'changed' }, true)).rejects.toThrow();
});
it('recovers from a cursor replay using immutable local files', async () => {
  await sendMessage(url, sender, msg(), true); await pollOnce(config(), audio);
  const cursor = join(config().state, 'poll', hash(url + '\0audio'), 'cursor.json');
  await atomicJson(cursor, 0);
  expect(await pollOnce(config(), audio)).toBe(1);
  expect(await messages(config().inbox)).toHaveLength(1);
});
it('keeps stored messages when the Oracle goes offline', async () => {
  await sendMessage(url, sender, msg(), true); await pollOnce(config(), audio);
  await new Promise<void>(r => server.close(() => r()));
  await expect(pollOnce(config(), audio)).rejects.toThrow();
  expect(await messages(config().inbox)).toHaveLength(1);
});
it('pages queues larger than 100 messages without skipping', async () => {
  for (let i = 0; i < 101; i++) await enqueue(join(dir, 'queue'), msg(`m-${i}`));
  expect(await pollOnce(config(), audio)).toBe(100);
  expect(await pollOnce(config(), audio)).toBe(1);
  expect(await pollOnce(config(), audio)).toBe(0);
}, 15000);
it('runs the actual OpenClaw sender function against HTTP through a registration fixture', async () => {
  let tool: Parameters<SenderApi['registerTool']>[0] | undefined;
  registerSender({ pluginConfig: { oracleUrl: url, senderTokenEnv: 'TEST_ORACLE_SENDER', allowInsecureHttp: true }, registerTool(t) { tool = t; } });
  expect(tool?.name).toBe('oracle_send_to_client');
  expect(await tool!.execute('call', msg())).toMatchObject({ details: { id: 'm1', status: 'queued' } });
  expect(await pollOnce(config(), audio)).toBe(1);
});
it('rejects corrupt page routing and cursor jumps before writing messages', async () => {
  await new Promise<void>(r => server.close(() => r()));
  let payload: unknown = { messages: [{ sequence: 1, message: msg('bad', 'chip') }], cursor: 1 };
  server = createServer((_req, res) => res.end(JSON.stringify(payload)));
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  await expect(pollOnce(config(), audio)).rejects.toThrow('routing');
  payload = { messages: [{ sequence: 1, message: msg() }], cursor: 5 };
  await expect(pollOnce(config(), audio)).rejects.toThrow('cursor');
  expect(await messages(config().inbox)).toHaveLength(0);
});
