#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmodSync, readFileSync, writeFileSync } from 'node:fs';

const SERVICE_NAME = 'rag-service';

function usage() {
  process.stderr.write('Usage: check-mypc-rag-release-contract.mjs --mode baseline|provenance|state-paths|snapshot|preserve|transition|match --container <name> [--target <rendered.json>]\n');
  process.exit(2);
}

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) usage();
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) usage();
    args[key.slice(2)] = value;
    index += 1;
  }
  return args;
}

function fail(message) {
  throw new Error(message);
}

function docker(args) {
  const dockerBin = process.env.DOCKER_BIN || 'docker';
  const env = { ...process.env };
  delete env.DOCKER_HOST;
  delete env.DOCKER_CONTEXT;
  delete env.DOCKER_CONFIG;
  try {
    return execFileSync(dockerBin, args, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      env,
    });
  } catch {
    fail(`Docker command failed while reading the RAG ${args[0] ?? 'contract'}`);
  }
}

function parseDockerJson(args, label) {
  try {
    return JSON.parse(docker(args));
  } catch {
    fail(`Docker returned invalid ${label} data for the RAG contract`);
  }
}

function parseRendered(path) {
  let config;
  try {
    config = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    fail('RAG rendered Compose contract is unreadable');
  }
  const service = config.services?.[SERVICE_NAME];
  if (!service || typeof service.image !== 'string' || service.image.length === 0) {
    fail('RAG rendered Compose contract has no immutable image');
  }
  return { config, service };
}

function sorted(values) {
  return [...values].sort();
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

function stable(value) {
  return JSON.stringify(canonical(value));
}

function assertEqual(label, actual, expected) {
  if (stable(actual) !== stable(expected)) {
    fail(`RAG ${label} does not match the rendered production contract`);
  }
}

function envMap(entries) {
  const result = {};
  for (const entry of entries ?? []) {
    if (typeof entry !== 'string') continue;
    const separator = entry.indexOf('=');
    if (separator <= 0) continue;
    result[entry.slice(0, separator)] = entry.slice(separator + 1);
  }
  return result;
}

function composeEnvMap(environment) {
  if (Array.isArray(environment)) return envMap(environment);
  if (!environment || typeof environment !== 'object') return {};
  const result = {};
  for (const [key, value] of Object.entries(environment)) {
    if (value === null || value === undefined) fail('RAG rendered environment has an unresolved key');
    result[key] = String(value);
  }
  return result;
}

function imageInfo(reference) {
  const images = parseDockerJson(['image', 'inspect', reference], 'image inspection');
  const image = images[0];
  if (!image?.Id || !image.Config) fail('RAG image inspection is incomplete');
  return image;
}

function composeArray(value, fallback) {
  if (value === undefined || value === null) return fallback ?? null;
  if (Array.isArray(value)) return value;
  return [value];
}

function expectedMounts(volumes, config) {
  return sorted((volumes ?? []).map((volume) => {
    if (!volume || typeof volume !== 'object') fail('RAG rendered mount is invalid');
    const type = volume.type;
    const source = volume.type === 'volume'
      ? config.volumes?.[volume.source]?.name ?? volume.source
      : volume.source;
    const target = volume.target;
    if (!type || !source || !target) fail('RAG rendered mount is incomplete');
    return stable({ type, source, target, rw: volume.read_only !== true });
  }));
}

function liveMounts(mounts) {
  return sorted((mounts ?? []).map((mount) => {
    const source = mount.Type === 'volume' ? mount.Name : mount.Source;
    if (!mount.Type || !source || !mount.Destination) fail('RAG live mount inspection is incomplete');
    return stable({ type: mount.Type, source, target: mount.Destination, rw: mount.RW !== false });
  }));
}

function expectedNetworks(config, service) {
  const result = [];
  for (const [key, value] of Object.entries(service.networks ?? {})) {
    const network = config.networks?.[key] ?? {};
    const aliases = new Set([SERVICE_NAME, service.container_name ?? SERVICE_NAME]);
    for (const alias of value?.aliases ?? []) aliases.add(alias);
    result.push(stable({ name: network.name ?? key, aliases: sorted(aliases) }));
  }
  return sorted(result);
}

function liveNetworks(networks) {
  return sorted(Object.entries(networks ?? {}).map(([name, value]) => stable({
    name,
    aliases: sorted(value?.Aliases ?? []),
  })));
}

function expectedPorts(ports) {
  const result = [];
  for (const port of ports ?? []) {
    if (!port || typeof port !== 'object') fail('RAG rendered port is invalid');
    if (port.published === undefined || port.target === undefined) fail('RAG rendered port is incomplete');
    result.push(stable({
      containerPort: `${port.target}/${port.protocol ?? 'tcp'}`,
      hostIp: port.host_ip ?? '0.0.0.0',
      hostPort: String(port.published),
    }));
  }
  return sorted(result);
}

function livePorts(bindings) {
  const result = [];
  for (const [containerPort, values] of Object.entries(bindings ?? {})) {
    for (const value of values ?? []) {
      result.push(stable({
        containerPort,
        hostIp: value.HostIp ?? '0.0.0.0',
        hostPort: String(value.HostPort),
      }));
    }
  }
  return sorted(result);
}

function stringList(value, label) {
  if (value === undefined || value === null) return [];
  const values = Array.isArray(value) ? value : [value];
  if (values.some((entry) => typeof entry !== 'string')) fail(`RAG ${label} is invalid`);
  return sorted(values);
}

function exactInteger(value, label) {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return value;
  if (typeof value === 'string' && /^[0-9]+$/.test(value)) return Number(value);
  fail(`RAG ${label} must be an exact non-negative integer`);
}

function exactNanoCpus(value) {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    const result = value * 1_000_000_000;
    if (Number.isSafeInteger(result)) return result;
  }
  if (typeof value === 'string' && /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(value)) {
    const result = Number(value) * 1_000_000_000;
    if (Number.isSafeInteger(result)) return result;
  }
  fail('RAG rendered CPU limit is invalid');
}

