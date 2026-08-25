#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const SERVICE_NAME = 'rag-service';

function usage() {
  process.stderr.write('Usage: check-mypc-rag-release-contract.mjs --mode transition|match --container <name> --target <rendered.json> [--baseline <rendered.json>] [--endpoint <https-url>]\n');
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
  try {
    return execFileSync(dockerBin, args, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
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
    environment: envMap(live.Config.Env),
    mounts: liveMounts(live.Mounts),
    networks: liveNetworks(live.NetworkSettings?.Networks),
    ports: livePorts(live.HostConfig.PortBindings),
  };
}

function assertLiveMatches(container, expected) {
  const actual = currentSpec(container);
  for (const key of Object.keys(expected)) assertEqual(key, actual[key], expected[key]);
}

function withoutEndpoint(spec) {
  const { HF_ENDPOINT: _endpoint, ...environment } = spec.environment;
  return { ...spec, imageRef: '', imageId: '', environment };
}

function assertTransition(baseline, target, endpoint) {
  if (baseline.environment.HF_ENDPOINT !== undefined) {
    fail('RAG baseline unexpectedly already defines the endpoint override');
  }
  if (target.environment.HF_ENDPOINT !== endpoint) {
    fail('RAG target endpoint does not match the versioned release contract');
  }
  assertEqual('transition shape', withoutEndpoint(target), withoutEndpoint(baseline));
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.mode || !args.container || !args.target) usage();
  const target = expectedSpec(parseRendered(args.target));

  if (args.mode === 'match') {
    if (args.endpoint && target.environment.HF_ENDPOINT !== args.endpoint) {
      fail('RAG target endpoint does not match the versioned release contract');
    }
    assertLiveMatches(args.container, target);
    process.stdout.write('RESULT=match\n');
    return;
  }

  if (args.mode === 'transition') {
    if (!args.baseline || !args.endpoint) usage();
    const baseline = expectedSpec(parseRendered(args.baseline));
    assertLiveMatches(args.container, baseline);
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
