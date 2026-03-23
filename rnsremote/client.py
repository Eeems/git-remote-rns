import logging
import time
import os
import sys
import argparse
import traceback
import subprocess  # noqa: B404
import threading

import RNS  # type: ignore[import-untyped]

from . import protocol
from .connection import (
    Link,
    APP_NAME,
    configure_logging,
)

GIT_DIR = os.environ.get("GIT_DIR", ".git")
__all__ = [
    "configure_logging",
    "ClientLink",
    "main",
    "GIT_DIR",
]


class ClientLink(Link):
    def __init__(
        self,
        link: RNS.Link | None,
        destination_hexhash: str,
        repo_path: str = "",
    ):
        super().__init__(link)
        self.destination_hexhash: str = destination_hexhash
        self.repo_path: str = repo_path

    def request_refs(self, timeout: float = 30.0) -> dict[str, str]:
        self._log.debug(
            "Requesting refs from server (repo: %s)", self.repo_path or "default"
        )

        self.send(
            protocol.HandshakePacket(
                protocol.PROTOCOL_VERSION, self.repo_path
            ).serialize()
        )

        refs: dict[str, str] = {}
        while True:
            data = self.receive(timeout)
            if not data:
                self._log.warning("No data received")
                break

            packet = protocol.parse_packet(data)
            match packet.packet_type:
                case protocol.PACKET_HANDSHAKE:
                    self._log.debug("Received handshake")

                case protocol.PACKET_REF_LIST:
                    if isinstance(packet, protocol.RefListPacket):
                        refs.update(packet.refs)
                        self._log.debug("Received %d refs", len(refs))

                case protocol.PACKET_DONE:
                    self._log.debug("Ref negotiation complete")
                    break

                case protocol.PACKET_ERROR:
                    error_msg = packet.payload.decode("utf-8", errors="replace")
                    self._log.error("Server error: %s", error_msg)
                    break

                case _:
                    continue

        return refs


def main():
    parser = argparse.ArgumentParser(prog="git-remote-rns")
    _ = parser.add_argument("remote", help="Remote name (ignored)")
    _ = parser.add_argument("url", help="Remote URL (rns::<hash>[/path])")
    args = parser.parse_args()

    verbose = bool(os.environ.get("VERBOSE", 0))
    configure_logging(verbose, level=logging.DEBUG if verbose else logging.WARNING)
    log = logging.getLogger(__name__)

    assert isinstance(args.url, str)  # pyright: ignore[reportAny] # nosec B101
    url = args.url

    if not url.startswith("rns::"):
        print("error: Invalid URL format. Expected rns::<hash>[/path]", file=sys.stderr)
        sys.exit(1)

    url_path = url[5:]
    parts = url_path.split("/", 1)
    destination_hash = parts[0]
    repo_path = parts[1] if len(parts) > 1 else ""

    config_path = os.environ.get("RNS_CONFIG_PATH", None)
    try:
        _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)
        _run_helper(log, destination_hash, repo_path)

    except Exception:
        log.exception("Error")
        traceback.print_exc()
        sys.exit(1)


def _run_helper(log: logging.Logger, destination_hash: str, repo_path: str = ""):
    log.debug("Connecting to %s...", destination_hash[:8])
    git_link = _connect(destination_hash, repo_path, 30)

    if not git_link.wait_for_connect(timeout=30):
        log.error("Failed to connect to remote")
        sys.exit(1)

    for line in sys.stdin:
        line = line.rstrip()
        args = line.split()

        if line == "":
            print()
            sys.stdout.flush()  # pyright: ignore[reportUnusedCallResult]
            break

        if args[0] == "capabilities":
            print("connect")
            print()

        elif args[0] == "list":
            refs = git_link.request_refs()
            for name, sha in refs.items():
                print(f"{sha} {name}")
            print()

        elif args[0] == "connect":
            service = args[1] if len(args) > 1 else None
            if service not in ("git-upload-pack", "git-receive-pack"):
                print("error: Unsupported service", file=sys.stderr)
                break

            log.debug("Connecting to service: %s", service)
            print()
            _ = sys.stdout.flush()

            pipe_git_service(git_link, service)
            break

        else:
            print(f"error: Unknown command '{args[0]}'", file=sys.stderr)
            sys.exit(1)

        _ = sys.stdout.flush()

    git_link.close()
    log.debug("Connection closed")


