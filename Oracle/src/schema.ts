import { z } from 'zod';

export const Recipient = z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/);
export const MessageSchema = z.object({
  version: z.literal(1),
  id: z.string().min(1).max(128),
  recipient: Recipient,
  sessionId: z.string().min(1).max(200).optional(),
  kind: z.enum(['context', 'task']),
  priority: z.enum(['normal', 'urgent']).default('normal'),
  createdAt: z.iso.datetime(),
  expiresAt: z.iso.datetime().optional(),
  body: z.string().min(1).max(6000),
}).strict();
export type Message = z.infer<typeof MessageSchema>;

export const ClientSchema = z.object({
  recipient: Recipient,
  inbox: z.string().min(1),
  state: z.string().min(1),
  oracleUrl: z.url().optional(),
  tokenEnv: z.string().default('ORACLE_CLIENT_TOKEN'),
  allowInsecureHttp: z.boolean().default(false),
  pollMs: z.number().int().min(250).max(60000).default(2000),
  maxContextChars: z.number().int().min(256).max(9000).default(8000),
}).strict();
export type ClientConfig = z.infer<typeof ClientSchema>;
export const ServerSchema = z.object({
  queue: z.string().min(1),
  host: z.string().default('127.0.0.1'),
  port: z.number().int().min(0).max(65535).default(8787),
  senderTokenEnv: z.string().default('ORACLE_SENDER_TOKEN'),
  clients: z.record(Recipient, z.string().min(1)),
}).strict();
export const EnvelopeSchema = z.object({
  sequence: z.number().int().positive(), message: MessageSchema,
}).strict();
export const PageSchema = z.object({
  messages: z.array(EnvelopeSchema).max(100),
  cursor: z.number().int().nonnegative(),
}).strict();
