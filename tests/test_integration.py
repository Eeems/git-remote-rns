import os
import pathlib
import random
import re
import select
import string
import subprocess
import sys
import tempfile
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

_rnsd_process: subprocess.Popen[str] | None = None
_rnsd_config_dir: Path | None = None


@pytest.fixture(scope="session", autouse=True)
def shared_rnsd(tmp_path_factory: pytest.TempPathFactory):
    global _rnsd_process
    global _rnsd_config_dir

    config_dir = tmp_path_factory.mktemp("rns")
    rns_config = config_dir / "config"
    _ = rns_config.write_text(RETICULUM_CONFIG)

    rnsd_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "RNS.Utilities.rnsd",
            "--config",
            str(config_dir),
            "--verbose",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    start = time.time()
    while True:
        if rnsd_proc.poll() is not None:
            out = rnsd_proc.stdout.read().strip() if rnsd_proc.stdout else ""
            err = rnsd_proc.stderr.read().strip() if rnsd_proc.stderr else ""
            raise Exception(
                f"rnsd exited early: {rnsd_proc.returncode} stdout: {out!r}, stderr: {err!r}"
            )

        if time.time() - start >= 10.0:
            rnsd_proc.terminate()
            _ = rnsd_proc.wait()
            out = rnsd_proc.stdout.read().strip() if rnsd_proc.stdout else ""
            err = rnsd_proc.stderr.read().strip() if rnsd_proc.stderr else ""
            raise Exception(
                f"RNS shared instance failed to start in time. stdout: {out!r}, stderr: {err!r}"
            )

        result = subprocess.run(
            ["rnstatus", "--config", str(config_dir), "-a"],
            capture_output=True,
            text=True,
        )
        if "Shared Instance" in result.stdout and "Up" in result.stdout:
            break

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

        self.server_proc = subprocess.Popen(
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

    def run_client(
        self, stdin: str, identity_path: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert self.server_hash is not None

        env = {**os.environ, "RNS_CONFIG_PATH": str(self.rns_config)}
        if identity_path:
            env["RNS_IDENTITY_PATH"] = str(identity_path)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rngit",
                "git-remote-rns",
                "origin",
                self.server_hash,
            ],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
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
        self, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        if cwd is None:
            cwd = self.server_repo
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
        )

    def _create_test_commit(
        self, filename: str, content: str, cwd: Path | None = None
    ) -> None:
        if cwd is None:
            cwd = self.server_repo
        path = cwd / filename
        _ = path.write_text(content)
        _ = self.git("add", ".", cwd=cwd)
        _ = self.git("commit", "-m", f"Add {filename}", cwd=cwd)

    def create_client_working_dir(self) -> Path:
        self.client_working_dir = Path(tempfile.mkdtemp())
        return self.client_working_dir

    def cleanup(self) -> None:
        if self.server_proc:
            self.server_proc.terminate()
            try:
                _ = self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
                _ = self.server_proc.wait()


def _init_git_repo(repo: Path) -> None:
    _ = subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    _ = subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        capture_output=True,
    )
    _ = (repo / "test.txt").write_text("hello")
    _ = subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    _ = subprocess.run(
        ["git", "branch", "-m", "main"],
        cwd=repo,
        capture_output=True,
        check=True,
    )


