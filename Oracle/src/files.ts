import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile, rename, unlink, readdir, open, constants } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import lockfile from 'proper-lockfile';
import { MessageSchema, type Message } from './schema.js';

export const hash = (s: string) => createHash('sha256').update(s).digest('hex');
export const isMissing = (e: unknown) => (e as NodeJS.ErrnoException).code === 'ENOENT';
export async function atomicJson(path: string, value: unknown) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temp, JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    await rename(temp, path);
  } finally { await unlink(temp).catch(() => {}); }
}
export async function readJson(path: string): Promise<unknown> {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > 65536) throw new Error('Invalid file size or type');
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}
export async function jsonFiles(dir: string) {
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    if (entries.length > 10000) throw new Error('Inbox file limit reached');
    return entries.filter(e => e.isFile() && e.name.endsWith('.json')).map(e => join(dir, e.name)).sort();
  } catch (e) { if (isMissing(e)) return []; throw e; }
}
export async function locked<T>(dir: string, work: () => Promise<T>, retries = 0): Promise<T> {
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const release = await lockfile.lock(dir, {
    realpath: false, stale: 10000, update: 2000,
    retries: { retries, minTimeout: 30, maxTimeout: 100 },
  });
  try { return await work(); } finally { await release(); }
}
export async function writeMessage(inbox: string, input: unknown): Promise<Message> {
  const message = MessageSchema.parse(input);
  return locked(inbox, async () => {
    const path = join(inbox, `${hash(message.id)}.json`);
    try {
      const existing = MessageSchema.parse(await readJson(path));
      if (JSON.stringify(existing) !== JSON.stringify(message)) throw new Error('Message ID conflict');
      return existing;
    } catch (e) { if (!isMissing(e)) throw e; }
    await atomicJson(path, message);
    return message;
  }, 20);
}
export async function messages(inbox: string): Promise<Message[]> {
  const result: Message[] = [];
  for (const path of await jsonFiles(inbox)) {
    try { result.push(MessageSchema.parse(await readJson(path))); }
    catch { process.stderr.write('Oracle Inbox: skipped invalid message file\n'); }
  }
  return result.sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
}
