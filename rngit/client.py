import argparse
import logging
import os
import subprocess  # noqa: B404
import sys
import threading
import traceback
from collections.abc import Sequence
from tempfile import TemporaryDirectory
from typing import cast

import RNS

from . import __version__
from .shared import (
    APP_NAME,
    configure_logging,
    is_valid_hexhash,
    packets,
)

__all__ = [
    "main",
]

log: logging.Logger = logging.getLogger(__name__)

_linkEvent: threading.Event = threading.Event()
_identity: RNS.Identity | None = None
_repo_path: str | None = None


def on_link_established(link: RNS.Link):
    global _identity  # pylint: disable=W0602 # noqa: F999
    assert _identity is not None  # nosec B101
    log.debug("ESTABLISHED: %s", link)
    link.set_packet_callback(on_packet)  # pyright: ignore[reportUnknownMemberType]
    _ = link.identify(_identity)  # pyright: ignore[reportUnknownMemberType]


def on_link_closed(link: RNS.Link):
    global _linkEvent  # pylint: disable=W0602 # noqa: F999
    log.debug("CLOSED: %s", link)
    _linkEvent.clear()


def on_packet(message: bytes, _packet: RNS.Packet):
    global _linkEvent  # pylint: disable=W0602 # noqa: F999
    log.debug("PACKET: %s", message)
    match message:
        case packets.PACKET_IDENTIFIED.value:
            _linkEvent.set()

        case _:
            log.error("Invalid packet: %d", message)


def request(
    link: RNS.Link, path: str, data: bytes = b""
) -> tuple[str | None, bytes | None]:
    global _repo_path  # pylint: disable=W0602 # noqa: F999
    assert _repo_path is not None  # nosec B101
    event = threading.Event()
    log.debug("REQUEST %s", path)
    receipt = link.request(  # pyright: ignore[reportUnknownMemberType]
        path,
        _repo_path.encode() + b"\n" + data,
        response_callback=lambda _, e=event: e.set(),  # pyright: ignore[reportUnknownLambdaType]
        failed_callback=lambda _, e=event: e.set(),  # pyright: ignore[reportUnknownLambdaType]
    )
    if not receipt:
        return "Failed to send request", None

    _ = event.wait()
    match receipt.get_status():
        case RNS.RequestReceipt.FAILED:
            return "Failed to send request", None

        case RNS.RequestReceipt.READY:
            data = receipt.get_response()  # pyright: ignore[reportUnknownVariableType, reportAssignmentType]
            assert isinstance(data, bytes)  # nosec B101
            returncode = int.from_bytes(data[0:1], "big")
            if returncode:
                return "Remote error: " + data[1:].decode(), None

            return None, data[1:]

        case _:
            return f"Invalid status: {receipt.get_status()}", None