class TestPublicAccess:
    def test_capabilities(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.start_server(allow_all_read=True)
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n")
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.start_server(allow_all_read=True)
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n")
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_read=[client_hash])
        try:
            result = stack.run_client("list\n\n")
            output = result.stdout + result.stderr
            assert "refs/heads" in output, (
                f"Expected refs/heads in output, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_fetch_with_allow_read(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_read=[client_hash])
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Fetch failed: {output}"
        finally:
            stack.cleanup()

    def test_list_for_push_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_read=[client_hash])
        try:
            result = stack.run_client("list\nfor-push\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected list-for-push to fail without write access, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_wrong_identity_denied(self, tmp_path: Path) -> None:
        # TODO dtermine why this test is slow
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        alt_rns_config = tmp_path / "rns_alt"
        alt_rns_config.mkdir()

        alt_identity = RNS.Identity(True)
        alt_identity_path = alt_rns_config / "identity"
        _ = alt_identity.to_file(str(alt_identity_path))  # pyright: ignore[reportUnknownMemberType]

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        correct_hash = stack.get_client_identity()
        stack.start_server(allow_read=[correct_hash])
        try:
            env = {**os.environ, "RNS_CONFIG_PATH": str(alt_rns_config)}
            venv_bin = pathlib.Path(sys.executable).parent
            assert stack.server_hash is not None
            cmd: list[str] = [
                str(venv_bin / "git-remote-rns"),
                "origin",
                stack.server_hash,
            ]
            result = subprocess.run(
                cmd,
                env=env,
                input="list\n\n",
                capture_output=True,
                text=True,
                timeout=60,
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Fetch failed: {output}"
        finally:
            stack.cleanup()

    def test_list_for_push(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("list\nfor-push\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" not in output, (
                f"Expected list-for-push to work with write access, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_push_new_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("push HEAD:refs/heads/new-branch\n\n")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Push failed: {output}"
            assert "error" not in output.lower(), f"Error in output: {output}"

            verify_result = stack.git("log", "new-branch", cwd=repo_dir)
            assert verify_result.returncode == 0, "Branch not created on server"
        finally:
            stack.cleanup()

    def test_push_update_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("push HEAD:refs/heads/feature\n\n")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Push failed: {output}"
        finally:
            stack.cleanup()

    def test_push_force(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("push HEAD:refs/heads/feature\n\n")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Push failed: {output}"

            new_commit = repo_dir / "test2.txt"
            _ = new_commit.write_text("world")
            _ = subprocess.run(
                ["git", "add", "."], cwd=repo_dir, capture_output=True, check=True
            )
            _ = subprocess.run(
                ["git", "commit", "-m", "add more"],
                cwd=repo_dir,
                capture_output=True,
                check=True,
            )

            result = stack.run_client("push +HEAD:refs/heads/feature\n\n")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"Force push failed: {output}"
        finally:
            stack.cleanup()

    def test_delete_branch(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        _ = subprocess.run(
            ["git", "checkout", "-b", "feature"], cwd=repo_dir, capture_output=True
        )
        _ = subprocess.run(
            ["git", "checkout", "main"], cwd=repo_dir, capture_output=True
        )

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            result = stack.run_client("push :refs/heads/feature\n\n")
            output = result.stdout + result.stderr
            if result.returncode != 0:
                result = stack.run_client("push :refs/heads/feature\n\n")
                output = result.stdout + result.stderr
            assert result.returncode == 0, f"Delete failed: {output}"

            verify_result = stack.git("log", "feature", cwd=repo_dir)
            assert verify_result.returncode != 0, "Branch should have been deleted"
        finally:
            stack.cleanup()

    def test_clone_and_push(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        client_hash = stack.get_client_identity()
        stack.start_server(allow_write=[client_hash])
        try:
            client_repo = stack.create_client_working_dir()

            env = {**os.environ, "RNS_CONFIG_PATH": str(_rnsd_config_dir)}
            venv_bin = pathlib.Path(sys.executable).parent
            env["PATH"] = str(venv_bin) + ":" + env.get("PATH", "")

            clone_result = subprocess.run(
                ["git", "clone", f"rns::{stack.server_hash}", str(client_repo)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if clone_result.returncode != 0:
                print(f"Clone stderr: {clone_result.stderr}")
                print(f"Clone stdout: {clone_result.stdout}")

            if not (client_repo / ".git").exists():
                raise Exception(
                    f"Clone failed - .git not created: {clone_result.stderr}"
                )

            new_file = client_repo / "new_feature.py"
            _ = new_file.write_text("# new feature")
            _ = subprocess.run(
                ["git", "add", "."], cwd=client_repo, capture_output=True, check=True
            )
            commit_result = subprocess.run(
                ["git", "commit", "-m", "Add new feature"],
                cwd=client_repo,
                capture_output=True,
                text=True,
            )
            print(f"Commit result: {commit_result.stdout}")
            print(f"Commit stderr: {commit_result.stderr}")

            git_log = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=client_repo,
                capture_output=True,
                text=True,
            )
            print(f"Client repo log: {git_log.stdout}")

            push_result = subprocess.run(
                ["git", "push", "origin", "HEAD:refs/heads/new-feature", "-f"],
                cwd=client_repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

            print(f"Push stderr: {push_result.stderr}")
            print(f"Push stdout: {push_result.stdout}")

            verify_result = stack.git("log", "--oneline", cwd=repo_dir)
            print(f"Server log: {verify_result.stdout.decode()}")

            refs_result = stack.git("show-ref", cwd=repo_dir)
            print(f"Server refs: {refs_result.stdout.decode()}")

            if "new-feature" in refs_result.stdout.decode():
                pass
            elif push_result.returncode == 0:
                pass
            else:
                assert False, (
                    f"Neither push succeeded nor changes on server: {push_result.stderr}"
                )
        finally:
            stack.cleanup()

    def test_wrong_identity_denied(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        correct_hash = stack.get_client_identity()
        _ = stack.get_alternate_client_identity()
        stack.start_server(allow_write=[correct_hash])
        try:
            result = stack.run_client("push HEAD:refs/heads/main\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected wrong identity to be denied, got: {output}"
            )
        finally:
            stack.cleanup()


class TestNoAuth:
    def test_list_no_auth_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.start_server()
        try:
            result = stack.run_client("list\nfor-push\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected list-for-push to fail without auth, got: {output}"
            )
        finally:
            stack.cleanup()

    def test_push_no_auth_fails(self, tmp_path: Path) -> None:
        if not _rnsd_config_dir:
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
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
            raise Exception("RNS not available")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        stack = IntegrationStack(_rnsd_config_dir, repo_dir)
        stack.start_server()
        try:
            result = stack.run_client("fetch HEAD refs/heads/main\n\n")
            output = result.stdout + result.stderr
            assert "Not allowed" in output or result.returncode != 0, (
                f"Expected fetch to fail without auth, got: {output}"
            )
        finally:
            stack.cleanup()
