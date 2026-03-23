import pytest
import os
import subprocess
import time
import shutil
import re
import sys
import pathlib

from pathlib import Path

RETICULUM_CONFIG = """
[reticulum]
  share_instance = Yes

[interfaces]
  [[AutoInterface]]
    type = AutoInterface
    enabled = yes
"""


def _run_stack(tmp_path: Path, stdin: str) -> str:
    if not shutil.which("rnsd"):
        pytest.skip("rnsd binary not found in PATH")

    venv_python: str = sys.executable
    venv_bin: Path = pathlib.Path(venv_python).parent
    rngit_bin: str = str(venv_bin / "rngit")
    git_remote_rns_bin: str = str(venv_bin / "git-remote-rns")
    rnsd: str = str(venv_bin / "rnsd")

    if not pathlib.Path(rngit_bin).exists():
        pytest.skip("rngit not installed in venv")

    rns_config_dir: Path = tmp_path / "rns"
    rns_config_dir.mkdir()
    repo_dir: Path = tmp_path / "repo"
    repo_dir.mkdir()

    _ = subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    with open(repo_dir / "test.txt", "w") as f:
        _ = f.write("hello")
    _ = subprocess.run(
        ["git", "add", "."], cwd=repo_dir, capture_output=True, check=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    rns_config: Path = rns_config_dir / "config"
    _ = rns_config.write_text(RETICULUM_CONFIG)

    identity_file: Path = tmp_path / "identity"
    workdir: Path = pathlib.Path.cwd()

    rnsd_proc: subprocess.Popen[str] = subprocess.Popen(
        [rnsd, "--config", str(rns_config_dir), "-v"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(workdir),
    )

    # Check if rnsd started successfully
    time.sleep(1)
    if rnsd_proc.poll() is not None:
        out = rnsd_proc.stdout.read().strip() if rnsd_proc.stdout else ""
        err = rnsd_proc.stderr.read().strip() if rnsd_proc.stderr else ""
        pytest.skip(
            f"rnsd failed to start ({rnsd_proc.returncode}). stdout: {out!r}, stderr: {err!r}"
        )

    def wait_for_rns_ready(timeout: float = 15) -> bool:
        import subprocess as subp

        time.sleep(2)  # Give rnsd time to start
        start = time.time()
        while time.time() - start < timeout:
            result = subp.run(
                ["rnstatus", "--config", str(rns_config_dir), "-a"],
                capture_output=True,
                text=True,
            )
            if "Shared Instance" in result.stdout and "Up" in result.stdout:
                return True
            time.sleep(0.5)
        # Print debug info on failure
        result = subp.run(
            ["rnstatus", "--config", str(rns_config_dir), "-a"],
            capture_output=True,
            text=True,
        )
        print(f"rnstatus stdout: {result.stdout}")
        print(f"rnstatus stderr: {result.stderr}")
        return False

    rns_ready = wait_for_rns_ready()
    print(f"RNS ready: {rns_ready}")

    if not rns_ready:
        rnsd_proc.terminate()
        pytest.skip("RNS shared instance failed to start")

    server_proc = subprocess.Popen(
        [
            rngit_bin,
            str(repo_dir),
            "--config",
            str(rns_config_dir),
            "--save-identity",
            str(identity_file),
            "--announce-interval",
            "1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(workdir),
        env={**os.environ, "RNS_CONFIG_PATH": str(rns_config_dir)},
    )

    dest_hash = None
    try:
        assert server_proc.stdout is not None, "Server stdout is None"

        print("Waiting for rngit output...")
        import select

        while True:
            ready, _, _ = select.select([server_proc.stdout], [], [], 5)  # type: ignore[assignment]
            if ready:
                line = server_proc.stdout.readline()
                if not line:
                    print("rngit stdout closed")
                    break

                print(f"SERVER: {line.rstrip()}")
                match = re.search(r"Server destination hash:\s*([a-f0-9]+)", line)
                if match:
                    dest_hash = match.group(1)
                    break

                if "error" in line.lower():
                    assert False, f"Server error: {line}"

            else:
                print("Timeout waiting for rngit output")
                break

        if server_proc.poll() is not None and dest_hash is None:
            stderr_data = server_proc.stderr.read() if server_proc.stderr else ""
            print(f"rngit exited early with code {server_proc.returncode}")
            print(f"stderr: {stderr_data}")
            assert False, f"rngit exited early with code {server_proc.returncode}"

        assert dest_hash is not None, (
            "Could not get destination hash from server. rngit output above."
        )
        assert len(dest_hash) == 32, f"Invalid destination hash length: {dest_hash}"

        time.sleep(2)

        try:
            result = subprocess.run(
                [git_remote_rns_bin, "origin", f"rns::{dest_hash}"],
                env={**os.environ, "RNS_CONFIG_PATH": str(rns_config_dir)},
                input=stdin,
                capture_output=True,
                text=True,
                timeout=30,
            )
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            output = result.stdout + (result.stderr or "")

            if result.returncode != 0:
                print(f"Client exit code: {result.returncode}")

            if "timeout" in output.lower() or "failed to connect" in output.lower() or "error" in output.lower():
                print(f"Client output: {output[:500]}")
                assert False, (
                    "Client failed to connect to server. "
                    f"Server hash: {dest_hash}. "
                    f"Output: {output}"
                )

            return output

        except subprocess.TimeoutExpired as e:
            print(f"Client timed out!")
            print(f"stdout: {e.stdout}")
            print(f"stderr: {e.stderr}")
            raise

    finally:
        server_proc.terminate()
        try:
            _ = server_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            server_proc.kill()
            _ = server_proc.wait()

        rnsd_proc.terminate()
        try:
            _ = rnsd_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            rnsd_proc.kill()
            _ = rnsd_proc.wait()


class TestEndToEnd:
    def test_capabilities(self, tmp_path: Path) -> None:
        output = _run_stack(tmp_path, "capabilities\n\n")
        assert "connect" in output, f"Expected 'connect' capability, got: {output}"

    def test_list(self, tmp_path: Path) -> None:
        output = _run_stack(tmp_path, "list\n\n")
        assert "refs/heads" in output, f"Expected refs/heads in output, got: {output}"
        assert "HEAD" in output, f"Expected HEAD in output, got: {output}"
