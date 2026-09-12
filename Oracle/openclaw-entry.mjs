import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { registerSender } from './dist/openclaw.js';

export default definePluginEntry({
  id: 'oracle-inbox',
  name: 'Oracle Inbox sender',
  description: 'Send Oracle context and assignments to Claude Code clients.',
  register: registerSender,
});