function expectedLogConfig(logging) {
  if (!logging || typeof logging !== 'object' || typeof logging.driver !== 'string' || !logging.driver) {
    fail('RAG rendered logging contract is missing');
  }
  if (logging.options !== undefined && (!logging.options || typeof logging.options !== 'object')) {
    fail('RAG rendered logging options are invalid');
  }
  return {
    type: logging.driver,
    config: Object.fromEntries(Object.entries(logging.options ?? {})
      .map(([key, value]) => [key, String(value)])),
  };
}

function liveLogConfig(logging) {
  return {
    type: logging?.Type ?? '',
    config: Object.fromEntries(Object.entries(logging?.Config ?? {})
      .map(([key, value]) => [key, String(value)])),
  };
}

function expectedSpec(rendered) {
  const { config, service } = rendered;
  const image = imageInfo(service.image);
  const environment = {
    ...envMap(image.Config.Env),
    ...composeEnvMap(service.environment),
  };
  return {
    imageRef: service.image,
    imageId: image.Id,
    containerName: service.container_name ?? SERVICE_NAME,
    command: composeArray(service.command, image.Config.Cmd),
    entrypoint: composeArray(service.entrypoint, image.Config.Entrypoint),
    user: service.user ?? image.Config.User ?? '',
    workingDir: service.working_dir ?? image.Config.WorkingDir ?? '',
    restart: service.restart ?? '',
    readOnly: service.read_only === true,
    privileged: service.privileged === true,
    logConfig: expectedLogConfig(service.logging),
    memory: exactInteger(service.mem_limit, 'rendered memory limit'),
    memorySwap: exactInteger(service.memswap_limit, 'rendered memory swap limit'),
    nanoCpus: exactNanoCpus(service.cpus),
    capAdd: stringList(service.cap_add, 'rendered added capabilities'),
    capDrop: stringList(service.cap_drop, 'rendered dropped capabilities'),
    securityOpt: stringList(service.security_opt, 'rendered security options'),
    usernsMode: service.userns_mode ?? '',
    pidMode: service.pid ?? '',
    environment,
    mounts: expectedMounts(service.volumes, config),
    networks: expectedNetworks(config, service),
    ports: expectedPorts(service.ports),
  };
}

