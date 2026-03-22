from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess  # noqa: B404
import sys
import threading
import time
import traceback

import RNS  # type: ignore[import-untyped]

from . import __version__
from . import protocol
from .connection import (
    configure_logging,
    create_server_identity,
    create_server_destination,
    load_identity,
    save_identity,
    Link,
)


__all__ = ["serve_forever", "ServerLink"]


class ServerLink(Link):
    def __init__(self, link: RNS.Link, repo_path: str):
        super().__init__(link)
        self.repo_path: str = repo_path


def serve_forever(  # noqa: PLR0913,PLR0912,R0917,too-many-positional-arguments
    destination_hexhash: str | None = None,
    repo_path: str | None = None,
    config_path: str | None = None,
    verbose: bool = False,
    identity_path: str | None = None,
    save_identity_path: str | None = None,
    announce_interval: int | None = None,
):
    if repo_path is None:
        args = _parse_args()
        assert isinstance(args.repo, str | None)  # pyright: ignore[reportAny] # nosec B101
        assert isinstance(args.destination, str | None)  # pyright: ignore[reportAny] # nosec B101
        assert isinstance(args.config, str | None)  # pyright: ignore[reportAny] # nosec B101
        assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny] # nosec B101
        assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny] # nosec B101
        assert isinstance(args.save_identity, str | None)  # pyright: ignore[reportAny] # nosec B101
        assert isinstance(args.announce_interval, int | None)  # pyright: ignore[reportAny] # nosec B101
        repo_path = args.repo
        destination_hexhash = args.destination or destination_hexhash
        config_path = args.config or config_path
        verbose = args.verbose or verbose
        identity_path = args.identity or identity_path
        save_identity_path = args.save_identity or save_identity_path
        announce_interval = args.announce_interval or announce_interval

    if repo_path is None:
        raise ValueError("repo is required")

    configure_logging(verbose)
    log = logging.getLogger(__name__)

    if not os.path.isdir(repo_path):
        log.error("Not a valid repository: %s", repo_path)
        sys.exit(1)

    identity = _load_or_create_identity(
        log,
        identity_path,
        save_identity_path,
        config_path,
    )

    if destination_hexhash is None:
        destination_hexhash = identity.hexhash
        log.info("Using identity's hash as destination: %s", destination_hexhash)

    assert destination_hexhash is not None  # nosec B101
    _serve_loop(
        log, identity, destination_hexhash, repo_path, announce_interval, config_path
    )


def _parse_args():
    parser = argparse.ArgumentParser(description="RNS Git Server", allow_abbrev=False)
    _ = parser.add_argument("repo", help="Path to git repository to serve")
    _ = parser.add_argument(
        "destination",
        nargs="?",
        default=None,
        help="Destination hash for this server (hex)",
    )
    _ = parser.add_argument("--config", help="Path to Reticulum config directory")
    _ = parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    _ = parser.add_argument("--identity", help="Path to load existing identity file")
    _ = parser.add_argument("--save-identity", help="Path to save identity file")
    _ = parser.add_argument(
        "--version", "-V", action="version", version=f"rngit {__version__}"
    )
    _ = parser.add_argument(
        "--announce-interval",
        type=int,
        default=None,
        help="Interval in seconds between announces (default: announce once)",
    )
    return parser.parse_args()


def _load_or_create_identity(
    log: logging.Logger,
    identity_path: str | None,
    save_identity_path: str | None,
    config_path: str | None,
):
    if identity_path:
        log.info("Loading identity from %s", identity_path)
        identity = load_identity(identity_path)

    else:
        log.info("Creating new server identity...")
        identity = create_server_identity(config_path)

    if save_identity_path:
        log.info("Saving identity to %s", save_identity_path)
        save_identity(identity, save_identity_path)

    return identity


def _serve_loop(  # noqa: PLR0913,PLR0912,too-many-positional-arguments
    log: logging.Logger,
    identity: RNS.Identity,
    destination_hexhash: str,
    repo_path: str,
    announce_interval: int | None = None,
    config_path: str | None = None,
):
    log.info("Creating server destination...")
    destination = create_server_destination(identity, destination_hexhash, config_path)

    log.info("Server destination hash: %s", destination.hexhash)  # pyright: ignore[reportAny]
    log.info("Share this hash with clients to allow connections")
    log.info("Serving repository: %s", repo_path)

    log.debug("Announcing destination...")
    _ = destination.announce()  # pyright: ignore[reportUnknownMemberType]

    def on_link_established(link: RNS.Link):
        log.debug("Client connected")
        server_link = ServerLink(link, repo_path)
        threading.Thread(
            target=handle_connection,
            args=(server_link,),
            daemon=True,
        ).start()

    destination.set_link_established_callback(on_link_established)  # pyright: ignore[reportUnknownMemberType]

    _wait_for_shutdown(log, destination, announce_interval)


def _wait_for_shutdown(
    log: logging.Logger,
    destination: RNS.Destination | None = None,
    announce_interval: int | None = None,
):
    if announce_interval is not None and announce_interval > 0:
        log.info("Will announce every %d seconds", announce_interval)
        if destination is None:
            raise ValueError("destination required for periodic announces")

    next_announce = 0.0
    while True:
        try:
            time.sleep(0.1)
            if announce_interval is not None and announce_interval > 0:
                next_announce -= 0.1
                if next_announce <= 0:
                    log.debug("Periodic announce...")
                    _ = destination.announce()  # pyright: ignore[reportUnknownMemberType, reportOptionalMemberAccess]
                    next_announce = announce_interval

        except KeyboardInterrupt:
            log.info("Server shutting down")
            break

        except OSError as e:
            log.error("OS error: %s", e)

        except Exception as e:
            log.error("Unexpected error: %s", e)
            raise