def main(argv: Sequence[str] | None = None) -> int:  # noqa: MC0001
    parser = argparse.ArgumentParser(prog="git-remote-rns")
    _ = parser.add_argument("remote", help="Remote name (ignored)")
    _ = parser.add_argument("url", help="Remote URL (<hash>[/path])")
    _ = parser.add_argument(
        "--version", action="version", version=f"git-remote-rns {__version__}"
    )
    _ = parser.add_argument(
        "-i", "--identity", help="Path identity file", dest="identity"
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    args = parser.parse_args(argv)

    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny] # nosec B101
    identity_path = args.identity

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny] # nosec B101
    verbose = args.verbose or bool(os.environ.get("VERBOSE", 0))
    configure_logging("git-remote-rns", logging.DEBUG if verbose else logging.WARNING)

    assert isinstance(args.url, str)  # pyright: ignore[reportAny] # nosec B101
    url = args.url
    parts = url.split("/", 1)
    destination_hexhash = parts[0]
    if not is_valid_hexhash(destination_hexhash):
        log.error("error: Invalid URL. Hexhash invalid: %s", destination_hexhash)
        return 1

    destination = bytes.fromhex(destination_hexhash)

    global _repo_path
    _repo_path = parts[1] if len(parts) > 1 else "."

    config_path = os.environ.get("RNS_CONFIG_PATH", None)
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType] # nosec B101
    if identity_path is None:
        identity_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    assert identity_path is not None  # nosec B101
    log.info("Identity: %s", identity_path)
    log.info("Destination: %s", destination_hexhash)
    identity: RNS.Identity | None = None
    if os.path.exists(identity_path):
        identity = RNS.Identity.from_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    if identity is None:
        identity = RNS.Identity(True)
        _ = identity.to_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    global _identity
    _identity = identity

    if not RNS.Transport.has_path(destination):  # pyright: ignore[reportUnknownMemberType]
        RNS.Transport.request_path(destination)  # pyright: ignore[reportUnknownMemberType]
        if not RNS.Transport.await_path(destination, 30):  # pyright: ignore[reportUnknownMemberType]
            log.error("Timed out waiting for path")
            return 1

    server_identity = RNS.Identity.recall(destination)  # pyright: ignore[reportUnknownMemberType]
    if server_identity is None:
        log.error("Failed to get server identity")
        return 1

    server_destination = RNS.Destination(
        server_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        APP_NAME,
    )
    link = RNS.Link(server_destination, on_link_established, on_link_closed)
    push_queue: list[tuple[str, str]] = []
    fetch_queue: list[tuple[str, str]] = []
    global _linkEvent  # pylint: disable=W0602 # noqa: F999
    try:  # pylint: disable=too-many-nested-blocks
        for line in sys.stdin:
            _ = _linkEvent.wait()
            if not line:
                continue

            log.debug("STDIN '%s'", line.encode())

            parts = cast(list[str], line.split(maxsplit=1))
            assert isinstance(parts, list)  # nosec B101
            if not parts:
                log.debug("\\n")
                while push_queue:
                    local_ref, remote_ref = push_queue.pop(0)
                    if local_ref.startswith("+"):
                        local_ref = local_ref[1:]

                    if not local_ref:
                        err, data = request(
                            link,
                            "delete",
                            remote_ref.encode(),
                        )
                        if err is not None:
                            _ = sys.stderr.write(err)
                            _ = sys.stderr.write("\n")
                            return 1

                        if data:
                            _ = sys.stderr.buffer.write(data)
                            _ = sys.stderr.buffer.write(b"\n")

                    else:
                        with TemporaryDirectory() as tmpdir:
                            bundle = os.path.join(tmpdir, "bundle")
                            _ = subprocess.check_call(  # nosec B607 B603
                                [
                                    "git",
                                    "bundle",
                                    "create",
                                    "--progress",
                                    bundle,
                                    local_ref,
                                ]
                            )
                            with open(bundle, "rb") as f:
                                data = f.read()

                            err, data = request(
                                link,
                                "push",
                                f"{local_ref}:{remote_ref}\n".encode() + data,
                            )
                            if err is not None:
                                msg = f'error {remote_ref} "{err}"\n'
                                log.debug(msg)
                                _ = sys.stdout.write(msg)
                                return 1

                            assert not data  # nosec B101
                            msg = f"ok {remote_ref}\n"
                            log.debug(msg)
                            _ = sys.stdout.write(msg)

                while fetch_queue:
                    sha, ref = fetch_queue.pop(0)
                    err, data = request(link, "fetch", f"{sha} {ref}".encode())
                    if err is not None:
                        _ = sys.stderr.write(err)
                        _ = sys.stderr.write("\n")
                        return 1

                    assert data is not None  # nosec B101
                    with TemporaryDirectory() as tmpdir:
                        bundle = os.path.join(tmpdir, f"{sha}.bundle")
                        with open(bundle, "wb") as f:
                            _ = f.write(data)

                        _ = subprocess.check_call(  # nosec B607 B603
                            ["git", "bundle", "verify", "--quiet", bundle],
                            stderr=subprocess.DEVNULL,
                        )
                        _ = subprocess.check_call(  # nosec B607 B603
                            ["git", "bundle", "unbundle", "--progress", bundle, ref],
                            stdout=subprocess.DEVNULL,
                        )

                _ = sys.stderr.flush()
                try:
                    _ = sys.stdout.write("\n")
                    _ = sys.stdout.flush()

                except BrokenPipeError:
                    break

                continue

            match parts[0]:
                case "capabilities":
                    log.debug("CAPABILITIES")
                    _ = sys.stdout.write("list\n")
                    _ = sys.stdout.write("fetch\n")
                    _ = sys.stdout.write("push\n")
                    _ = sys.stdout.write("\n")
                    _ = sys.stdout.flush()

                case "fetch":
                    push_queue.clear()
                    sha, ref = parts[1].rstrip().split(" ", maxsplit=1)
                    log.debug("FETCH %s %s", sha, ref)
                    fetch_queue.append((sha, ref))

                case "push":
                    fetch_queue.clear()
                    local_ref, remote_ref = parts[1].rstrip().split(":", maxsplit=1)
                    log.debug("PUSH %s %s", local_ref, remote_ref)
                    push_queue.append((local_ref, remote_ref))

                case "list":
                    log.debug("LIST")
                    path = "list"
                    if len(parts) > 1 and "for-push" in parts[1]:
                        path = "list-for-push"

                    err, data = request(link, path)
                    if err is not None:
                        _ = sys.stderr.write(err)
                        _ = sys.stderr.write("\n")
                        return 1

                    assert data is not None  # nosec B101
                    _ = sys.stdout.buffer.write(data)
                    _ = sys.stdout.write("\n")
                    _ = sys.stdout.flush()

                case _:
                    _ = sys.stderr.write(f"Unknown command: {parts[0]}\n")
                    return 1

        log.debug("End of stdin")

    except Exception:
        log.error(traceback.format_exc())
        return 1

    finally:
        log.debug("Closing link")
        link.teardown()

    return 0