function currentSpec(container) {
  const live = parseDockerJson(['inspect', container], 'container inspection')[0];
  if (!live?.Config || !live?.HostConfig || !live?.State || !live.State.Running) {
    fail('RAG container is not running with a complete production contract');
  }
  return {
    imageRef: live.Config.Image,
    imageId: live.Image,
    containerName: String(live.Name ?? '').replace(/^\//, ''),
    command: live.Config.Cmd ?? null,
    entrypoint: live.Config.Entrypoint ?? null,
    user: live.Config.User ?? '',
    workingDir: live.Config.WorkingDir ?? '',
    restart: live.HostConfig.RestartPolicy?.Name ?? '',
    readOnly: live.HostConfig.ReadonlyRootfs === true,
    privileged: live.HostConfig.Privileged === true,
    logConfig: liveLogConfig(live.HostConfig.LogConfig),
    memory: live.HostConfig.Memory ?? 0,
    memorySwap: live.HostConfig.MemorySwap ?? 0,
    nanoCpus: live.HostConfig.NanoCpus ?? 0,
    capAdd: stringList(live.HostConfig.CapAdd, 'live added capabilities'),
    capDrop: stringList(live.HostConfig.CapDrop, 'live dropped capabilities'),
    securityOpt: stringList(live.HostConfig.SecurityOpt, 'live security options'),
    usernsMode: live.HostConfig.UsernsMode ?? '',
    pidMode: live.HostConfig.PidMode ?? '',
    environment: envMap(live.Config.Env),
    mounts: liveMounts(live.Mounts),
    networks: liveNetworks(live.NetworkSettings?.Networks),
    ports: livePorts(live.HostConfig.PortBindings),
  };
}

function currentContainer(container) {
  const live = parseDockerJson(['inspect', container], 'container inspection')[0];
  if (!live?.Config || !live?.HostConfig || !live?.State || !live.State.Running || live.State.Paused) {
    fail('RAG container is not running with a complete production contract');
  }
  return live;
}

function hashValue(value) {
  return createHash('sha256').update(value).digest('hex');
}

function hashedEnvironment(entries) {
  return Object.fromEntries(Object.entries(envMap(entries))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => [key, hashValue(value)]));
}

