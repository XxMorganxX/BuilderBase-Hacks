import { createServer, type IncomingMessage } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { z } from 'zod';
import { enqueue, page } from './queue.js';
import { MessageSchema, Recipient, ServerSchema } from './schema.js';

export function secret(name: string) {
  const value = process.env[name];
  if (!value || value.length < 24) throw new Error(`Set ${name} to a token of at least 24 characters`);
  return value;
}
function authorized(req: IncomingMessage, token: string) {
  const expected = Buffer.from(`Bearer ${token}`);
  const actual = Buffer.from(req.headers.authorization ?? '');
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
async function body(req: IncomingMessage) {
  const chunks: Buffer[] = []; let length = 0;
  for await (const chunk of req) {
    length += chunk.length;
    if (length > 65536) throw new Error('Request too large');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
export function deliveryServer(config: z.infer<typeof ServerSchema>) {
  const sender = secret(config.senderTokenEnv);
  const clients = new Map(Object.entries(config.clients).map(([id, env]) => [id, secret(env)]));
  if (new Set([sender, ...clients.values()]).size !== clients.size + 1) throw new Error('Use distinct sender and client tokens');
  return createServer({ requestTimeout: 10000, headersTimeout: 10000 }, async (req, res) => {
    const reply = (status: number, value: unknown) => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(value));
    };
    try {
      const url = new URL(req.url ?? '/', 'http://localhost');
      if (req.method === 'POST' && url.pathname === '/messages') {
        if (!authorized(req, sender)) { reply(401, { error: 'Unauthorized' }); return; }
        const message = MessageSchema.parse(await body(req));
        if (!clients.has(message.recipient)) { reply(400, { error: 'Unknown recipient' }); return; }
        reply(201, await enqueue(config.queue, message)); return;
      }
      const match = /^\/clients\/([^/]+)\/messages$/.exec(url.pathname);
      if (req.method === 'GET' && match) {
        const recipient = Recipient.parse(match[1]);
        const token = clients.get(recipient);
        if (!token || !authorized(req, token)) { reply(401, { error: 'Unauthorized' }); return; }
        const raw = url.searchParams.get('after') ?? '0';
        if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw))) throw new Error('Invalid cursor');
        reply(200, await page(config.queue, recipient, Number(raw))); return;
      }
      reply(404, { error: 'Not found' });
    } catch {
      // Do not expose message bodies, filesystem paths, or credentials in errors.
      reply(400, { error: 'Invalid request, conflicting ID, or unavailable queue' });
    }
  });
}
export function endpoint(base: string, path: string, allowInsecureHttp: boolean) {
  const url = new URL(base);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('Use an Oracle origin URL');
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && allowInsecureHttp)) throw new Error('HTTP requires explicit allowInsecureHttp for the LAN demo');
  return new URL(path, url);
}
export async function sendMessage(base: string, token: string, input: unknown, allowInsecureHttp = false) {
  const message = MessageSchema.parse(input);
  const response = await fetch(endpoint(base, '/messages', allowInsecureHttp), {
    method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(message), redirect: 'error', signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`Oracle send failed (${response.status}); retry with the same ID`);
  return { id: message.id, status: 'queued' as const };
}
