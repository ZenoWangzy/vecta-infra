#!/usr/bin/env node
/** Verify the complete ephemeral vtest platform bundle, including signatures. */
import { createPublicKey, verify } from 'node:crypto';

const FIXTURE_TENANT = 'vtest-shared-pool';
const MAX_TOKEN_TTL_SECONDS = 31 * 24 * 60 * 60;

function fail(reason) {
  console.error(`SHARED_POOL_PLATFORM_TOKEN_INVALID:${reason}`);
  process.exit(1);
}

function decodeJson(segment, reason) {
  if (!/^[A-Za-z0-9_-]+$/.test(segment)) fail(`${reason}_encoding`);
  try {
    const bytes = Buffer.from(segment, 'base64url');
    if (bytes.toString('base64url') !== segment) fail(`${reason}_encoding`);
    const value = JSON.parse(bytes.toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${reason}_object`);
    return value;
  } catch {
    fail(`${reason}_json`);
  }
}

function checkToken(token, expectedAudience, expectedScopes, expectedTenants, publicKey, tokenRole) {
  if (!token || typeof token !== 'string') fail(`${tokenRole}_missing`);
  const parts = token.split('.');
  if (parts.length !== 3) fail(`${tokenRole}_shape`);
  const header = decodeJson(parts[0], `${tokenRole}_header`);
  const payload = decodeJson(parts[1], `${tokenRole}_payload`);
  if (header.alg !== 'EdDSA' || header.typ !== 'JWT') fail(`${tokenRole}_algorithm`);
  if (!/^[A-Za-z0-9_-]+$/.test(parts[2])) fail(`${tokenRole}_signature_encoding`);
  let signature;
  try {
    signature = Buffer.from(parts[2], 'base64url');
    if (signature.toString('base64url') !== parts[2]) fail(`${tokenRole}_signature_encoding`);
  } catch {
    fail(`${tokenRole}_signature_encoding`);
  }
  if (!verify(null, Buffer.from(`${parts[0]}.${parts[1]}`), publicKey, signature)) fail(`${tokenRole}_signature`);
  if (payload.iss !== 'vecta' || payload.aud !== expectedAudience) fail(`${tokenRole}_audience`);
  if (typeof payload.sub !== 'string' || payload.sub.trim() === '') fail(`${tokenRole}_subject`);
  if (!Array.isArray(payload.scope) || payload.scope.some((value) => typeof value !== 'string') || !expectedScopes.every((scope) => payload.scope.includes(scope))) {
    fail(`${tokenRole}_scope`);
  }
  if (typeof payload.tid !== 'string' || payload.tid.trim() === '') fail(`${tokenRole}_tid`);
  if (!Array.isArray(payload.tenant_ids) || payload.tenant_ids.some((value) => typeof value !== 'string' || value.trim() === '')) {
    fail(`${tokenRole}_tenant_ids`);
  }
  const actualTenants = [payload.tid, ...payload.tenant_ids];
  if (new Set(actualTenants).size !== actualTenants.length) fail(`${tokenRole}_tenant_ids_duplicate`);
  if (JSON.stringify([...actualTenants].sort()) !== JSON.stringify([...expectedTenants].sort())) fail(`${tokenRole}_tenant_scope`);
  if (!actualTenants.includes(FIXTURE_TENANT)) fail(`${tokenRole}_fixture_scope`);
  if (typeof payload.iat !== 'number' || !Number.isInteger(payload.iat) || !Number.isFinite(payload.iat)) fail(`${tokenRole}_iat`);
  if (typeof payload.exp !== 'number' || !Number.isInteger(payload.exp) || !Number.isFinite(payload.exp)) fail(`${tokenRole}_exp`);
  const now = Math.floor(Date.now() / 1000);
  if (payload.iat > now + 60 || payload.exp <= now || payload.exp <= payload.iat || payload.exp - payload.iat > MAX_TOKEN_TTL_SECONDS) {
    fail(`${tokenRole}_time`);
  }
}

const publicPem = (process.env.VTEST_BUNDLE_PUBLIC_KEY || '').replace(/\\n/g, '\n').trim();
if (!publicPem) fail('public_key_missing');
let publicKey;
try {
  publicKey = createPublicKey(publicPem);
  if (publicKey.asymmetricKeyType !== 'ed25519') fail('public_key_algorithm');
} catch {
  fail('public_key_invalid');
}
let expectedTenants;
try {
  expectedTenants = JSON.parse(process.env.VTEST_EXPECTED_TENANT_IDS_JSON || 'null');
  if (!Array.isArray(expectedTenants)
      || expectedTenants.length === 0
      || expectedTenants.some((value) => typeof value !== 'string' || value.trim() === '')
      || new Set(expectedTenants).size !== expectedTenants.length
      || JSON.stringify([...expectedTenants].sort()) !== JSON.stringify(expectedTenants)
      || !expectedTenants.includes(FIXTURE_TENANT)) fail('expected_tenants');
} catch {
  fail('expected_tenants');
}
checkToken(process.env.VTEST_BUNDLE_CHANNEL_TOKEN, 'channel-gateway', ['channel:internal'], expectedTenants, publicKey, 'channel');
checkToken(process.env.VTEST_BUNDLE_FLEET_TOKEN, 'fleet-gateway', ['fleet:internal'], expectedTenants, publicKey, 'fleet');
checkToken(process.env.VTEST_BUNDLE_FRUIT_TOKEN, 'fleet-gateway', ['fleet:internal', 'fleet:scenario-pack-publish'], expectedTenants, publicKey, 'fruit');
console.log('SHARED_POOL_PLATFORM_TOKEN_SIGNATURES_OK');
