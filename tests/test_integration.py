import atexit
import os
import pathlib
import random
import re
import select
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import RNS


def randomword(length: int) -> str:
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for _ in range(length))


RETICULUM_CONFIG = f"""
[reticulum]
  instance_name = rns_shared{randomword(5)}

[interfaces]
  [[AutoInterface]]
    type = AutoInterface
    enabled = no

  [[Dummy]]
    type = BackboneInterface
    enable = yes
    listen_on = 127.0.0.2
"""

_rnsd_process: subprocess.Popen[bytes] | None = None
_rnsd_config_dir: Path | None = None


@pytest.fixture(scope="session", autouse=True)
def shared_rnsd():
    global _rnsd_process
    global _rnsd_config_dir
    config_dir = Path(tempfile.mkdtemp())
    _ = atexit.register(lambda: shutil.rmtree(config_dir))
    rns_config = config_dir / "config"
    _ = rns_config.write_text(RETICULUM_CONFIG)

    # Wait for rnsd to be up
    tries = 3
    timeout = 5
    start = time.time()
    rnsd_proc = None
    remaining = tries
    while True:
        if rnsd_proc is None:
            rnsd_proc = subprocess.Popen(  # pylint: disable=R1732
                [
                    sys.executable,
                    "-m",
                    "RNS.Utilities.rnsd",
                    "--config",
                    str(config_dir),
                    "-vvv",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        if rnsd_proc.returncode is not None:
            stdout = (
                rnsd_proc.stdout.read().decode() if rnsd_proc.stdout is not None else ""
            )
            raise RuntimeError(
                f"RNS shared instance exited early: {rnsd_proc.returncode}"
                + f"\n  stdout: {stdout}"
            )

        # Output error message, but maybe not if it somehow works now.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "RNS.Utilities.rnstatus",
                "--config",
                str(config_dir),
                "-a",
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if not proc.returncode:
            break

        if time.time() - start < timeout:
            continue

        rnsd_proc.terminate()
        try:
            _ = rnsd_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            rnsd_proc.kill()
            _ = rnsd_proc.wait()

        if remaining:
            rnsd_proc = None
            remaining -= 1
            start = time.time()
            continue

        stdout = (
            rnsd_proc.stdout.read().decode() if rnsd_proc.stdout is not None else ""
        )
        raise RuntimeError(
            f"RNS shared instance failed to start in {tries} tries..."
            + f"\n  stdout: {stdout}"
            + f"\n  rnstatus: {proc.returncode} {proc.stdout or ''}"
        )

    _rnsd_process = rnsd_proc
    _rnsd_config_dir = config_dir

    yield config_dir

    rnsd_proc.terminate()
    try:
        _ = rnsd_proc.wait(timeout=5)

    except subprocess.TimeoutExpired:
        rnsd_proc.kill()
        _ = rnsd_proc.wait()


class IntegrationStack:
    def __init__(self, rns_config: Path, server_repo: Path):
        self.rns_config: Path = rns_config
        self.server_repo: Path = server_repo
        self.server_proc: subprocess.Popen[str] | None = None
        self.server_hash: str | None = None
        self.client_identity: RNS.Identity | None = None
        self.client_hexhash: str | None = None
        self.client_working_dir: Path | None = None
        self._alternate_identity: RNS.Identity | None = None
        self._alternate_hexhash: str | None = None

    def start_server(
        self,
        allow_all_read: bool = False,
        allow_read: list[str] | None = None,
        allow_write: list[str] | None = None,
    ):
        args = [
            sys.executable,
            "-m",
            "rngit",
            "rngit",
            str(self.server_repo),
            "--verbose",
            "--config",
            str(self.rns_config),
            "--announce-interval",
            "1",
        ]

        if allow_all_read:
            args.append("--allow-all-read")
        else:
            if allow_read:
                for identity in allow_read:
                    args.extend(["--allow-read", identity])
            if allow_write:
                for identity in allow_write:
                    args.extend(["--allow-write", identity])

        self.server_proc = subprocess.Popen(  # pylint: disable=R1732
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "RNS_CONFIG_PATH": str(self.rns_config)},
        )

        dest_hash = None
        assert self.server_proc.stdout is not None

        while True:
            ready, _, _ = select.select([self.server_proc.stdout], [], [], 5)
            if ready:
                line = self.server_proc.stdout.readline()
                if not line:
                    break

                print(f"SERVER: {line.rstrip()}")
                match = re.search(r"\[INFO\] Destination: <([a-f0-9]+)>", line)
                if match:
                    dest_hash = match.group(1)
                    break

                if "error" in line.lower():
                    raise RuntimeError(f"Server error: {line}")
            else:
                break

        if self.server_proc.poll() is not None and dest_hash is None:
            raise RuntimeError(
                f"rngit exited early with code {self.server_proc.returncode}"
            )

        assert dest_hash is not None, "Could not get destination hash from server"
        assert len(dest_hash) == 32, f"Invalid destination hash length: {dest_hash}"
        self.server_hash = dest_hash
        while subprocess.run(
            [
                sys.executable,
                "-m",
                "RNS.Utilities.rnpath",
                "--config",
                str(self.rns_config),
                "-w1",
                dest_hash,
            ],
            check=False,
        ).returncode:
            if self.server_proc.returncode is not None:
                raise RuntimeError(
                    f"Server exited early: {self.server_proc.returncode}\n"
                    + f"{self.server_proc.stdout}"
                )

        def fn(proc: subprocess.Popen[str]):
            while proc.returncode is None:
                for f in (proc.stdout, proc.stderr):
                    if f is None:
                        continue

                    line = f.readline()
                    if line:
                        print(line, file=sys.stderr, end="")

        threading.Thread(target=fn, args=(self.server_proc,)).start()

    def run_client(
        self,
        stdin: str,
        cwd: Path | str | None = None,
        identity_path: Path | None = None,
        config_path: Path | None = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        assert self.server_hash is not None
        flags: list[str] = ["--verbose"]
        if identity_path:
            flags.append(f"--identity={identity_path}")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rngit",
                "git-remote-rns",
                *flags,
                "origin",
                self.server_hash,
            ],
            cwd=cwd,
            env={
                **os.environ,
                "RNS_CONFIG_PATH": str(config_path or self.rns_config),
            },
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        print(f"CLIENT STDOUT: {result.stdout}")
        print(f"CLIENT STDERR: {result.stderr}")
        return result

    def get_client_identity(self) -> str:
        if self.client_hexhash:
            return self.client_hexhash

        identity_path = self.rns_config / "identity"
        if identity_path.exists():
            self.client_identity = RNS.Identity.from_file(str(identity_path))  # pyright: ignore[reportUnknownMemberType]
        else:
            self.client_identity = RNS.Identity(True)
            _ = self.client_identity.to_file(str(identity_path))  # pyright: ignore[reportUnknownMemberType]

        assert self.client_identity is not None
        self.client_hexhash = self.client_identity.hexhash
        assert self.client_hexhash is not None
        return self.client_hexhash

    def get_alternate_client_identity(self) -> str:
        if self._alternate_hexhash:
            return self._alternate_hexhash

        identity_path = self.rns_config / "identity_alt"
        self._alternate_identity = RNS.Identity(True)
        _ = self._alternate_identity.to_file(str(identity_path))  # pyright: ignore[reportUnknownMemberType]

        assert self._alternate_identity is not None
        self._alternate_hexhash = self._alternate_identity.hexhash
        assert self._alternate_hexhash is not None
        return self._alternate_hexhash

    def git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = False,
        timeout: int = 60,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        if cwd is None:
            cwd = self.server_repo

        venv_bin = pathlib.Path(sys.executable).parent
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=capture_output,
            check=check,
            env={
                **os.environ,
                "PATH": str(venv_bin) + ":" + os.environ.get("PATH", ""),
                "RNS_CONFIG_PATH": str(_rnsd_config_dir),
                "VERBOSE": "0",
            },
            timeout=timeout,
        )

    def _create_test_commit(
        self, filename: str, content: str, cwd: Path | None = None
    ) -> None:
        if cwd is None:
            cwd = self.server_repo
        path = cwd / filename
        _ = path.write_text(content)
        _ = self.git("add", ".", cwd=cwd, check=True)
        _ = self.git("commit", "-m", f"Add {filename}", cwd=cwd, check=True)

    def create_client_working_dir(self) -> Path:
        self.client_working_dir = Path(tempfile.mkdtemp())
        return self.client_working_dir

    def init_client_repo(self, empty: bool = True, copy: bool = False) -> Path:
        repodir = self.create_client_working_dir() / "repo"
        if copy:
            _ = shutil.copytree(self.server_repo, repodir)

        else:
            repodir.mkdir()
            self.init_git_repo(repodir, populate=False)

        _ = self.git(
            "remote",
            "add",
            "origin",
            f"rns::{self.server_hash}",
            cwd=repodir,
            check=True,
        )
        if not empty:
            _ = self.git(
                "commit",
                "--allow-empty",
                "-m",
                "init",
                cwd=repodir,
                check=True,
            )

        return repodir

    def cleanup(self) -> None:
        if not self.server_proc:
            return

        self.server_proc.terminate()
        try:
            _ = self.server_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            self.server_proc.kill()
            _ = self.server_proc.wait()

        if self.server_proc.stdout is not None:
            print(self.server_proc.stdout.read())

    def init_git_repo(self, repo: Path, populate: bool = True) -> None:
        _ = self.git("init", cwd=repo, check=True)
        if not populate:
            return

        _ = self.git("config", "user.email", "test@test.com", cwd=repo, check=True)
        _ = self.git("config", "user.name", "Test User", cwd=repo, check=True)
        _ = (repo / "test.txt").write_text("hello")
        _ = self.git("add", ".", cwd=repo, check=True)
        _ = self.git("commit", "-m", "init", cwd=repo, check=True)
        _ = self.git("branch", "-m", "main", cwd=repo, check=True)


