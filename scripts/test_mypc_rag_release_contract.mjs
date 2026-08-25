import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';

const repoRoot = resolve(new URL('..', import.meta.url).pathname);
const releaseScript = join(repoRoot, 'scripts/release-mypc-rag-service.sh');
const endpointOverride = join(repoRoot, 'deploy/mypc/rag-hf-endpoint.override.yml');
const imageOnlyOverride = join(repoRoot, 'deploy/mypc/rag-service-image.override.yml');
const targetImage = '127.0.0.1:8082/rag-service@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

function writeExecutable(path, source) {
  writeFileSync(path, source);
  chmodSync(path, 0o755);
}

function createFakeMypc({ extraMount = false, targetHealthFailure = false } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'mypc-rag-release-contract-'));
  const binDir = join(root, 'bin');
  const statePath = join(root, 'state.json');
  const operationsPath = join(root, 'operations.log');
  const composePath = join(root, 'migration-compose.config.yml');
  mkdirSync(binDir, { recursive: true });
  writeFileSync(composePath, 'services: {}\n');
  writeFileSync(operationsPath, '');
  writeFileSync(statePath, JSON.stringify({ stage: 'baseline', extraMount, targetHealthFailure }));

  const fakeDocker = String.raw`#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const statePath = process.env.FAKE_STATE;
const operationsPath = process.env.FAKE_OPERATIONS;
const targetImage = '127.0.0.1:8082/rag-service@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const baselineImage = '127.0.0.1:8082/rag-service:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const targetImageId = 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';
const baselineImageId = 'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd';
function readState() { return JSON.parse(fs.readFileSync(statePath, 'utf8')); }
function writeState(stage) { const state = readState(); state.stage = stage; fs.writeFileSync(statePath, JSON.stringify(state)); }
function imageInfo(ref) {
  const target = ref === targetImage;
  return {
    Id: target ? targetImageId : baselineImageId,
    Config: {
      Env: ['PATH=/usr/local/bin:/usr/bin', 'NODE_VERSION=20'],
      Cmd: ['node', 'dist/fastify-server.js'],
      Entrypoint: ['docker-entrypoint.sh'],
      User: 'node',
      WorkingDir: '/app',
    },
  };
}
function renderedConfig(includeEndpoint) {
  const environment = {
    DATABASE_URL: 'postgresql://secret-db',
    RAG_INTERNAL_TOKEN: 'secret-rag-token',
    HF_HOME: '/home/node/.cache/huggingface',
    TRANSFORMERS_CACHE: '/home/node/.cache/huggingface',
  };
  if (includeEndpoint) environment.HF_ENDPOINT = 'https://hf-mirror.com';
  return {
    services: {
      'rag-service': {
        image: process.env.RAG_SERVICE_IMAGE,
        container_name: 'openclaw-rag-service',
        environment,
        restart: 'unless-stopped',
        ports: [{ target: 8000, published: '8000', protocol: 'tcp', mode: 'ingress' }],
        volumes: [
          { type: 'volume', source: 'openclaw-enterprise_rag_model_cache', target: '/home/node/.cache/huggingface' },
          { type: 'bind', source: '/data/ocee/packages/rag-service/knowledge', target: '/app/knowledge', bind: { create_host_path: true } },
        ],
        networks: { 'openclaw-net': {} },
      },
    },
    networks: { 'openclaw-net': { name: 'openclaw-enterprise_openclaw-net' } },
  };
}
function liveContainer() {
  const state = readState();
  const target = state.stage === 'target';
  const image = target ? targetImage : baselineImage;
  const imageId = target ? targetImageId : baselineImageId;
  const environment = [
    'PATH=/usr/local/bin:/usr/bin',
    'NODE_VERSION=20',
    'DATABASE_URL=postgresql://secret-db',
    'RAG_INTERNAL_TOKEN=secret-rag-token',
    'HF_HOME=/home/node/.cache/huggingface',
    'TRANSFORMERS_CACHE=/home/node/.cache/huggingface',
  ];
  if (target) environment.push('HF_ENDPOINT=https://hf-mirror.com');
  const mounts = [
    { Type: 'volume', Name: 'openclaw-enterprise_rag_model_cache', Source: '/var/lib/docker/volumes/rag/_data', Destination: '/home/node/.cache/huggingface', RW: true },
    { Type: 'bind', Source: '/data/ocee/packages/rag-service/knowledge', Destination: '/app/knowledge', RW: true },
  ];
  if (state.extraMount) mounts.push({ Type: 'bind', Source: '/unexpected', Destination: '/unexpected', RW: true });
  return {
    Name: '/openclaw-rag-service',
    Image: imageId,
    State: { Running: true },
    Config: {
      Image: image,
      Env: environment,
      Cmd: ['node', 'dist/fastify-server.js'],
      Entrypoint: ['docker-entrypoint.sh'],
      User: 'node',
      WorkingDir: '/app',
    },
    HostConfig: {
      RestartPolicy: { Name: 'unless-stopped' },
      PortBindings: { '8000/tcp': [{ HostIp: '0.0.0.0', HostPort: '8000' }] },
      ReadonlyRootfs: false,
      Privileged: false,
    },
    Mounts: mounts,
    NetworkSettings: {
      Networks: {
        'openclaw-enterprise_openclaw-net': { Aliases: ['openclaw-rag-service', 'rag-service'] },
      },
    },
  };
}
if (args[0] === 'image' && args[1] === 'inspect') {
  process.stdout.write(JSON.stringify([imageInfo(args[2])]));
  process.exit(0);
}
if (args[0] === 'inspect') {
  if (args.includes('--format')) {
    const template = args[args.indexOf('--format') + 1];
    const live = liveContainer();
    if (template.includes('.Config.Image')) process.stdout.write(live.Config.Image);
    else if (template.includes('.State.Running')) process.stdout.write(String(live.State.Running));
    else process.exit(2);
  } else {
    process.stdout.write(JSON.stringify([liveContainer()]));
  }
  process.exit(0);
}
if (args[0] === 'compose' && args.includes('config') && args.includes('--format')) {
  const includeEndpoint = args.some(arg => arg.endsWith('rag-hf-endpoint.override.yml'));
  process.stdout.write(JSON.stringify(renderedConfig(includeEndpoint)));
  process.exit(0);
}
if (args[0] === 'compose' && args.includes('up')) {
  const configPath = args.filter(arg => arg.endsWith('.json')).at(-1);
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const endpoint = Object.prototype.hasOwnProperty.call(config.services['rag-service'].environment, 'HF_ENDPOINT');
  const stage = endpoint ? 'target' : 'baseline';
  writeState(stage);
  fs.appendFileSync(operationsPath, 'up:' + stage + '\n');
  process.exit(0);
}
process.stderr.write('unexpected docker invocation\n');
process.exit(99);
`;
  const fakeCurl = String.raw`#!/usr/bin/env node
const fs = require('node:fs');
const state = JSON.parse(fs.readFileSync(process.env.FAKE_STATE, 'utf8'));
const healthy = state.stage !== 'target' || !state.targetHealthFailure;
process.stdout.write(healthy ? '{"ok":true,"service":"rag-service"}' : '{"ok":false}');
`;
  const fakeHostname = '#!/usr/bin/env bash\nprintf mypc\n';
  const fakeId = '#!/usr/bin/env bash\nprintf 0\n';
  writeExecutable(join(binDir, 'docker'), fakeDocker);
  writeExecutable(join(binDir, 'curl'), fakeCurl);
  writeExecutable(join(binDir, 'hostname'), fakeHostname);
  writeExecutable(join(binDir, 'id'), fakeId);
  return {
    root,
    composePath,
    statePath,
    operationsPath,
    dockerPath: join(binDir, 'docker'),
    curlPath: join(binDir, 'curl'),
    hostnamePath: join(binDir, 'hostname'),
    idPath: join(binDir, 'id'),
  };
}

