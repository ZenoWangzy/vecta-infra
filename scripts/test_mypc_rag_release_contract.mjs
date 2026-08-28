import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
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
const sourceSha = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';

function writeExecutable(path, source) {
  writeFileSync(path, source);
  chmodSync(path, 0o755);
}

function createFakeMypc({
  extraMount = false,
  extraSecurityOpt = false,
  targetHealthFailure = false,
  targetRuntimeDrift = false,
  wrongTargetProvenance = false,
} = {}) {
  const root = mkdtempSync(join(tmpdir(), 'mypc-rag-release-contract-'));
  const binDir = join(root, 'bin');
  const statePath = join(root, 'state.json');
  const operationsPath = join(root, 'operations.log');
  const composePath = join(root, 'migration-compose.config.yml');
  const cachePath = join(root, 'rag-cache');
  const knowledgePath = join(root, 'knowledge');
  const backupRoot = join(root, 'backups');
  mkdirSync(binDir, { recursive: true });
  mkdirSync(cachePath, { recursive: true });
  mkdirSync(knowledgePath, { recursive: true });
  writeFileSync(join(cachePath, 'baseline-cache.txt'), 'baseline cache\n');
  writeFileSync(join(knowledgePath, 'baseline-knowledge.txt'), 'baseline knowledge\n');
  writeFileSync(composePath, 'services: {}\n');
  writeFileSync(operationsPath, '');
  writeFileSync(statePath, JSON.stringify({
    stage: 'baseline',
    ragRunning: true,
    fleetPaused: false,
    extraMount,
    extraSecurityOpt,
    targetHealthFailure,
    targetRuntimeDrift,
    wrongTargetProvenance,
  }));

  const fakeDocker = String.raw`#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const statePath = process.env.FAKE_STATE;
const operationsPath = process.env.FAKE_OPERATIONS;
const targetImage = '127.0.0.1:8082/rag-service@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const baselineImage = '127.0.0.1:8082/rag-service:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const targetImageId = 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';
const baselineImageId = 'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd';
const baselineDigest = '127.0.0.1:8082/rag-service@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd';
const sourceSha = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';
const cachePath = process.env.FAKE_CACHE_PATH;
const knowledgePath = process.env.FAKE_KNOWLEDGE_PATH;
if (process.env.FAKE_REQUIRE_LOCAL_DOCKER_CONTEXT === 'true'
  && [process.env.DOCKER_HOST, process.env.DOCKER_CONTEXT, process.env.DOCKER_CONFIG].some(Boolean)) {
  process.stderr.write('docker context was not scrubbed\\n');
  process.exit(97);
}
function readState() { return JSON.parse(fs.readFileSync(statePath, 'utf8')); }
function writeState(stage, imageRef) {
  const state = readState();
  state.stage = stage;
  state.imageRef = imageRef;
  state.ragRunning = true;
  fs.writeFileSync(statePath, JSON.stringify(state));
}
function imageInfo(ref) {
  const state = readState();
  const target = ref === targetImage || ref === targetImageId;
  return {
    Id: target ? targetImageId : baselineImageId,
    RepoDigests: [target ? targetImage : baselineDigest],
    Config: {
      Env: ['PATH=/usr/local/bin:/usr/bin', 'NODE_VERSION=20'],
      Cmd: ['node', 'dist/fastify-server.js'],
      Entrypoint: ['docker-entrypoint.sh'],
      User: 'node',
      WorkingDir: '/app',
      Labels: target ? {
        'org.opencontainers.image.revision': state.wrongTargetProvenance
          ? 'ffffffffffffffffffffffffffffffffffffffff'
          : sourceSha,
        'com.vecta.source.repository': 'ZenoWangzy/vecta',
      } : {},
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
        logging: { driver: 'json-file', options: { 'max-size': '50m', 'max-file': '5' } },
        mem_limit: '1073741824',
        memswap_limit: '2147483648',
        cpus: 1,
        ports: [{ target: 8000, published: '8000', protocol: 'tcp', mode: 'ingress' }],
        volumes: [
          { type: 'volume', source: 'openclaw-enterprise_rag_model_cache', target: '/home/node/.cache/huggingface' },
          { type: 'bind', source: knowledgePath, target: '/app/knowledge', bind: { create_host_path: true } },
        ],
        networks: { 'openclaw-net': {} },
      },
    },
    networks: { 'openclaw-net': { name: 'openclaw-enterprise_openclaw-net' } },
  };
}
function liveRagContainer() {
  const state = readState();
  const target = state.stage === 'target';
  const image = state.imageRef || (target ? targetImage : baselineImage);
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
    { Type: 'volume', Name: 'openclaw-enterprise_rag_model_cache', Source: cachePath, Destination: '/home/node/.cache/huggingface', RW: true },
    { Type: 'bind', Source: knowledgePath, Destination: '/app/knowledge', RW: true },
  ];
  if (state.extraMount) mounts.push({ Type: 'bind', Source: '/unexpected', Destination: '/unexpected', RW: true });
  return {
    Name: '/openclaw-rag-service',
    Image: imageId,
    State: { Running: state.ragRunning, Paused: false },
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
      LogConfig: { Type: 'json-file', Config: { 'max-size': '50m', 'max-file': '5' } },
      Memory: 1073741824,
      MemorySwap: 2147483648,
      MemoryReservation: target && state.targetRuntimeDrift ? 1048576 : 0,
      NanoCpus: 1000000000,
      ReadonlyRootfs: false,
      Privileged: false,
      SecurityOpt: state.extraSecurityOpt ? ['no-new-privileges:true'] : null,
    },
    Mounts: mounts,
    NetworkSettings: {
      Networks: {
        'openclaw-enterprise_openclaw-net': { Aliases: ['openclaw-rag-service', 'rag-service'] },
      },
    },
  };
}
function liveFleetContainer() {
  const state = readState();
  return {
    Name: '/openclaw-fleet-gateway',
    Image: 'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
    State: { Running: true, Paused: state.fleetPaused },
    Config: { Image: 'fleet:baseline', Env: [], Cmd: ['node'], Entrypoint: null, User: 'node', WorkingDir: '/app' },
    HostConfig: { RestartPolicy: { Name: 'unless-stopped' }, PortBindings: {} },
    Mounts: [
      { Type: 'bind', Source: knowledgePath, Destination: '/app/knowledge', RW: true },
    ],
    NetworkSettings: { Networks: {} },
  };
}
function requestedContainer() {
  return args.some(arg => arg === 'openclaw-fleet-gateway') ? liveFleetContainer() : liveRagContainer();
}
if (args[0] === 'volume' && args[1] === 'inspect') {
  if (args.includes('--format')) {
    process.stdout.write(cachePath);
    process.exit(0);
  }
  process.stdout.write(JSON.stringify([{ Mountpoint: cachePath }]));
  process.exit(0);
}
if (args[0] === 'image' && args[1] === 'inspect') {
  process.stdout.write(JSON.stringify([imageInfo(args[2])]));
  process.exit(0);
}
if (args[0] === 'inspect') {
  if (args.includes('--format')) {
    const template = args[args.indexOf('--format') + 1];
    const live = requestedContainer();
    if (template.includes('.Config.Image')) process.stdout.write(live.Config.Image);
    else if (template.includes('.State.Running')) process.stdout.write(String(live.State.Running));
    if (template.includes('.State.Paused')) process.stdout.write(':' + String(live.State.Paused));
    else process.exit(2);
  } else {
    process.stdout.write(JSON.stringify([requestedContainer()]));
  }
  process.exit(0);
}
if (args[0] === 'container' && args[1] === 'inspect') {
  process.stdout.write(JSON.stringify([requestedContainer()]));
  process.exit(0);
}
if (args[0] === 'compose' && args.includes('config') && args.includes('--format')) {
  const includeEndpoint = args.some(arg => arg.endsWith('rag-hf-endpoint.override.yml'));
  process.stdout.write(JSON.stringify(renderedConfig(includeEndpoint)));
  process.exit(0);
}
if (args[0] === 'compose' && args.includes('up')) {
  if (!args.includes('--no-deps')) {
    process.stderr.write('rag release must not start dependencies\\n');
    process.exit(98);
  }
  const configPath = args[args.lastIndexOf('-f') + 1];
  if (!configPath.startsWith('/proc/')) {
    process.stderr.write('rendered config must be anonymous\n');
    process.exit(95);
  }
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const endpoint = Object.prototype.hasOwnProperty.call(config.services['rag-service'].environment, 'HF_ENDPOINT');
  const stage = endpoint ? 'target' : 'baseline';
  const current = readState();
  if (stage === 'target' && (!current.fleetPaused || current.ragRunning)) {
    process.stderr.write('writers were not quiesced before target mutation\n');
    process.exit(94);
  }
  writeState(stage, config.services['rag-service'].image);
  if (stage === 'target') {
    fs.writeFileSync(cachePath + '/target-marker.txt', 'target cache mutation\n');
    fs.writeFileSync(knowledgePath + '/target-marker.txt', 'target knowledge mutation\n');
  }
  fs.appendFileSync(operationsPath, 'up:' + stage + '\n');
  process.exit(0);
}
if (args[0] === 'rm' && args.includes('openclaw-rag-service')) {
  const state = readState();
  state.ragRunning = false;
  fs.writeFileSync(statePath, JSON.stringify(state));
  fs.appendFileSync(operationsPath, 'rm\n');
  process.exit(0);
}
if (args[0] === 'pause' && args[1] === 'openclaw-fleet-gateway') {
  const state = readState();
  state.fleetPaused = true;
  fs.writeFileSync(statePath, JSON.stringify(state));
  fs.appendFileSync(operationsPath, 'pause:fleet\n');
  process.exit(0);
}
if (args[0] === 'unpause' && args[1] === 'openclaw-fleet-gateway') {
  if (process.env.FAKE_UNPAUSE_FAILURE === 'true') {
    fs.appendFileSync(operationsPath, 'unpause:fleet:failed\n');
    process.exit(93);
  }
  const state = readState();
  state.fleetPaused = false;
  fs.writeFileSync(statePath, JSON.stringify(state));
  fs.appendFileSync(operationsPath, 'unpause:fleet\n');
  process.exit(0);
}
if (args[0] === 'stop' && args.at(-1) === 'openclaw-rag-service') {
  const state = readState();
  state.ragRunning = false;
  fs.writeFileSync(statePath, JSON.stringify(state));
  fs.appendFileSync(operationsPath, 'stop:rag\n');
  process.exit(0);
}
if (args[0] === 'start' && args[1] === 'openclaw-rag-service') {
  const state = readState();
  state.ragRunning = true;
  fs.writeFileSync(statePath, JSON.stringify(state));
  fs.appendFileSync(operationsPath, 'start:rag\n');
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
  const fakeRegression = '#!/usr/bin/env bash\nset -euo pipefail\nphase="${4:-unknown}"\nprintf \'regression:%s\\n\' "$phase" >> "$FAKE_OPERATIONS"\nif [[ "$phase" == after && "${FAKE_REGRESSION_FAIL_AFTER:-}" == true ]]; then exit 1; fi\n';
  writeExecutable(join(binDir, 'docker'), fakeDocker);
  writeExecutable(join(binDir, 'curl'), fakeCurl);
  writeExecutable(join(binDir, 'hostname'), fakeHostname);
  writeExecutable(join(binDir, 'id'), fakeId);
  writeExecutable(join(binDir, 'regression'), fakeRegression);
  return {
    root,
    composePath,
    statePath,
    operationsPath,
    dockerPath: join(binDir, 'docker'),
    curlPath: join(binDir, 'curl'),
    hostnamePath: join(binDir, 'hostname'),
    idPath: join(binDir, 'id'),
    regressionPath: join(binDir, 'regression'),
    cachePath,
    knowledgePath,
    backupRoot,
  };
}

function runRelease(fake, { execute = false, env: overrides = {} } = {}) {
  return spawnSync('bash', [releaseScript, execute ? '--execute' : '--check'], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: {
      ...process.env,
      RAG_SERVICE_IMAGE: targetImage,
      RAG_SOURCE_SHA: sourceSha,
      RAG_INFRA_SHA: 'ffffffffffffffffffffffffffffffffffffffff',
      RAG_BASE_COMPOSE: fake.composePath,
      RAG_RELEASE_OVERRIDE: endpointOverride,
      RAG_RELEASE_IMAGE_OVERRIDE: imageOnlyOverride,
      RAG_REGRESSION_SCRIPT: fake.regressionPath,
      RAG_STATE_BACKUP_ROOT: fake.backupRoot,
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
      FAKE_CACHE_PATH: fake.cachePath,
      FAKE_KNOWLEDGE_PATH: fake.knowledgePath,
      DOCKER_HOST: 'tcp://unexpected-daemon:2375',
      DOCKER_CONTEXT: 'unexpected-context',
      DOCKER_CONFIG: join(fake.root, 'unexpected-docker-config'),
      FAKE_REQUIRE_LOCAL_DOCKER_CONTEXT: 'true',
      ...overrides,
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
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), 'regression:before\n');
    assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /secret-rag-token|secret-db/);
  });
});

test('execute recreates only RAG with the target digest and endpoint contract', () => {
  withFake({}, (fake) => {
    const result = runRelease(fake, { execute: true });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /RESULT=changed/);
    assert.equal(
      readFileSync(fake.operationsPath, 'utf8'),
      'regression:before\npause:fleet\nstop:rag\nup:target\nunpause:fleet\nregression:after\n',
    );
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'target');
    const backupDirectory = join(fake.backupRoot, readdirSync(fake.backupRoot)[0]);
    const evidence = readFileSync(join(backupDirectory, 'release-evidence.env'), 'utf8');
    assert.match(evidence, new RegExp(`source_sha=${sourceSha}`));
    assert.match(evidence, /state_backup=verified/);
    assert.match(evidence, /pre_health=verified/);
    assert.match(evidence, /pre_regression=verified/);
    assert.match(evidence, /runtime_contract=verified/);
    assert.match(evidence, /post_health=verified/);
    assert.match(evidence, /post_regression=verified/);
    assert.ok(existsSync(join(backupDirectory, 'baseline-runtime.json')));
    assert.match(
      readFileSync(join(backupDirectory, 'checksums.sha256'), 'utf8'),
      /state-metadata\.env/,
    );
    assert.doesNotMatch(evidence, /secret-rag-token|secret-db/);
  });
});

test('check rejects a live RAG mount that is absent from the rendered Compose contract', () => {
  withFake({ extraMount: true }, (fake) => {
    const result = runRelease(fake);
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
  });
});

test('check rejects an unmodelled live security setting before a Compose mutation', () => {
  withFake({ extraSecurityOpt: true }, (fake) => {
    const result = runRelease(fake);
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
  });
});

test('execute rejects a target whose OCI provenance does not bind the selected source SHA', () => {
  withFake({ wrongTargetProvenance: true }, (fake) => {
    const result = runRelease(fake, { execute: true });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /provenance/);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
    assert.equal(existsSync(fake.backupRoot), false);
  });
});

test('execute rejects an invalid retry contract before a Compose mutation', () => {
  withFake({}, (fake) => {
    const result = runRelease(fake, {
      execute: true,
      env: { RAG_RELEASE_RETRIES: 'not-a-positive-integer' },
    });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /RAG_RELEASE_RETRIES/);
    assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
  });
});

test('execute rejects a backup root that overlaps live mutable state before quiescing writers', () => {
  withFake({}, (fake) => {
    for (const backupRoot of [
      fake.knowledgePath,
      join(fake.knowledgePath, 'nested-backups'),
      fake.root,
    ]) {
      const result = runRelease(fake, {
        execute: true,
        env: { RAG_STATE_BACKUP_ROOT: backupRoot },
      });
      assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
      assert.match(result.stderr, /must not overlap/);
      assert.equal(readFileSync(fake.operationsPath, 'utf8'), '');
      assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).fleetPaused, false);
    }
  });
});

test('a second check against the already-adopted immutable target is a non-mutating no-op', () => {
  withFake({}, (fake) => {
    const first = runRelease(fake, { execute: true });
    assert.equal(first.status, 0, `${first.stdout}\n${first.stderr}`);
    const second = runRelease(fake);
    assert.equal(second.status, 0, `${second.stdout}\n${second.stderr}`);
    assert.match(second.stdout, /RESULT=noop/);
    assert.equal(
      readFileSync(fake.operationsPath, 'utf8'),
      'regression:before\npause:fleet\nstop:rag\nup:target\nunpause:fleet\nregression:after\nregression:after\n',
    );
  });
});

test('execute rolls back when a host runtime field outside the rendered Compose shape drifts', () => {
  withFake({ targetRuntimeDrift: true }, (fake) => {
    const result = runRelease(fake, { execute: true });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /exact baseline restored/);
    assert.equal(
      readFileSync(fake.operationsPath, 'utf8'),
      'regression:before\npause:fleet\nstop:rag\nup:target\nrm\nup:baseline\nunpause:fleet\nregression:after\n',
    );
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'baseline');
  });
});

test('execute rolls back to the exact baseline if target readiness fails', () => {
  withFake({ targetHealthFailure: true }, (fake) => {
    const knowledgeInode = statSync(fake.knowledgePath).ino;
    const cacheInode = statSync(fake.cachePath).ino;
    const result = runRelease(fake, { execute: true });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /exact baseline restored/);
    assert.equal(
      readFileSync(fake.operationsPath, 'utf8'),
      'regression:before\npause:fleet\nstop:rag\nup:target\nrm\nup:baseline\nunpause:fleet\nregression:after\n',
    );
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'baseline');
    assert.equal(readFileSync(join(fake.cachePath, 'baseline-cache.txt'), 'utf8'), 'baseline cache\n');
    assert.equal(readFileSync(join(fake.knowledgePath, 'baseline-knowledge.txt'), 'utf8'), 'baseline knowledge\n');
    assert.equal(existsSync(join(fake.cachePath, 'target-marker.txt')), false);
    assert.equal(existsSync(join(fake.knowledgePath, 'target-marker.txt')), false);
    assert.equal(statSync(fake.knowledgePath).ino, knowledgeInode);
    assert.equal(statSync(fake.cachePath).ino, cacheInode);
    const backupDirectory = join(fake.backupRoot, readdirSync(fake.backupRoot)[0]);
    assert.ok(readdirSync(backupDirectory).some((entry) => entry.startsWith('cache_metadata-failed-')));
    assert.ok(readdirSync(backupDirectory).some((entry) => entry.startsWith('knowledge_metadata-failed-')));
    assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /secret-rag-token|secret-db/);
  });
});

test('a post-commit regression failure never overwrites state after Fleet resumes', () => {
  withFake({}, (fake) => {
    const result = runRelease(fake, {
      execute: true,
      env: { FAKE_REGRESSION_FAIL_AFTER: 'true' },
    });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /post-commit regression failed/);
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'target');
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).fleetPaused, false);
    assert.equal(existsSync(join(fake.knowledgePath, 'target-marker.txt')), true);
    assert.doesNotMatch(readFileSync(fake.operationsPath, 'utf8'), /rm|up:baseline/);
  });
});

test('a Fleet unpause failure propagates and cleanup retains enough state to retry', () => {
  withFake({}, (fake) => {
    const result = runRelease(fake, {
      execute: true,
      env: { FAKE_UNPAUSE_FAILURE: 'true' },
    });
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /Fleet could not resume/);
    assert.match(result.stderr, /cleanup could not resume the Fleet writer/);
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).stage, 'target');
    assert.equal(JSON.parse(readFileSync(fake.statePath, 'utf8')).fleetPaused, true);
    assert.equal(
      readFileSync(fake.operationsPath, 'utf8'),
      'regression:before\npause:fleet\nstop:rag\nup:target\nunpause:fleet:failed\nunpause:fleet:failed\n',
    );
  });
});