def pipe_git_service(git_link: ClientLink, service: str):
    log = logging.getLogger(__name__)
    stdin_lock = threading.Lock()

    try:
        proc = subprocess.Popen(  # noqa: B603,consider-using-with
            [service, os.path.abspath(GIT_DIR)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError:
        log.error("Service not found: %s. Is git installed?", service)
        sys.exit(1)

    except OSError as e:
        log.error("Failed to start %s: %s", service, e)
        sys.exit(1)

    log.debug("Started %s subprocess", service)
    _pipe_service_data(git_link, proc, stdin_lock, service, log)


def _forward_to_remote(
    proc: subprocess.Popen[bytes],
    log: logging.Logger,
    git_link: ClientLink,
):
    stdout = proc.stdout

    def fn():
        try:
            while stdout is not None:
                data = stdout.read(65536)
                if not data:
                    log.debug("Git stdout closed")
                    break

                git_link.send(protocol.PackPacket(data).serialize())

        except Exception as e:
            log.debug("Error forwarding to remote: %s", e)

        finally:
            try:
                git_link.send(protocol.DonePacket().serialize())

            except Exception:
                traceback.print_exc()

    return fn


def _forward_to_git(
    proc: subprocess.Popen[bytes],
    log: logging.Logger,
    git_link: ClientLink,
    stdin_lock: threading.Lock,
):

    def fn():
        stdin = proc.stdin
        if stdin is None:
            return

        try:
            while True:
                data = git_link.receive()
                if not data:
                    log.debug("Link closed")
                    break
                packet = protocol.parse_packet(data)
                if packet.packet_type == protocol.PACKET_PACK:
                    with stdin_lock:
                        _ = stdin.write(packet.payload)

                elif packet.packet_type == protocol.PACKET_DONE:
                    log.debug("Received DONE from server")
                    with stdin_lock:
                        stdin.close()
                        proc.stdin = None

                    break

        except Exception as e:
            log.debug("Error forwarding to git: %s", e)

        finally:
            with stdin_lock:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()

                    except Exception:
                        traceback.print_exc()

                    proc.stdin = None

    return fn


def _pipe_service_data(
    git_link: ClientLink,
    proc: subprocess.Popen[bytes],
    stdin_lock: threading.Lock,
    service: str,
    log: logging.Logger,
):
    t_remote = threading.Thread(
        target=_forward_to_remote(proc, log, git_link),
        daemon=True,
    )
    t_git = threading.Thread(
        target=_forward_to_git(proc, log, git_link, stdin_lock),
        daemon=True,
    )

    t_remote.start()
    t_git.start()

    _ = proc.wait()
    log.debug("%s exited with code %d", service, proc.returncode)

    if proc.returncode != 0:
        stderr_data = (
            proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        )
        log.warning("%s exited with code %d: %s", service, proc.returncode, stderr_data)

    t_remote.join(timeout=5)
    t_git.join(timeout=5)

    if t_remote.is_alive():
        log.warning("forward_to_remote thread still running after timeout")
    if t_git.is_alive():
        log.warning("forward_to_git thread still running after timeout")


def _connect(
    destination_hexhash: str,
    repo_path: str = "",
    timeout: float = 60.0,
):
    """Create a client link to a remote RNS destination.

    Args:
        destination_hexhash: 32-character hex string of the destination hash.
        config_path: Optional path to RNS config directory.
        repo_path: Optional repo path to request from server.
        timeout: Timeout for path discovery in seconds.

    Returns:
        A ClientLink instance connected to the destination.

    Raises:
        ValueError: If destination hash is invalid or connection fails.
    """
    log = logging.getLogger(__name__)

    dest_len = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2
    if len(destination_hexhash) != dest_len:
        raise ValueError(
            f"Invalid destination hash: expected {dest_len} hex characters, got {len(destination_hexhash)}. "
            + "Ensure you are using the correct RNS destination hash."
        )

    try:
        destination_hash = bytes.fromhex(destination_hexhash)

    except ValueError as e:
        raise ValueError(
            f"Invalid destination hash format: {destination_hexhash[:8]}... - {e}"
        ) from e

    log.debug("Looking for path to %s...", destination_hexhash[:8])

    if not RNS.Transport.has_path(destination_hash):  # pyright: ignore[reportUnknownMemberType]
        log.debug("Path not known, requesting...")
        RNS.Transport.request_path(destination_hash)  # pyright: ignore[reportUnknownMemberType]

        waited = 0.0
        while not RNS.Transport.has_path(destination_hash) and waited < timeout:  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.5)
            waited += 0.5

        if not RNS.Transport.has_path(destination_hash):  # pyright: ignore[reportUnknownMemberType]
            raise ValueError(
                f"Connection timeout: could not find path to destination {destination_hexhash[:8]}... "
                + f"after {timeout}s. Verify the server is running and the destination hash is correct."
            )

    server_identity = RNS.Identity.recall(destination_hash)  # pyright: ignore[reportUnknownMemberType]
    if server_identity is None:
        raise ValueError(
            f"Unknown destination: server identity not found for {destination_hexhash[:8]}... "
            + "The server may need to be restarted or the destination hash is incorrect."
        )

    destination = RNS.Destination(
        server_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        APP_NAME,
        destination_hexhash,
    )

    log.debug("Connecting to %s...", destination_hexhash[:8])
    client_link = ClientLink(None, destination_hexhash, repo_path)  # type: ignore[arg-type]
    client_link.start(destination)
    return client_link