function runtimePreservationSpec(live) {
  return {
    containerName: String(live.Name ?? '').replace(/^\//, ''),
    command: live.Config.Cmd ?? null,
    entrypoint: live.Config.Entrypoint ?? null,
    user: live.Config.User ?? '',
    workingDir: live.Config.WorkingDir ?? '',
    environment: hashedEnvironment(live.Config.Env),
    mounts: liveMounts(live.Mounts),
    networks: liveNetworks(live.NetworkSettings?.Networks),
    ports: livePorts(live.HostConfig.PortBindings),
    hostConfig: live.HostConfig,
    healthcheck: live.Config.Healthcheck ?? null,
    stopSignal: live.Config.StopSignal ?? '',
  };
}

function withoutEndpointHash(spec) {
  const { HF_ENDPOINT: _endpoint, ...environment } = spec.environment;
  return { ...spec, environment };
}

function immutableRagDigest(image) {
  const digest = (image.RepoDigests ?? []).find((candidate) => (
    /^127\.0\.0\.1:8082\/rag-service@sha256:[0-9a-f]{64}$/.test(candidate)
  ));
  if (!digest) fail('RAG image has no local Nexus immutable digest');
  return digest;
}

function assertSourceProvenance(reference, sourceSha) {
  if (!/^[0-9a-f]{40}$/.test(sourceSha)) {
    fail('RAG source SHA must be a full lowercase Git SHA');
  }
  if (!/^127\.0\.0\.1:8082\/rag-service@sha256:[0-9a-f]{64}$/.test(reference)) {
    fail('RAG target image must be a local Nexus immutable digest reference');
  }
  const image = imageInfo(reference);
  if (!(image.RepoDigests ?? []).includes(reference)) {
    fail('RAG target digest is not locally bound to its inspected image');
  }
  if (image.Config.Labels?.['org.opencontainers.image.revision'] !== sourceSha) {
    fail('RAG target image provenance does not match the requested VectA source SHA');
  }
  if (image.Config.Labels?.['com.vecta.source.repository'] !== 'ZenoWangzy/vecta') {
    fail('RAG target image provenance does not name the VectA source repository');
  }
  return image;
}

function statePaths(container, fleetContainer) {
  const live = currentContainer(container);
  const fleet = currentContainer(fleetContainer);
  const cache = (live.Mounts ?? []).find((mount) => (
    mount.Type === 'volume'
    && mount.Destination === '/home/node/.cache/huggingface'
    && mount.RW === true
  ));
  const knowledge = (live.Mounts ?? []).find((mount) => (
    mount.Type === 'bind'
    && mount.Destination === '/app/knowledge'
    && mount.RW === true
  ));
  if (!cache?.Name || !knowledge?.Source) {
    fail('RAG mutable state mounts are incomplete');
  }
  const fleetKnowledge = (fleet.Mounts ?? []).find((mount) => (
    mount.Type === 'bind'
    && mount.Destination === '/app/knowledge'
    && mount.RW === true
  ));
  if (!fleetKnowledge?.Source || fleetKnowledge.Source !== knowledge.Source) {
    fail('Fleet and RAG do not share the same writable knowledge bind');
  }
  return { cacheVolume: cache.Name, knowledgePath: knowledge.Source };
}

function assertLiveMatches(container, expected, { allowEquivalentImageReference = false } = {}) {
  const actual = currentSpec(container);
  for (const key of Object.keys(expected)) {
    if (allowEquivalentImageReference && key === 'imageRef') continue;
    assertEqual(key, actual[key], expected[key]);
  }
}

function withoutEndpoint(spec) {
  const { HF_ENDPOINT: _endpoint, ...environment } = spec.environment;
  return { ...spec, imageRef: '', imageId: '', environment };
}

function assertTransition(baseline, target, endpoint) {
  if (
    baseline.environment.HF_ENDPOINT !== undefined
    && baseline.environment.HF_ENDPOINT !== endpoint
  ) {
    fail('RAG baseline endpoint does not match the versioned release contract');
  }
  if (target.environment.HF_ENDPOINT !== endpoint) {
    fail('RAG target endpoint does not match the versioned release contract');
  }
  assertEqual('transition shape', withoutEndpoint(target), withoutEndpoint(baseline));
}

function assertRuntimePreserved(container, snapshot) {
  const actual = withoutEndpointHash(runtimePreservationSpec(currentContainer(container)));
  const expected = withoutEndpointHash(snapshot);
  assertEqual('preserved runtime contract', actual, expected);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.mode || !args.container) usage();

  if (args.mode === 'baseline') {
    const live = currentContainer(args.container);
    const image = imageInfo(live.Image);
    const imageRef = immutableRagDigest(image);
    if (image.Id !== live.Image) {
      fail('RAG baseline image ID does not match the running container');
    }
    process.stdout.write(`RESULT=baseline\nIMAGE_REF=${imageRef}\nIMAGE_ID=${image.Id}\n`);
    return;
  }

  if (args.mode === 'provenance') {
    if (!args.image || !args['source-sha']) usage();
    const image = assertSourceProvenance(args.image, args['source-sha']);
    process.stdout.write(`RESULT=provenance\nIMAGE_ID=${image.Id}\n`);
    return;
  }

  if (args.mode === 'state-paths') {
    if (!args['fleet-container']) usage();
    const paths = statePaths(args.container, args['fleet-container']);
    process.stdout.write(`RESULT=state-paths\nCACHE_VOLUME=${paths.cacheVolume}\nKNOWLEDGE_PATH=${paths.knowledgePath}\n`);
    return;
  }

  if (args.mode === 'snapshot') {
    if (!args.output) usage();
    const snapshot = runtimePreservationSpec(currentContainer(args.container));
    writeFileSync(args.output, `${JSON.stringify(snapshot)}\n`, { mode: 0o600 });
    chmodSync(args.output, 0o600);
    process.stdout.write('RESULT=snapshot\n');
    return;
  }

  if (args.mode === 'preserve') {
    if (!args['baseline-runtime']) usage();
    let snapshot;
    try {
      snapshot = JSON.parse(readFileSync(args['baseline-runtime'], 'utf8'));
    } catch {
      fail('RAG baseline runtime snapshot is unreadable');
    }
    assertRuntimePreserved(args.container, snapshot);
    process.stdout.write('RESULT=preserved\n');
    return;
  }

  if (!args.target) usage();
  const target = expectedSpec(parseRendered(args.target));

  if (args.mode === 'match') {
    if (args.endpoint && target.environment.HF_ENDPOINT !== args.endpoint) {
      fail('RAG target endpoint does not match the versioned release contract');
    }
    assertLiveMatches(args.container, target, {
      allowEquivalentImageReference: args['allow-equivalent-image-reference'] === 'true',
    });
    process.stdout.write('RESULT=match\n');
    return;
  }

  if (args.mode === 'transition') {
    if (!args.baseline || !args.endpoint) usage();
    const baseline = expectedSpec(parseRendered(args.baseline));
    // A pre-existing container may still name an immutable full-SHA tag even
    // though its locally inspected image is pinned by a Nexus digest. Its image
    // ID remains mandatory; only that legacy reference spelling is equivalent.
    assertLiveMatches(args.container, baseline, { allowEquivalentImageReference: true });
    assertTransition(baseline, target, args.endpoint);
    process.stdout.write('RESULT=ready\n');
    return;
  }

  usage();
}

try {
  main();
} catch (error) {
  const message = error instanceof Error ? error.message : 'unknown RAG release contract failure';
  process.stderr.write(`FAIL: ${message}\n`);
  process.exit(1);
}
