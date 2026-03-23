import logging
import os
import sys
import argparse
import threading
import subprocess
import traceback
import signal

import RNS

from collections.abc import Sequence
from typing import cast
from tempfile import TemporaryDirectory

from . import __version__
from .shared import (
    configure_logging,
    APP_NAME,
    is_valid_hexhash,
)

__all__ = [
    "main",
]

log: logging.Logger = logging.getLogger(__name__)

_linkEvent: threading.Event = threading.Event()


def on_link_established(link: RNS.Link):
    global _linkEvent
    log.debug(f"ESTABLISHED: {link}")
    _linkEvent.set()


def on_link_closed(link: RNS.Link):
    global _linkEvent
    log.debug(f"CLOSED: {link}")
    _linkEvent.clear()


def request(
    link: RNS.Link, path: str, data: bytes | None = None
) -> tuple[str | None, bytes | None]:
    event = threading.Event()
    receipt = link.request(  # pyright: ignore[reportUnknownMemberType]
        path,
        data,
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
            data = receipt.get_response()  # pyright: ignore[reportUnknownVariableType]
            assert isinstance(data, bytes)
            return None, data

        case _:
            return f"Invalid status: {receipt.get_status()}", None


def main(argv: Sequence[str] | None = None) -> int:
    global _linkEvent
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
    configure_logging(logging.DEBUG if verbose else logging.WARNING)

    assert isinstance(args.url, str)  # pyright: ignore[reportAny] # nosec B101
    url = args.url
    parts = url.split("/", 1)
    destination_hexhash = parts[0]
    if not is_valid_hexhash(destination_hexhash):
        log.error(f"error: Invalid URL. Hexhash invalid: {destination_hexhash}")
        return 1

    destination = bytes.fromhex(destination_hexhash)

    repo_path = parts[1] if len(parts) > 1 else ""

    config_path = os.environ.get("RNS_CONFIG_PATH", None)
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType]
    if identity_path is None:
        identity_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    assert identity_path is not None
    log.info(f"Identity: {identity_path}")
    log.info(f"Destination: {destination_hexhash}")
    identity: RNS.Identity | None = None
    if os.path.exists(identity_path):
        identity = RNS.Identity.from_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    if identity is None:
        identity = RNS.Identity(True)
        _ = identity.to_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

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
    try:
        for line in sys.stdin:
            _ = _linkEvent.wait()
            if not line:
                continue

            log.debug(f"STDIN '{line.rstrip()}'")

            parts = cast(list[str], line.split(maxsplit=1))
            assert isinstance(parts, list)
            if not parts:
                log.debug("\\n")
                _ = sys.stdout.write("\n")
                try:
                    _ = sys.stdout.flush()

                except BrokenPipeError:
                    # Ignoring as git likes to close stdout early
                    pass

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
                    sha, ref = parts[1].rstrip().split(" ", maxsplit=1)
                    log.debug(f"FETCH {sha} {ref}")
                    err, data = request(link, "fetch", f"{sha} {ref}".encode())
                    if err is not None:
                        _ = sys.stderr.write(err)
                        _ = sys.stderr.write("\n")
                        _ = sys.stderr.flush()
                        return 1

                    assert data is not None
                    with TemporaryDirectory() as tmpdir:
                        bundle = os.path.join(tmpdir, f"{sha}.bundle")
                        with open(bundle, "wb") as f:
                            _ = f.write(data)

                        _ = subprocess.check_call(
                            ["git", "bundle", "unbundle", bundle, ref],
                            stdout=subprocess.DEVNULL,
                        )

                case "push":
                    local_ref, remote_ref = parts[1].rstrip().split(":", maxsplit=1)
                    log.debug(f"PUSH {local_ref} {remote_ref}")

                case "list":
                    log.debug("LIST")
                    err, data = request(link, "list")
                    if err is not None:
                        _ = sys.stderr.write(err)
                        _ = sys.stderr.write("\n")
                        _ = sys.stderr.flush()
                        return 1

                    assert data is not None
                    _ = sys.stdout.buffer.write(data)
                    _ = sys.stdout.write("\n")
                    _ = sys.stdout.flush()

                case _:
                    _ = sys.stderr.write(f"Unknown command: {parts[1]}\n")
                    _ = sys.stderr.flush()
                    return 1

        log.debug("End of stdin")
        _ = signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    except Exception:
        log.error(traceback.format_exc())
        return 1

    finally:
        log.debug("Closing link")
        link.teardown()

    return 0
