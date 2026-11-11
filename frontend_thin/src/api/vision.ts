import { createClient } from './client.mjs';

const client = createClient();
export const request = (path: string, data?: unknown, signal?: AbortSignal): Promise<any> =>
  client.request(path, data, signal);
