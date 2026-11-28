import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from '../src/lib/supabase';

const config = { url: 'https://example.supabase.co', key: 'k' };
const reply = (status: number, body: unknown = []) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => vi.unstubAllGlobals());

describe('get', () => {
  it('tries again after a 500 (statement timeout) and returns the data', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(reply(500))
      .mockResolvedValueOnce(reply(200, [{ id: 1 }]));
    vi.stubGlobal('fetch', fetch);
    await expect(get(config, 'promos?select=*', [0, 0])).resolves.toEqual([{ id: 1 }]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('tries again after a network error', async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(reply(200));
    vi.stubGlobal('fetch', fetch);
    await expect(get(config, 'promos', [0])).resolves.toEqual([]);
  });

  it('gives up after the last attempt', async () => {
    const fetch = vi.fn().mockResolvedValue(reply(500));
    vi.stubGlobal('fetch', fetch);
    await expect(get(config, 'promos?x=1', [0, 0])).rejects.toThrow('Supabase 500 on promos');
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('does not retry a request the server refuses (4xx)', async () => {
    const fetch = vi.fn().mockResolvedValue(reply(401));
    vi.stubGlobal('fetch', fetch);
    await expect(get(config, 'promos', [0, 0])).rejects.toThrow('Supabase 401');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
