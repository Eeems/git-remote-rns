import os
import tempfile
import pytest
from unittest.mock import (
    MagicMock,
    patch,
)
from rnsremote import (
    helper,
    server,
    connection,
    protocol,
)
from rnsremote.connection import (
    ClientLink,
    Link,
)


class TestURLParsing:
    def test_valid_url_simple(self):
        url = "rns::a1b2c3d4e5f678901234567890123456"
        assert url.startswith("rns::")
        url_path = url[5:]
        parts = url_path.split("/", 1)
        assert parts[0] == "a1b2c3d4e5f678901234567890123456"

    def test_valid_url_with_path(self):
        url = "rns::a1b2c3d4e5f678901234567890123456/myproject"
        url_path = url[5:]
        parts = url_path.split("/", 1)
        assert parts[0] == "a1b2c3d4e5f678901234567890123456"
        assert parts[1] == "myproject"

    def test_valid_url_with_nested_path(self):
        url = "rns::a1b2c3d4e5f678901234567890123456/myproject/sub"
        url_path = url[5:]
        parts = url_path.split("/", 1)
        assert parts[0] == "a1b2c3d4e5f678901234567890123456"
        assert parts[1] == "myproject/sub"

    def test_invalid_url_no_prefix(self):
        url = "a1b2c3d4e5f678901234567890123456"
        assert not url.startswith("rns::")

    def test_invalid_url_empty(self):
        url = "rns::"
        url_path = url[5:]
        assert url_path == ""

    def test_invalid_url_too_short_hash(self):
        url = "rns::a1b2c3d4e5"
        url_path = url[5:]
        parts = url_path.split("/", 1)
        dest_hash = parts[0]
        assert len(dest_hash) < 32


class TestDestinationHashValidation:
    def test_valid_hash_length(self):
        dest_hash = "a1b2c3d4e5f678901234567890123456"
        dest_len = 32
        assert len(dest_hash) == dest_len

    def test_invalid_hash_length_short(self):
        dest_hash = "a1b2c3d4"
        dest_len = 32
        assert len(dest_hash) != dest_len

    def test_invalid_hash_length_long(self):
        dest_hash = "a1b2c3d4e5f6789012345678901234567890"
        dest_len = 32
        assert len(dest_hash) != dest_len

    def test_valid_hex_characters(self):
        dest_hash = "a1b2c3d4e5f678901234567890123456"
        try:
            _ = bytes.fromhex(dest_hash)
            valid = True
        except ValueError:
            valid = False
        assert valid

    def test_invalid_hex_characters(self):
        dest_hash = "g1h2i3j4k5l678901234567890123456"
        try:
            _ = bytes.fromhex(dest_hash)
            valid = True
        except ValueError:
            valid = False
        assert not valid


class TestCommandParsing:
    def test_capabilities_command(self):
        line = "capabilities"
        args = line.split()
        assert args[0] == "capabilities"

    def test_list_command(self):
        line = "list"
        args = line.split()
        assert args[0] == "list"

    def test_connect_command(self):
        line = "connect git-upload-pack"
        args = line.split()
        assert args[0] == "connect"
        assert args[1] == "git-upload-pack"

    def test_connect_receive_pack(self):
        line = "connect git-receive-pack"
        args = line.split()
        assert args[0] == "connect"
        assert args[1] == "git-receive-pack"

    def test_empty_line(self):
        line = ""
        args = line.split()
        assert line == ""
        assert len(args) == 0

    def test_unknown_command(self):
        line = "unknown"
        args = line.split()
        assert args[0] not in ("capabilities", "list", "connect")


