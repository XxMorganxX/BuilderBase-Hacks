import { join } from 'node:path';
import { z } from 'zod';
import { atomicJson, hash, isMissing, locked, readJson, writeMessage } from './files.js';
import { endpoint } from './network.js';
import { PageSchema, type ClientConfig } from './schema.js';

export async function pollOnce(config: ClientConfig, token: string) {
  if (!config.oracleUrl) throw new Error('oracleUrl is required for polling');
  const dir = join(config.state, 'poll', hash(config.oracleUrl + '\0' + config.recipient));
  return locked(dir, async () => {
    let cursor = 0;
    const cursorPath = join(dir, 'cursor.json');
    try { cursor = z.number().int().nonnegative().parse(await readJson(cursorPath)); }
    catch (e) { if (!isMissing(e)) throw e; }
    const url = endpoint(config.oracleUrl!, `/clients/${config.recipient}/messages?after=${cursor}`, config.allowInsecureHttp);
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` }, redirect: 'error', signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(`Oracle poll failed (${response.status})`);
    if (!response.body) throw new Error('Empty poll response');
    const chunks: Uint8Array[] = []; let bytes = 0;
    for await (const chunk of response.body) {
      bytes += chunk.length;
      if (bytes > 4_000_000) throw new Error('Poll response too large');
      chunks.push(chunk);
    }
    const result = PageSchema.parse(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    let previous = cursor;
    for (const e of result.messages) {
      if (e.message.recipient !== config.recipient || e.sequence !== previous + 1) throw new Error('Invalid routing or sequence');
      previous = e.sequence;
    }
    if (result.cursor !== previous) throw new Error('Invalid cursor advancement');
    for (const e of result.messages) await writeMessage(config.inbox, e.message);
    // Commit cursor only after every file is stored. Replay after a crash is safe.
    await atomicJson(cursorPath, result.cursor);
    return result.messages.length;
  });
}
