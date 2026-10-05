import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

test('cached first-paint script stays under 250ms when origin stalls; API and non-JS stay uncached', async () => {
  let stalled = false, assetCalls = 0;
  const origin = http.createServer((req, res) => {
    if (req.url === '/chat/api/config') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ user: req.headers.cookie }));
      return;
    }
    assetCalls++;
    assert.equal(req.headers.cookie, undefined);
    assert.equal(req.headers.authorization, undefined);
    assert.equal(req.headers['x-auth-email'], undefined);
    res.setHeader('Content-Type', req.url.includes('privacy') ? 'text/html' : 'text/javascript');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Set-Cookie', req.url.includes('cookie') ? 'session=private' : 'vecta_cache_reset_0929b=1; Path=/; Max-Age=31536000; Secure; SameSite=Lax');
    res.setHeader('Clear-Site-Data', '"cache"');
    res.setHeader('Vary', 'Accept-Encoding');
    setTimeout(() => res.end('const publicAsset = true;'.repeat(100)), stalled ? 1000 : 20);
  });
  await new Promise(resolve => origin.listen(0, '127.0.0.1', resolve));
  const reserve = net.createServer();
  await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
  const port = reserve.address().port;
  await new Promise(resolve => reserve.close(resolve));
  const dir = mkdtempSync(path.join(os.tmpdir(), 'vecta-2573-nginx-'));
  const root = new URL('../deploy/open-webui/', import.meta.url);
  const httpConfig = readFileSync(new URL('vps-chat-assets-cache.conf', root), 'utf8').replace('/var/cache/nginx/vecta-chat-assets', `${dir}/cache`);
  const location = readFileSync(new URL('vps-chat-assets-location.conf', root), 'utf8').replace('proxy_cache_valid 200 5m;', 'proxy_cache_valid 200 1s;');
  const config = `${process.getuid() === 0 ? 'user root;' : ''}\npid ${dir}/nginx.pid;\nerror_log ${dir}/error.log;\nevents {}\nhttp {\naccess_log ${dir}/access.log;\n${httpConfig}\nupstream vecta_webui_upstream { server 127.0.0.1:${origin.address().port}; }\nserver { listen 127.0.0.1:${port}; ${location}\nlocation / { proxy_pass http://vecta_webui_upstream; }\n}\n}\n`;
  writeFileSync(`${dir}/nginx.conf`, config);
  const run = (...args) => spawnSync(process.env.NGINX_BIN || '/usr/sbin/nginx', ['-p', dir, '-c', `${dir}/nginx.conf`, ...args], { encoding: 'utf8' });
  let running = false;
  try {
    const start = run();
    assert.equal(start.status, 0, start.stderr || start.error?.message);
    running = true;
    const headers = { Cookie: 'user=one', Authorization: 'Bearer dummy', 'X-Auth-Email': 'one@example.invalid', 'Accept-Encoding': 'gzip' };
    const url = `http://127.0.0.1:${port}/chat/_app/immutable/chunks/start.a.js`;
    const warm = await fetch(url, { headers });
    assert.equal(warm.status, 200);
    assert.equal(warm.headers.get('Content-Encoding'), 'gzip');
    assert.equal(warm.headers.get('Set-Cookie'), null);
    assert.equal(warm.headers.get('Clear-Site-Data'), null);
    const body = await warm.text();
    stalled = true;
    const started = performance.now();
    const hit = await fetch(url, { headers: { ...headers, Cookie: 'user=two', 'Accept-Encoding': 'gzip, deflate' } });
    assert.equal(await hit.text(), body);
    assert.ok(performance.now() - started < 250, 'cached first-paint path must not wait on stalled origin');
    assert.equal(hit.headers.get('X-Vecta-Asset-Cache'), 'HIT');
    assert.equal(hit.headers.get('Content-Encoding'), 'gzip');
    assert.equal(assetCalls, 1);
    for (const user of ['one', 'two']) {
      const api = await fetch(`http://127.0.0.1:${port}/chat/api/config`, { headers: { Cookie: `user=${user}` } });
      assert.deepEqual(await api.json(), { user: `user=${user}` });
      assert.equal(api.headers.get('X-Vecta-Asset-Cache'), null);
    }
    stalled = false;
    for (let i = 0; i < 2; i++) {
      const html = await fetch(`http://127.0.0.1:${port}/chat/_app/immutable/chunks/privacy.a.js`);
      assert.equal(await html.text(), body);
      assert.equal(html.headers.get('X-Vecta-Asset-Cache'), 'MISS');
    }
    assert.equal(assetCalls, 3);
    for (let i = 0; i < 2; i++) {
      const cookie = await fetch(`http://127.0.0.1:${port}/chat/_app/immutable/chunks/cookie.a.js`);
      assert.equal(await cookie.text(), body);
      assert.equal(cookie.headers.get('X-Vecta-Asset-Cache'), 'MISS');
      assert.equal(cookie.headers.get('Set-Cookie'), null);
    }
    assert.equal(assetCalls, 5);
    await new Promise(resolve => setTimeout(resolve, 2200));
    stalled = true;
    const expiredStarted = performance.now();
    const expired = await fetch(url, { headers });
    assert.equal(await expired.text(), body);
    assert.ok(performance.now() - expiredStarted < 250, 'expired public asset must refresh in background without blocking first paint');
    assert.equal(expired.headers.get('X-Vecta-Asset-Cache'), 'STALE');
  } finally {
    if (running) assert.equal(run('-s', 'quit').status, 0);
    origin.closeAllConnections();
    await new Promise(resolve => origin.close(resolve));
    rmSync(dir, { recursive: true, force: true });
  }
});