function runRelease(fake, { execute = false } = {}) {
  return spawnSync('bash', [releaseScript, execute ? '--execute' : '--check'], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: {
      ...process.env,
      RAG_SERVICE_IMAGE: targetImage,
      RAG_BASE_COMPOSE: fake.composePath,
      RAG_RELEASE_OVERRIDE: endpointOverride,
      RAG_RELEASE_IMAGE_OVERRIDE: imageOnlyOverride,
      RAG_RELEASE_LOCK_FILE: join(fake.root, 'release.lock'),
      RAG_RELEASE_RETRIES: '1',
      RAG_RELEASE_RETRY_DELAY_SECONDS: '0',
      MYPC_DEPLOY_ENABLED: execute ? 'true' : '',
      DOCKER_BIN: fake.dockerPath,
      CURL_BIN: fake.curlPath,
      RAG_HOSTNAME_BIN: fake.hostnamePath,
      RAG_ID_BIN: fake.idPath,
      FAKE_STATE: fake.statePath,
      FAKE_OPERATIONS: fake.operationsPath,
    },
  });
}

function withFake(options, callback) {
  const fake = createFakeMypc(options);
  try {
    callback(fake);
  } finally {
    rmSync(fake.root, { recursive: true, force: true });
  }
}

test('check renders the exact RAG replacement contract without mutating production state', () => {
  withFake({}, (fake) => {
    const result = runRelease(fake);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /RESULT=ready/);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
    assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /secret-rag-token|secret-db/);
  });
});

test('execute recreates only RAG with the target digest and endpoint contract', () => {
  withFake({}, (fake) => {
    const result = runRelease(fake, { execute: true });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /RESULT=changed/);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), 'up:target\n');
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'target');
  });
});

test('check rejects a live RAG mount that is absent from the rendered Compose contract', () => {
  withFake({ extraMount: true }, (fake) => {
    const result = runRelease(fake);
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
  });
});

test('execute rolls back to the exact baseline if target readiness fails', () => {
  withFake({ targetHealthFailure: true }, (fake) => {
    const result = runRelease(fake, { execute: true });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /exact baseline restored/);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), 'up:target\nup:baseline\n');
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'baseline');
    assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /secret-rag-token|secret-db/);
  });
});
