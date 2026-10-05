import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const root = new URL('../', import.meta.url);
const location = readFileSync(new URL('deploy/open-webui/vps-chat-assets-location.conf', root), 'utf8');
const cache = readFileSync(new URL('deploy/open-webui/vps-chat-assets-cache.conf', root), 'utf8');

test('first-paint public scripts have an edge path; identities and APIs remain outside it', () => {
  const pattern = new RegExp(location.match(/^location ~ (.+) \{$/m)[1]);
  for (const path of ['/chat/_app/immutable/entry/start.a.js', '/chat/_app/immutable/chunks/0popVBBz.js', '/chat/_app/immutable/nodes/0.BoNSMiaY.js', '/chat/_app/immutable/assets/app.a.css', '/chat/static/branding.js', '/chat/static/custom.css']) {
    assert.equal(pattern.test(path), true, path);
  }
  for (const path of ['/chat/', '/chat/api/config', '/chat/api/models', '/chat/api/v1/tools/', '/chat/api/v1/users/user/settings', '/chat/api/v1/chats/', '/chat/_app/version.json', '/api/files/a/download', '/chat/static/user.js', '/chat/_app/immutable/chunks/a.js/extra']) {
    assert.equal(pattern.test(path), false, path);
  }
  for (const directive of ['proxy_pass_request_headers off;', 'proxy_set_header Connection "";', 'proxy_set_header Accept-Encoding "";', 'gzip_types text/javascript application/javascript text/css;', 'proxy_cache vecta_chat_assets;', 'proxy_cache_valid 200 5m;', 'proxy_cache_lock on;', 'proxy_hide_header Set-Cookie;', 'proxy_hide_header Clear-Site-Data;', 'proxy_no_cache $vecta_chat_asset_no_cache $vecta_chat_asset_cookie_no_cache;']) {
    assert.ok(location.includes(directive), directive);
  }
  assert.ok(cache.includes('default 1;'));
  assert.ok(cache.includes('application/javascript|text/javascript|text/css'));
  assert.ok(cache.includes('max_size=256m'));
  assert.ok(cache.includes('map $upstream_http_set_cookie $vecta_chat_asset_cookie_no_cache'));
});
