import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("mypc-data-layer-regression.sh")


class MyPCDataLayerRegressionTest(unittest.TestCase):
    def make_fake_tools(self, root):
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
printf '%s|' "$@" >> "$DOCKER_ARGS_LOG"
if [ "${POSTGRES_PASSWORD+x}" = x ]; then
  printf 'present' > "$DOCKER_PASSWORD_PRESENT"
fi
if [ "$1" = inspect ]; then
  [ "${2:-}" = -f ] && echo true
  exit 0
fi
if [ "$1" = exec ]; then
  case "$*" in
    *pg_isready*) exit 0 ;;
    *information_schema.tables*) echo t; exit 0 ;;
    *pg_namespace*) echo 1; exit 0 ;;
  esac
fi
exit 1
"""
        )
        docker.chmod(0o755)
        curl = bin_dir / "curl"
        curl.write_text("#!/usr/bin/env bash\nprintf '200'\n")
        curl.chmod(0o755)
        return bin_dir

    def make_env(self, root, bin_dir):
        env = os.environ.copy()
        env.pop("POSTGRES_PASSWORD", None)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["DOCKER_ARGS_LOG"] = str(root / "docker-args")
        env["DOCKER_PASSWORD_PRESENT"] = str(root / "password-present")
        return env

    def test_postgres_reads_literal_values_without_executing_or_printing_secrets(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bin_dir = self.make_fake_tools(root)
            marker = root / "executed"
            postgres_user = f"fixture user $(touch {marker})"
            postgres_db = "fixture database"
            secret = "fixture-secret-must-not-print"
            dotenv = root / ".env"
            dotenv.write_text(
                f"# comment\n\nPOSTGRES_CONTAINER=fixture-postgres\n"
                f"POSTGRES_USER={postgres_user}\nPOSTGRES_DB={postgres_db}\n"
                f"POSTGRES_PASSWORD={secret}\n"
            )
            env = self.make_env(root, bin_dir)
            env["MYPC_DATA_LAYER_ENV_FILE"] = str(dotenv)
            completed = subprocess.run(
                ["bash", str(SCRIPT), "--service", "postgres"],
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )

            diagnostics = (completed.stdout + completed.stderr)
            for value in (postgres_user, postgres_db, secret):
                diagnostics = diagnostics.replace(value, "[fixture value]")
            self.assertEqual(completed.returncode, 0, diagnostics[-500:])
            self.assertFalse(marker.exists(), "dotenv value executed as shell")
            args = (root / "docker-args").read_bytes().split(b"|")
            self.assertIn(postgres_user.encode(), args)
            self.assertIn(postgres_db.encode(), args)
            self.assertIn(b"fixture-postgres", args)
            self.assertFalse((root / "password-present").exists())
            self.assertNotIn(secret, completed.stdout + completed.stderr)

    def test_litellm_does_not_open_dotenv(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bin_dir = self.make_fake_tools(root)
            dotenv_fifo = root / ".env-fifo"
            os.mkfifo(dotenv_fifo)
            env = self.make_env(root, bin_dir)
            env["MYPC_DATA_LAYER_ENV_FILE"] = str(dotenv_fifo)
            completed = subprocess.run(
                ["bash", str(SCRIPT), "--service", "litellm"],
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
