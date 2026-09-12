import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { clientConfig, serverConfig } from './config.js';
import { readJson, writeMessage } from './files.js';
import { runHook } from './hooks.js';
import { deliveryServer, secret, sendMessage } from './network.js';
import { pollOnce } from './poller.js';
import { MessageSchema } from './schema.js';

async function output(value: unknown) {
  await new Promise<void>((done, reject) => process.stdout.write(JSON.stringify(value) + '\n', e => e ? reject(e) : done()));
}
const quote = (s: string) => "'" + s.replaceAll("'", "'\\''") + "'";
async function main() {
  const [command, configPath, messagePath] = process.argv.slice(2);
  if (!configPath) throw new Error('Usage: node dist/cli.js serve|poll|poll-once|hook|settings|codex-settings CONFIG; write CLIENT_CONFIG MESSAGE.json; send ORACLE_URL MESSAGE.json');
  if (command === 'serve') {
    const config = await serverConfig(configPath);
    const server = deliveryServer(config);
    server.listen(config.port, config.host, () => process.stderr.write(`Oracle delivery listening on ${config.host}:${(server.address() as { port: number }).port}\n`));
    server.on('error', () => { process.stderr.write('Oracle server failed to listen\n'); process.exitCode = 1; });
    for (const sig of ['SIGINT', 'SIGTERM'] as const) process.once(sig, () => server.close());
    return;
  }
  if (command === 'send') {
    if (!messagePath) throw new Error('Message file required');
    await output(await sendMessage(configPath, secret('ORACLE_SENDER_TOKEN'), await readJson(messagePath), process.env.ORACLE_ALLOW_HTTP === '1'));
    return;
  }
  const config = await clientConfig(configPath);
  if (command === 'settings' || command === 'codex-settings') {
    const hook = [process.execPath, fileURLToPath(import.meta.url), 'hook', resolve(configPath)].map(quote).join(' ');
    await output({ hooks: Object.fromEntries(['UserPromptSubmit', 'PostToolUse', 'Stop'].map(event => [event, [
      { ...(event === 'PostToolUse' ? { matcher: '*' } : {}), hooks: [{
        type: 'command', command: hook, timeout: 5,
        // Our reader already bounds output; preserve it instead of spilling it.
        ...(command === 'codex-settings' && event !== 'Stop' ? { additionalContextLimit: 0 } : {}),
      }] },
    ]])) });
    return;
  }
  if (command === 'write') {
    if (!messagePath) throw new Error('Message file required');
    const message = MessageSchema.parse(await readJson(messagePath));
    if (message.recipient !== config.recipient) throw new Error('Recipient does not match client config');
    await writeMessage(config.inbox, message);
    await output({ id: message.id, status: 'stored' }); return;
  }
  if (command === 'hook') {
    const chunks: Buffer[] = []; let size = 0;
    for await (const chunk of process.stdin) {
      size += chunk.length;
      if (size > 1_000_000) throw new Error('Hook input too large');
      chunks.push(chunk);
    }
    await runHook(config, JSON.parse(Buffer.concat(chunks).toString('utf8')), output); return;
  }
  if (command === 'poll-once') { await output({ stored: await pollOnce(config, secret(config.tokenEnv)) }); return; }
  if (command === 'poll') {
    const token = secret(config.tokenEnv);
    const abort = new AbortController();
    for (const sig of ['SIGINT', 'SIGTERM'] as const) process.once(sig, () => abort.abort());
    let failures = 0;
    while (!abort.signal.aborted) {
      try { await pollOnce(config, token); failures = 0; }
      catch { failures++; process.stderr.write('Oracle Inbox: poll failed; retaining local messages and retrying\n'); }
      const wait = Math.min(30000, config.pollMs * 2 ** Math.min(failures, 4));
      await delay(wait + (failures ? Math.random() * 250 : 0), undefined, { signal: abort.signal }).catch(() => {});
    }
    return;
  }
  throw new Error('Unknown command');
}
main().catch(async e => {
  if (process.argv[2] === 'hook') {
    process.stderr.write('Oracle Inbox: hook failed; continuing normally\n');
    await output({}).catch(() => {});
  } else {
    // Validation details may include input: do not dump exception objects.
    process.stderr.write(e instanceof Error && e.name !== 'ZodError' ? `${e.message}\n` : 'Invalid configuration or message\n');
    process.exitCode = 1;
  }
});
