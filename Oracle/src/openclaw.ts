import { z } from 'zod';
import { MessageSchema } from './schema.js';
import { secret, sendMessage } from './network.js';

const PluginConfig = z.object({
  oracleUrl: z.url(), senderTokenEnv: z.string().default('ORACLE_SENDER_TOKEN'),
  allowInsecureHttp: z.boolean().default(false),
});
// Small structural boundary: no OpenClaw runtime is bundled into the receiver.
export interface SenderApi {
  pluginConfig?: unknown;
  registerTool(tool: {
    name: string; label: string; description: string; parameters: object;
    execute: (callId: string, params: unknown) => Promise<{
      content: { type: 'text'; text: string }[]; details: { id: string; status: string };
    }>;
  }): void;
}
export function registerSender(api: SenderApi) {
  const config = PluginConfig.parse(api.pluginConfig);
  api.registerTool({
    name: 'oracle_send_to_client', label: 'Send to Oracle client',
    description: 'Queue context or a task for a Claude Code client. Supply a stable unique ID and reuse it unchanged on retries. Queued does not mean consumed or completed. Normal tasks wait for a response boundary; urgent tasks reprioritize at the next prompt/tool boundary.',
    parameters: z.toJSONSchema(MessageSchema, { io: 'input' }),
    async execute(_callId, input) {
      const result = await sendMessage(config.oracleUrl, secret(config.senderTokenEnv), input, config.allowInsecureHttp);
      return { content: [{ type: 'text', text: JSON.stringify(result) }], details: result };
    },
  });
}
