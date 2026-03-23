import argparse
import logging
import os
import subprocess
import time
import traceback

import RNS

from collections.abc import Sequence
from tempfile import TemporaryDirectory

from . import __version__
from .shared import (
    configure_logging,
    APP_NAME,
)

__all__ = [
    "main",
]

log: logging.Logger = logging.getLogger(__name__)
_repo_path: str | None = None


def on_link_closed(link: RNS.Link):
    log.debug(f"CLOSED: {link}")


def on_link_established(link: RNS.Link):
    try:
        log.debug(f"ESTABLISHED: {link}")
        link.set_link_closed_callback(on_link_closed)  # pyright: ignore[reportUnknownMemberType]

    except Exception:
        traceback.print_exc()
        raise


def on_list_request(
    path: str,
    _data: bytes,
    _request_id: bytes,
    _link_id: RNS.Identity,
    _remote_identity: RNS.Identity,
) -> bytes | None:
    log.debug(f"REQUEST {path}")
    global _repo_path
    assert _repo_path is not None
    head_path = os.path.join(_repo_path, ".git", "HEAD")
    if not os.path.exists(head_path):
        head_path = os.path.join(_repo_path, "HEAD")

    with open(head_path, "r") as f:
        ref = f.read()[5:].rstrip()

    return (
        subprocess.check_output(
            ["git", "refs", "list", "--format", "%(objectname) %(refname)"],
            text=False,
            cwd=_repo_path,
        )
        + f"@{ref} HEAD\n".encode()
    )


def on_fetch_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    _link_id: RNS.Identity,
    _remote_identity: RNS.Identity,
) -> bytes | None:
    global _repo_path
    assert _repo_path is not None
    sha, ref = data.decode().split(" ", maxsplit=1)
    log.debug(f"REQUEST {path} {sha} {ref}")
    with TemporaryDirectory() as tmpdir:
        bundle = os.path.join(tmpdir, f"{sha}.bundle")
        _ = subprocess.check_call(
            ["git", "bundle", "create", bundle, ref], cwd=_repo_path
        )
        with open(bundle, "rb") as f:
            return f.read()


def main(argv: Sequence[str] | None = None) -> int:
    global _repo_path
    parser = argparse.ArgumentParser(description="RNS Git Server", allow_abbrev=False)
    _ = parser.add_argument("repo", help="Path to git repository to serve")
    _ = parser.add_argument(
        "-c", "--config", help="Path to Reticulum config directory", dest="config"
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    _ = parser.add_argument(
        "-i", "--identity", help="Path identity file", dest="identity"
    )
    _ = parser.add_argument(
        "--version", action="version", version=f"rngit {__version__}"
    )
    _ = parser.add_argument(
        "-a",
        "--announce-interval",
        type=int,
        default=None,
        help="Interval in seconds between announces (default: announce once)",
        dest="announce_interval",
    )
    args = parser.parse_args(argv)

    assert isinstance(args.repo, str)  # pyright: ignore[reportAny] # nosec B101
    repo_path = os.path.realpath(args.repo)
    if not os.path.exists(repo_path):
        raise FileNotFoundError(repo_path)

    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

    _repo_path = repo_path

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny] # nosec B101
    config_path = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny] # nosec B101
    verbose = args.verbose

    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny] # nosec B101
    identity_path = args.identity

    assert isinstance(args.announce_interval, int | None)  # pyright: ignore[reportAny] # nosec B101
    announce_interval = args.announce_interval

    configure_logging(logging.DEBUG if verbose else logging.WARNING)

    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType]
    if identity_path is None:
        identity_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    assert identity_path is not None
    log.info(f"Identity: {identity_path}")
    identity: RNS.Identity | None = None
    if os.path.exists(identity_path):
        identity = RNS.Identity.from_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    if identity is None:
        identity = RNS.Identity(True)
        _ = identity.to_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    assert identity is not None
    assert identity.hexhash is not None  # nosec B101

    server_destination = RNS.Destination(
        identity,
        RNS.Destination.IN,
        RNS.Destination.SINGLE,
        APP_NAME,
    )
    log.info(f"Destination: {RNS.prettyhexrep(server_destination.hash)}")  # pyright: ignore[reportUnknownMemberType]
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "list",
        on_list_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "fetch",
        on_fetch_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.set_link_established_callback(on_link_established)  # pyright: ignore[reportUnknownMemberType]

    _ = server_destination.announce()  # pyright: ignore[reportUnknownMemberType]
    if announce_interval is None:
        while True:
            time.sleep(10)

    last_announce = time.time()
    while True:
        current = time.time()
        if last_announce + announce_interval >= current:
            _ = server_destination.announce()  # pyright: ignore[reportUnknownMemberType]
            last_announce = current

        time.sleep(0.1)
