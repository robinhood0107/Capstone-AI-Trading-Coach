import { readAgentConfig } from './src/server/config';

export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    // Quota settings must be valid before the service accepts public traffic.
    readAgentConfig();
  }
}