def handle_connection(link: ServerLink):
    log = logging.getLogger(__name__)

    try:
        if not link.wait_for_connect():
            log.error("Failed to establish link")
            return

        handshake_data = link.receive()
        if handshake_data:
            packet = protocol.parse_packet(handshake_data)
            if packet.packet_type == protocol.PACKET_HANDSHAKE:
                link.send(
                    protocol.HandshakePacket(
                        protocol.PROTOCOL_VERSION, link.repo_path
                    ).serialize()
                )
                log.debug("Handshake complete")

        refs = get_git_refs(link.repo_path)
        link.send(protocol.RefListPacket(refs).serialize())
        link.send(protocol.DonePacket().serialize())
        log.debug("Sent %d refs", len(refs))

        service_type = _wait_for_service_request(link, log)

        if service_type:
            log.info("Starting %s", service_type)
            run_git_service(link, service_type, link.repo_path)
        else:
            log.debug("Connection closed (no service requested)")

    except Exception as e:
        log.error("Error handling connection: %s", e)
    finally:
        link.close()


def _wait_for_service_request(link: ServerLink, log: logging.Logger):
    while True:
        data = link.receive(timeout=60)
        if not data:
            log.debug("No more data from client")
            break

        packet = protocol.parse_packet(data)
        match packet.packet_type:
            case protocol.PACKET_WANT:
                return "git-upload-pack"

            case protocol.PACKET_DONE:
                break

            case _:
                pass

    return None


def get_git_refs(repo_path: str) -> dict[str, str]:
    log = logging.getLogger(__name__)
    refs: dict[str, str] = {}
    git_cmd = shutil.which("git")
    if git_cmd is None:
        log.error("git executable not found in PATH")
        return refs

    try:
        result = subprocess.run(  # noqa: S603
            [git_cmd, "for-each-ref", "--format=%(objectname) %(refname)", "refs/"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        for line in result.stdout.strip().split("\n"):
            if line:
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    sha, name = parts
                    refs[name] = sha

    except subprocess.CalledProcessError as e:
        log.error("git for-each-ref failed: %s", e.stderr)  # pyright: ignore[reportAny]

    except subprocess.TimeoutExpired:
        log.error("git for-each-ref timed out")

    except Exception as e:
        log.error("Error getting refs: %s", e)

    return refs


def run_git_service(link: ServerLink, service: str, repo_path: str):
    log = logging.getLogger(__name__)
    stdin_lock = threading.Lock()

    try:
        proc = subprocess.Popen(  # noqa: B603,consider-using-with
            [service, repo_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    except FileNotFoundError:
        log.error("Service not found: %s. Is git installed?", service)
        return

    except OSError as e:
        log.error("Failed to start %s: %s", service, e)
        return

    log.debug("Started %s subprocess", service)
    _pipe_server_data(link, proc, stdin_lock, service, log)


def _forward_to_git(
    proc: subprocess.Popen[bytes],
    link: ServerLink,
    log: logging.Logger,
    stdin_lock: threading.Lock,
):
    def fn():
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            while True:
                data = link.receive()
                if not data:
                    log.debug("Link closed, closing git stdin")
                    break

                packet = protocol.parse_packet(data)
                if packet.packet_type == protocol.PACKET_PACK:
                    with stdin_lock:
                        _ = stdin.write(packet.payload)
                        stdin.flush()

                elif packet.packet_type == protocol.PACKET_DONE:
                    log.debug("Received DONE, closing git stdin")
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


def _forward_from_git(
    proc: subprocess.Popen[bytes],
    link: ServerLink,
    log: logging.Logger,
):
    def fn():
        stdout = proc.stdout
        if stdout is None:
            return
        send_failed = False
        try:
            while True:
                data = stdout.read(65536)
                if not data:
                    break

                try:
                    link.send(protocol.PackPacket(data).serialize())

                except Exception as e:
                    log.debug("Error sending pack data: %s", e)
                    send_failed = True
                    break

            if not send_failed:
                link.send(protocol.DonePacket().serialize())

            log.debug("Finished forwarding from git")

        except Exception as e:
            log.debug("Error forwarding from git: %s", e)

    return fn


def _pipe_server_data(
    link: ServerLink,
    proc: subprocess.Popen[bytes],
    stdin_lock: threading.Lock,
    service: str,
    log: logging.Logger,
):

    to_git = threading.Thread(
        target=_forward_to_git(proc, link, log, stdin_lock),
        daemon=True,
    )
    from_git = threading.Thread(target=_forward_from_git(proc, link, log), daemon=True)

    to_git.start()
    from_git.start()

    _ = proc.wait()
    log.debug("%s exited with code %d", service, proc.returncode)

    if proc.returncode != 0:
        stderr_data = (
            proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        )
        log.warning("%s exited with code %d: %s", service, proc.returncode, stderr_data)

    to_git.join(timeout=5)
    from_git.join(timeout=5)
