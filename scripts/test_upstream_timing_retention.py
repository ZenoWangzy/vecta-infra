#!/usr/bin/env python3
"""Exercise real shell rotation offline, with Docker/nginx as local stand-ins."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)
    log = work / 'timing.log'
    source = (root / 'scripts/vecta-upstream-timing-rotate.sh').read_text()
    script = work / 'rotate'
    script.write_text(source.replace('/var/log/nginx/vecta-upstream-timing.log', str(log))
                      .replace('/run/lock/vecta-upstream-timing-rotate.lock', str(work / 'lock')))
    docker = work / 'docker'
    docker.write_text('''#!/bin/sh
if [ "$1" = inspect ]; then echo true; exit; fi
shift 2
exec "$@"
''')
    nginx = work / 'nginx'
    nginx.write_text(f'''#!/bin/sh
[ "$*" = '-s reopen' ] || exit 2
[ ! -e '{work}/fail' ] || exit 1
echo reopen >> '{work}/reopens'
: > '{log}'
''')
    docker.chmod(0o755)
    nginx.chmod(0o755)
    env = dict(os.environ, PATH=str(work) + ':' + os.environ['PATH'])
    def run(*args, success=True):
        result = subprocess.run(['sh', str(script), *args], env=env, check=False)
        assert (result.returncode == 0) == success, result.returncode

    log.write_text('recent chunk\n')
    run()
    assert log.read_text() == 'recent chunk\n' and not (work / 'reopens').exists()
    run('--force')
    assert log.read_text() == '' and Path(str(log) + '.1').read_text() == 'recent chunk\n'
    with log.open('wb') as stream:
        stream.truncate(33554432)
    run()
    assert Path(str(log) + '.1').stat().st_size == 33554432 and log.stat().st_size == 0
    for i in range(8):
        log.write_text(str(i))
        run('--force')
    assert len(list(work.glob('timing.log.*'))) == 5
    assert Path(str(log) + '.1').read_text() == '7'
    assert Path(str(log) + '.5').read_text() == '3'
    (work / 'fail').touch()
    log.write_text('still active')
    run('--force', success=False)
    assert log.read_text() == 'still active'
    (work / 'fail').unlink()
    log.unlink()
    run()
    assert log.exists()
    run('--bad-option', success=False)
print('PASS: threshold, forced rotate, five archives, reopen, retry after failure/restart')
