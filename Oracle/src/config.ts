import { dirname, resolve } from 'node:path';
import { readJson } from './files.js';
import { ClientSchema, ServerSchema } from './schema.js';

export async function clientConfig(path: string) {
  const config = ClientSchema.parse(await readJson(path));
  return { ...config, inbox: resolve(dirname(path), config.inbox), state: resolve(dirname(path), config.state) };
}
export async function serverConfig(path: string) {
  const config = ServerSchema.parse(await readJson(path));
  return { ...config, queue: resolve(dirname(path), config.queue) };
}