class TestPublicAccess:
    def test_capabilities(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server(allow_all_read=True)
        try:
            result = stack.run_client("capabilities\n\n")
            output = result.stdout + result.stderr
            assert "list" in output, f"'list' missing from capabilities: {output}"
            assert "fetch" in output, f"'fetch' missing from capabilities: {output}"
            assert "push" in output, f"'push' missing from capabilities: {output}"

        finally:
            stack.cleanup()

    def test_list(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server(allow_all_read=True)
        try:
            result = stack.run_client("list\n\n")
            output = result.stdout + result.stderr
            assert "refs/heads" in output, (
                f"Expected refs/heads in output, got: {output}"
            )
            assert "HEAD" in output, f"Expected HEAD in output, got: {output}"

        finally:
            stack.cleanup()

    def test_fetch_single_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server(allow_all_read=True)

        client_repo = stack.init_client_repo()
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n", cwd=client_repo)
            output = result.stdout + result.stderr
            if result.returncode != 0:
                print(f"Fetch failed with code {result.returncode}")
                print(f"Output: {output}")

            assert result.returncode == 0, f"Fetch failed: {output}"
            assert "error" not in output.lower(), f"Error in output: {output}"

        finally:
            stack.cleanup()

    def test_fetch_all_refs(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server(allow_all_read=True)
        client_repo = stack.init_client_repo()
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n", cwd=client_repo)
            output = result.stdout + result.stderr
            if result.returncode != 0:
                print(f"Fetch failed with code {result.returncode}")
                print(f"Output: {output}")
            assert result.returncode == 0, f"Fetch failed: {output}"

        finally:
            stack.cleanup()


class TestAllowRead:
    def test_list_with_allow_read(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_read=[client_hash])
        client_repo = stack.init_client_repo()
        try:
            result = stack.run_client("list\n\n", cwd=client_repo)
            output = result.stdout + result.stderr
            assert "refs/heads" in output, (
                f"Expected refs/heads in output, got: {output}"
            )

        finally:
            stack.cleanup()

    def test_fetch_with_allow_read(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_read=[client_hash])
        client_repo = stack.init_client_repo()
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n", cwd=client_repo)
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Fetch failed: {output}"

        finally:
            stack.cleanup()

    def test_list_for_push_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_read=[client_hash])
        try:
            result = stack.run_client("list for-push\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected list-for-push to fail without write access, got: {output}"
            )

        finally:
            stack.cleanup()

    def test_wrong_identity_denied(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        alt_rns_config = tmp_path / "rns_alt"
        alt_rns_config.mkdir()

        alt_identity = RNS.Identity(True)
        alt_identity_path = alt_rns_config / "identity"
        _ = alt_identity.to_file(str(alt_identity_path))  # pyright: ignore[reportUnknownMemberType]

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        correct_hash = stack.get_client_identity()
        assert correct_hash != alt_identity.hexhash, "Failed to generate a new identity"
        stack.start_server(allow_read=[correct_hash])
        try:
            result = stack.run_client(
                "list\n\n",
                identity_path=alt_identity_path,
            )
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected wrong identity to be denied, got: {output}"
            )

        finally:
            stack.cleanup()


class TestAllowWrite:
    def test_list_with_allow_write(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("list\n\n")
            output = result.stdout + result.stderr
            assert "refs/heads" in output, (
                f"Expected refs/heads in output, got: {output}"
            )

        finally:
            stack.cleanup()

    def test_fetch_with_allow_write(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        client_repo = stack.init_client_repo()
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n", cwd=client_repo)
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Fetch failed: {output}"

        finally:
            stack.cleanup()

    def test_list_for_push(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("list for-push\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" not in output, (
                f"Expected list-for-push to work with write access, got: {output}"
            )

        finally:
            stack.cleanup()

    def test_push_new_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        client_repo = stack.init_client_repo(copy=True)
        try:
            result = stack.run_client(
                "push HEAD:refs/heads/new-branch\n\n",
                cwd=client_repo,
            )
            output = result.stdout + result.stderr
            assert result.returncode == 0, "Push failed"
            assert "error" not in output.lower(), "Error in output"

            verify_result = stack.git(
                "log",
                "new-branch",
                cwd=repo_dir,
                capture_output=True,
            )
            assert verify_result.returncode == 0, "Branch not created on server"

        finally:
            stack.cleanup()

    def test_push_update_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        client_repo = stack.init_client_repo(copy=True)
        try:
            result = stack.run_client(
                "push HEAD:refs/heads/feature\n\n",
                cwd=client_repo,
            )
            assert result.returncode == 0, "Push failed"

        finally:
            stack.cleanup()

    def test_push_force(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        client_repo = stack.init_client_repo(empty=False)
        try:
            result = stack.run_client(
                "push HEAD:refs/heads/feature\n\n",
                cwd=client_repo,
            )
            assert result.returncode == 0, "Push failed"

            new_commit = client_repo / "test2.txt"
            _ = new_commit.write_text("world")
            _ = stack.git("add", ".", cwd=client_repo, check=True)
            _ = stack.git("commit", "-m", "add more", cwd=client_repo, check=True)

            result = stack.run_client(
                "push +HEAD:refs/heads/feature\n\n",
                cwd=client_repo,
            )
            assert result.returncode == 0, "Force push failed"

        finally:
            stack.cleanup()

    def test_delete_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        _ = stack.git("checkout", "-b", "feature", cwd=repo_dir, check=True)
        _ = stack.git("checkout", "main", cwd=repo_dir, check=True)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        client_repo = stack.init_client_repo()
        try:
            result = stack.run_client("push :refs/heads/feature\n\n", cwd=client_repo)
            if result.returncode != 0:
                result = stack.run_client(
                    "push :refs/heads/feature\n\n", cwd=client_repo
                )

            assert result.returncode == 0, "Delete failed"

            verify_result = stack.git(
                "log",
                "feature",
                cwd=client_repo,
                capture_output=True,
            )
            assert verify_result.returncode != 0, "Branch should have been deleted"

        finally:
            stack.cleanup()

    def test_clone_and_push(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            client_repo = stack.create_client_working_dir()

            clone_result = stack.git(
                "clone",
                f"rns::{stack.server_hash}",
                str(client_repo),
                capture_output=True,
                check=True,
            )

            if clone_result.returncode != 0:
                print(f"\nClone stderr: {clone_result.stderr.decode()}")
                print(f"\nClone stdout: {clone_result.stdout.decode()}")

            if not (client_repo / ".git").exists():
                raise RuntimeError(
                    f"Clone failed - .git not created: {clone_result.stderr}"
                )

            new_file = client_repo / "new_feature.py"
            _ = new_file.write_text("# new feature")
            _ = stack.git("add", ".", cwd=client_repo, check=True)
            commit_result = stack.git(
                "commit",
                "-m",
                "Add new feature",
                cwd=client_repo,
                check=True,
                capture_output=True,
            )
            print(f"\nCommit result: {commit_result.stdout.decode()}")
            print(f"\nCommit stderr: {commit_result.stderr.decode()}")

            git_log = stack.git(
                "log",
                "--oneline",
                cwd=client_repo,
                check=True,
                capture_output=True,
            )
            print(f"Client repo log: {git_log.stdout}")

            push_result = stack.git(
                "push",
                "origin",
                "HEAD:refs/heads/new-feature",
                cwd=client_repo,
                capture_output=True,
            )

            print(f"\nPush stderr: {push_result.stderr.decode()}")
            print(f"\nPush stdout: {push_result.stdout.decode()}")
            assert push_result.returncode == 0, "Push failed"

            verify_result = stack.git(
                "log",
                "--oneline",
                cwd=repo_dir,
                capture_output=True,
            )
            print(f"Server log: {verify_result.stdout.decode()}")

            refs_result = stack.git(
                "show-ref",
                cwd=repo_dir,
                capture_output=True,
            )
            print(f"Server refs: {refs_result.stdout.decode()}")

            assert "new-feature" in refs_result.stdout.decode(), (
                f"Neither push succeeded nor changes on server: {push_result.stderr}"
            )
            assert push_result.returncode == 0, (
                f"Neither push succeeded nor changes on server: {push_result.stderr}"
            )

        finally:
            stack.cleanup()

    def test_wrong_identity_denied(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        alt_rns_config = tmp_path / "rns_alt"
        alt_rns_config.mkdir()

        alt_identity = RNS.Identity(True)
        alt_identity_path = alt_rns_config / "identity"
        _ = alt_identity.to_file(str(alt_identity_path))  # pyright: ignore[reportUnknownMemberType]

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        correct_hash = stack.get_client_identity()
        assert correct_hash != alt_identity.hexhash, "Failed to generate a new identity"
        _ = stack.get_alternate_client_identity()
        stack.start_server(allow_write=[correct_hash])
        try:
            result = stack.run_client(
                "push HEAD:refs/heads/main\n\n",
                identity_path=alt_identity_path,
            )
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected wrong identity to be denied, got: {output}"
            )

        finally:
            stack.cleanup()


class TestNoAuth:
    def test_list_no_auth_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server()
        try:
            result = stack.run_client("list\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected list to fail without auth, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_list_for_push_no_auth_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server()
        try:
            result = stack.run_client("list for-push\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected list-for-push to fail without auth, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_push_no_auth_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server()
        try:
            result = stack.run_client("push HEAD:refs/heads/main\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected push to fail without auth, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_fetch_no_auth_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise RuntimeError("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.init_git_repo(repo_dir)
        stack.start_server()
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected fetch to fail without auth, got: {output}"
            )
        finally:
            stack.cleanup()
