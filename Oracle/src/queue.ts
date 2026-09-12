import { join } from 'node:path';
import { atomicJson, jsonFiles, locked, readJson } from './files.js';
import { EnvelopeSchema, MessageSchema } from './schema.js';

async function records(dir: string) {
  const result = [];
  for (const path of await jsonFiles(dir)) result.push(EnvelopeSchema.parse(await readJson(path)));
  return result.sort((a, b) => a.sequence - b.sequence);
}
export async function enqueue(queue: string, input: unknown) {
  const message = MessageSchema.parse(input);
  const dir = join(queue, message.recipient);
  return locked(dir, async () => {
    const all = await records(dir);
    const old = all.find(e => e.message.id === message.id);
    if (old) {
      if (JSON.stringify(old.message) !== JSON.stringify(message)) throw new Error('Message ID conflict');
      return old;
    }
    const sequence = (all.at(-1)?.sequence ?? 0) + 1;
    const record = { sequence, message };
    await atomicJson(join(dir, `${String(sequence).padStart(12, '0')}.json`), record);
    return record;
  }, 20);
}
export async function page(queue: string, recipient: string, after: number) {
  const all = await records(join(queue, recipient));
  const latest = all.at(-1)?.sequence ?? 0;
  if (after > latest) throw new Error('Cursor ahead of queue; operator reset required');
  const messages = all.filter(e => e.sequence > after).slice(0, 100);
  return { messages, cursor: messages.at(-1)?.sequence ?? after };
}
