#!/usr/bin/env node
/**
 * Mint the vtest-only platform bundle in memory.
 *
 * The private key never leaves this process. The caller captures only the
 * public key and three signed JWTs, masks them, and passes them to Ansible in
 * the same shell. No file, GitHub output, environment file, or artifact is
 * written by this script.
 */
import { createPublicKey, generateKeyPairSync, sign, verify } from 'node:crypto';
import { readFileSync } from 'node:fs';

const FIXTURE_TENANT = 'vtest-shared-pool';
const TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60;

const base64url = (value) => Buffer.from(value).toString('base64url');

function readTenants() {
  const values = readFileSync(0, 'utf8')
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const tenants = [...new Set([...values, FIXTURE_TENANT])].sort();
  if (tenants.length === 0 || tenants.some((tenant) => tenant.length > 64 || /[\u0000-\u001f,]/u.test(tenant))) {
    throw new Error('invalid tenant collection');
  }
  return tenants;
}

function signJwt(privateKey, audience, scope, subject, tenants, iat, exp) {
  const header = base64url(JSON.stringify({ alg: 'EdDSA', typ: 'JWT' }));
  const payload = base64url(JSON.stringify({
    iss: 'vecta',
    aud: audience,
    sub: subject,
    scope: [scope],
    tid: tenants[0],
    tenant_ids: tenants.slice(1),
    iat,
    exp,
  }));
  const input = `${header}.${payload}`;
  const signature = sign(null, Buffer.from(input), privateKey).toString('base64url');
  const token = `${input}.${signature}`;
  if (!verify(null, Buffer.from(input), createPublicKey(privateKey), Buffer.from(signature, 'base64url'))) {
    throw new Error('generated platform token failed its signature self-check');
  }
  return token;
}

const tenants = readTenants();
const { privateKey, publicKey } = generateKeyPairSync('ed25519');
const publicPem = publicKey.export({ type: 'spki', format: 'pem' }).toString().trim().replace(/\n/g, '\\n');
const iat = Math.floor(Date.now() / 1000);
const exp = iat + TOKEN_TTL_SECONDS;

console.log(`PUBLIC_KEY_ESCAPED=${publicPem}`);
console.log(`CHANNEL_PLATFORM_SERVICE_TOKEN=${signJwt(privateKey, 'channel-gateway', 'channel:internal', 'vtest-channel', tenants, iat, exp)}`);
console.log(`FLEET_PLATFORM_SERVICE_TOKEN=${signJwt(privateKey, 'fleet-gateway', 'fleet:internal', 'vtest-fleet', tenants, iat, exp)}`);
console.log(`FLEET_FRUIT_PLATFORM_TOKEN=${signJwt(privateKey, 'fleet-gateway', 'fleet:internal', 'vtest-fruit', tenants, iat, exp)}`);
console.log(`TENANT_IDS_JSON=${JSON.stringify(tenants)}`);