class TestClientLink:
    def test_request_refs_empty_response(self):
        mock_link = MagicMock()
        mock_link.receive.side_effect = [None]

        client_link = ClientLink(mock_link, "a1b2c3d4e5f678901234567890123456")
        refs = client_link.request_refs(timeout=1.0)

        assert refs == {}

    def test_request_refs_with_refs(self):
        mock_link = MagicMock()

        ref_list_data = protocol.RefListPacket(
            {"refs/heads/main": "abc123def456"}
        ).serialize()
        done_data = protocol.DonePacket().serialize()

        mock_link.receive.side_effect = [
            protocol.HandshakePacket(1, "").serialize(),
            ref_list_data,
            done_data,
        ]

        client_link = ClientLink(mock_link, "a1b2c3d4e5f678901234567890123456")
        refs = client_link.request_refs(timeout=1.0)

        assert "refs/heads/main" in refs
        assert refs["refs/heads/main"] == "abc123def456"

    def test_request_refs_error_packet(self):
        mock_link = MagicMock()

        error_data = protocol.ErrorPacket("Server error").serialize()
        mock_link.receive.side_effect = [
            protocol.HandshakePacket(1, "").serialize(),
            error_data,
        ]

        client_link = ClientLink(mock_link, "a1b2c3d4e5f678901234567890123456")
        refs = client_link.request_refs(timeout=1.0)

        assert refs == {}


class TestServerIdentitySaveLoad:
    @patch("rnsremote.connection.RNS.Identity.from_file")
    def test_identity_save_load_roundtrip(self, mock_from_file):
        mock_identity = MagicMock()
        mock_identity.to_file = MagicMock()
        mock_from_file.return_value = mock_identity

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ident") as f:
            path = f.name

        try:
            connection.save_identity(mock_identity, path)
            mock_identity.to_file.assert_called_once_with(path)

            loaded_identity = connection.load_identity(path)
            mock_from_file.assert_called_once_with(path)
            assert loaded_identity is mock_identity
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @patch("rnsremote.connection.RNS.Identity.from_file")
    def test_load_identity_failure(self, mock_from_file):
        mock_from_file.return_value = None

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ident") as f:
            path = f.name

        try:
            os.unlink(path)
            with pytest.raises(ValueError, match="Failed to load identity"):
                _ = connection.load_identity(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestGetGitRefs:
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_git_refs_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/git"
        mock_run.return_value = MagicMock(
            stdout="abc123 refs/heads/main\ndef456 refs/tags/v1.0\n", check=True
        )

        refs = server.get_git_refs("/some/repo")

        assert "refs/heads/main" in refs
        assert refs["refs/heads/main"] == "abc123"
        assert "refs/tags/v1.0" in refs
        assert refs["refs/tags/v1.0"] == "def456"

    @patch("shutil.which")
    def test_get_git_refs_git_not_found(self, mock_which):
        mock_which.return_value = None

        refs = server.get_git_refs("/some/repo")

        assert refs == {}

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_git_refs_command_error(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/git"
        mock_run.side_effect = MagicMock(stderr="fatal: not a git repository")
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(
            1, "git", stderr=b"fatal: not a git repository"
        )

        refs = server.get_git_refs("/some/repo")

        assert refs == {}

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_git_refs_empty_repo(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/git"
        mock_run.return_value = MagicMock(stdout="", check=True)

        refs = server.get_git_refs("/some/repo")

        assert refs == {}

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_git_refs_timeout(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/git"
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)

        refs = server.get_git_refs("/some/repo")

        assert refs == {}


class TestHandleConnection:
    @patch.object(protocol, "HandshakePacket")
    @patch.object(protocol, "RefListPacket")
    @patch.object(protocol, "DonePacket")
    @patch.object(server, "get_git_refs")
    def test_handle_connection_success(
        self, mock_get_refs, mock_done, mock_ref_list, mock_handshake
    ):
        mock_link = MagicMock()
        mock_link.wait_for_connect.return_value = True
        mock_link.receive.side_effect = [
            protocol.HandshakePacket(1, "/repo").serialize(),
            protocol.DonePacket().serialize(),
        ]
        mock_get_refs.return_value = {"refs/heads/main": "abc123"}

        class TestServerLink:
            def __init__(self, link, repo_path):
                self._link = link
                self.repo_path = repo_path

            def wait_for_connect(self, timeout=30.0):
                return self._link.wait_for_connect(timeout)

            def receive(self, timeout=None):
                return self._link.receive(timeout)

            def send(self, data):
                self._link.send(data)

            def close(self):
                self._link.close()

        server_link = TestServerLink(mock_link, "/repo")
        server.handle_connection(server_link)  # pyright: ignore[reportArgumentType]

        mock_link.wait_for_connect.assert_called_once()
        assert mock_link.send.call_count >= 2

    def test_handle_connection_no_connect(self):
        mock_link = MagicMock()
        mock_link.wait_for_connect.return_value = False

        class TestServerLink:
            def __init__(self, link, repo_path):
                self._link = link
                self.repo_path = repo_path

            def wait_for_connect(self, timeout=30.0):
                return self._link.wait_for_connect(timeout)

            def receive(self, timeout=None):
                return self._link.receive(timeout)

            def send(self, data):
                self._link.send(data)

            def close(self):
                self._link.close()

        server_link = TestServerLink(mock_link, "/repo")
        server.handle_connection(server_link)  # pyright: ignore[reportArgumentType]

        mock_link.close.assert_called_once()


class TestLink:
    def test_link_wait_for_connect_timeout(self):
        link = Link()
        result = link.wait_for_connect(timeout=0.1)
        assert result is False

    def test_link_wait_for_connect_sets_connected(self):
        link = Link()

        result = link.wait_for_connect(timeout=0.1)
        assert result is False
        assert not link._connected.is_set()

        link.set_connected()
        result = link.wait_for_connect(timeout=0.1)
        assert result is True

    def test_link_send(self):
        mock_link = MagicMock()
        link = Link(mock_link)
        link.send(b"test data")
        mock_link.send.assert_called_once_with(b"test data")

    def test_link_receive(self):
        mock_link = MagicMock()
        mock_link.receive.return_value = b"test data"

        link = Link(mock_link)
        result = link.receive()

        assert result == b"test data"

    def test_link_receive_none(self):
        mock_link = MagicMock()
        mock_link.receive.return_value = None

        link = Link(mock_link)
        result = link.receive()

        assert result is None

    def test_link_close(self):
        mock_link = MagicMock()
        link = Link(mock_link)
        link.close()

        mock_link.teardown.assert_called_once()
        assert link._link is None


class TestPipeGitService:
    @patch("subprocess.Popen")
    def test_pipe_git_service_starts_process(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        mock_git_link = MagicMock()
        mock_git_link.receive.return_value = None

        with patch.object(helper, "_pipe_service_data"):
            helper.pipe_git_service(mock_git_link, "git-upload-pack")

        mock_popen.assert_called_once()

    @patch("subprocess.Popen")
    def test_pipe_git_service_file_not_found(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError()

        mock_git_link = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            helper.pipe_git_service(mock_git_link, "git-upload-pack")

        assert exc_info.value.code == 1

    @patch("subprocess.Popen")
    def test_pipe_git_service_os_error(self, mock_popen):
        mock_popen.side_effect = OSError("Permission denied")

        mock_git_link = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            helper.pipe_git_service(mock_git_link, "git-upload-pack")

        assert exc_info.value.code == 1


class TestConfigureLogging:
    def test_configure_logging_verbose(self):
        import logging

        connection.configure_logging(verbose=True)
        assert logging.root.level == logging.DEBUG

    def test_configure_logging_default(self):
        import logging

        connection.configure_logging(verbose=False)
        assert logging.root.level == logging.INFO

    def test_configure_logging_custom_level(self):
        import logging

        helper.configure_logging(False, level=logging.WARNING)
        assert logging.root.level == logging.WARNING


class TestRefLineParsing:
    def test_single_word_line(self):
        line = "singleword"
        parts = line.split(" ", 1)
        if len(parts) != 2:
            pass
        else:
            sha, name = parts

    def test_valid_ref_line(self):
        line = "abc123def456 refs/heads/main"
        parts = line.split(" ", 1)
        assert len(parts) == 2
        sha, name = parts
        assert sha == "abc123def456"
        assert name == "refs/heads/main"

    def test_ref_with_spaces_in_name(self):
        line = "abc123 refs/heads/feature branch"
        parts = line.split(" ", 1)
        if len(parts) == 2:
            sha, name = parts


class TestServerParseArgs:
    @patch("sys.argv", ["rngit", "/path/to/repo", "abc123def45678901234567890123456"])
    def test_parse_args_basic(self):
        args = server._parse_args()
        assert args.repo == "/path/to/repo"
        assert args.destination == "abc123def45678901234567890123456"
        assert args.verbose is False
        assert args.config is None
        assert args.identity is None
        assert args.save_identity is None

    @patch("sys.argv", ["rngit", "/path/to/repo", "-v"])
    def test_parse_args_verbose(self):
        args = server._parse_args()
        assert args.verbose is True

    @patch("sys.argv", ["rngit", "/path/to/repo", "--config", "/path/to/config"])
    def test_parse_args_with_config(self):
        args = server._parse_args()
        assert args.config == "/path/to/config"

    @patch("sys.argv", ["rngit", "/path/to/repo", "--identity", "server.ident"])
    def test_parse_args_with_identity(self):
        args = server._parse_args()
        assert args.identity == "server.ident"

    @patch("sys.argv", ["rngit", "/path/to/repo", "--save-identity", "server.ident"])
    def test_parse_args_with_save_identity(self):
        args = server._parse_args()
        assert args.save_identity == "server.ident"


class TestConnectionExports:
    def test_connection_has_all(self):
        from rnsremote import connection

        assert hasattr(connection, "__all__")
        expected = [
            "Link",
            "ClientLink",
            "connect",
            "create_server_identity",
            "create_server_destination",
            "save_identity",
            "load_identity",
            "configure_logging",
            "get_reticulum",
        ]
        assert connection.__all__ == expected

    def test_all_exports_are_available(self):
        from rnsremote import connection

        for name in connection.__all__:
            assert hasattr(connection, name), f"Missing export: {name}"


class TestEndToEnd:
    def test_rns_link_callback_interface(self):
        import RNS

        # Verify RNS.Link accepts established_callback parameter
        import inspect

        sig = inspect.signature(RNS.Link.__init__)
        params = list(sig.parameters.keys())
        assert "established_callback" in params

    def test_rns_destination_callback_interface(self):
        import RNS

        # Verify RNS.Destination has set_link_established_callback
        assert hasattr(RNS.Destination, "set_link_established_callback")

    def test_server_and_client(self, tmp_path):
        import os
        import subprocess
        import time
        import shutil
        import re
        import sys
        import pathlib

        if not shutil.which("rnsd"):
            pytest.skip("rnsd binary not found in PATH")

        venv_python = sys.executable
        venv_bin = pathlib.Path(venv_python).parent
        rngit_bin = str(venv_bin / "rngit")
        git_remote_rns_bin = str(venv_bin / "git-remote-rns")
        rnsd = str(venv_bin / "rnsd")

        if not pathlib.Path(rngit_bin).exists():
            pytest.skip("rngit not installed in venv")

        rns_config_dir = tmp_path / "rns"
        rns_config_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        with open(repo_dir / "test.txt", "w") as f:
            f.write("hello")
        subprocess.run(
            ["git", "add", "."], cwd=repo_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )

        rns_config = rns_config_dir / "config"
        rns_config.write_text("""
[reticulum]
  share_instance = Yes

[interfaces]
  [[AutoInterface]]
    type = AutoInterface
    enabled = yes
""")

        identity_file = tmp_path / "identity"
        workdir = pathlib.Path.cwd()

        rnsd_proc = subprocess.Popen(
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

        def wait_for_rns_ready(timeout=15):
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
                ready, _, _ = select.select([server_proc.stdout], [], [], 5)
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

            result = subprocess.run(
                [git_remote_rns_bin, "origin", f"rns::{dest_hash}"],
                env={**os.environ, "RNS_CONFIG_PATH": str(rns_config_dir)},
                input="capabilities\n\n",
                capture_output=True,
                text=True,
                timeout=30,
            )

            output = result.stdout + (result.stderr or "")

            if "timeout" in output.lower() or "failed to connect" in output.lower():
                assert False, (
                    f"Client failed to connect to server. "
                    f"Server hash: {dest_hash}. "
                    f"Output: {output}"
                )

            assert "connect" in output, f"Expected 'connect' capability, got: {output}"

        finally:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()
            rnsd_proc.terminate()
            try:
                rnsd_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rnsd_proc.kill()
                rnsd_proc.wait()
