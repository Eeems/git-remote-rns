import argparse
import logging
import os
import subprocess
import time
import traceback
import struct

import RNS

from typing import cast
from collections.abc import Sequence
from tempfile import TemporaryDirectory

from . import __version__
from .shared import (
    configure_logging,
    APP_NAME,
    is_valid_hexhash,
    packets,
)

__all__ = [
    "main",
]

log: logging.Logger = logging.getLogger(__name__)
_repo_path: str | None = None
_write_list: set[str] = set()
_read_list: set[str] = set()


def on_link_closed(link: RNS.Link):
    log.debug(f"CLOSED: {link} {link.get_remote_identity()}")


def on_link_established(link: RNS.Link):
    try:
        log.debug(f"ESTABLISHED: {link}")
        link.set_link_closed_callback(on_link_closed)  # pyright: ignore[reportUnknownMemberType]
        link.set_remote_identified_callback(on_identified)  # pyright: ignore[reportUnknownMemberType]

    except Exception:
        traceback.print_exc()
        raise


def on_identified(link: RNS.Link, identity: RNS.Identity):
    try:
        assert link.get_remote_identity() == identity
        _ = RNS.Packet(link, packets.PACKET_IDENTIFIED.value).send()
        log.debug(f"IDENTIFIED: {link} {identity}")

    except Exception:
        traceback.print_exc()
        raise


def on_list_request(
    path: str,
    _data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
) -> bytes | None:
    try:
        global _read_list
        if remote_identity is None:
            return b"\1Not allowed"

        if (
            remote_identity.hexhash not in _write_list
            if path == "list-for-push"
            else _read_list
        ):
            return b"\1Not allowed"

        log.debug(f"REQUEST {path}")
        global _repo_path
        assert _repo_path is not None
        head_path = os.path.join(_repo_path, ".git", "HEAD")
        if not os.path.exists(head_path):
            head_path = os.path.join(_repo_path, "HEAD")

        with open(head_path, "r") as f:
            ref = f.read()[5:].rstrip()

        proc = subprocess.run(
            ["git", "refs", "list", "--format", "%(objectname) %(refname)"],
            text=False,
            cwd=_repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        log.debug(f"git refs list code: {proc.returncode}")
        if proc.returncode:
            return proc.returncode.to_bytes(1, "big") + proc.stderr

        return b"\0" + proc.stdout + f"@{ref} HEAD\n".encode()

    except Exception:
        return b"\1" + traceback.format_exc().encode()


def on_fetch_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
) -> bytes | None:
    try:
        global _read_list
        if remote_identity is None:
            return b"\1Not allowed"

        if remote_identity.hexhash not in _read_list:
            return b"\1Not allowed"

        global _repo_path
        assert _repo_path is not None
        sha, ref = data.decode().split(" ", maxsplit=1)
        log.debug(f"REQUEST {path} {sha} {ref}")
        with TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, f"{sha}.bundle")
            proc = subprocess.run(
                ["git", "bundle", "create", "--no-progress", bundle, ref],
                cwd=_repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.debug(f"git bundle create return code: {proc.returncode}")
            if proc.returncode:
                return proc.returncode.to_bytes(1, "big") + proc.stderr

            with open(bundle, "rb") as f:
                return b"\0" + f.read()

    except Exception:
        return b"\1" + traceback.format_exc().encode()


def on_push_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
) -> bytes | None:
    try:
        global _write_list
        if remote_identity is None:
            return b"\1Not allowed"

        if remote_identity.hexhash not in _write_list:
            return b"\1Not allowed"

        global _repo_path
        assert _repo_path is not None
        info, data = data.split(b"\n", maxsplit=1)
        local_ref, remote_ref = info.decode().split(":", maxsplit=1)
        force = local_ref.startswith("+")
        if force:
            local_ref = local_ref[1:]

        log.debug(
            f"REQUEST {path} {'(force) ' if force else ''}{local_ref} {remote_ref}"
        )
        with TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            with open(bundle, "wb") as f:
                _ = f.write(data)

            proc = subprocess.run(
                ["git", "bundle", "verify", bundle],
                cwd=_repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.debug(f"git bundle verifyreturn code: {proc.returncode}")
            if proc.returncode:
                return proc.returncode.to_bytes(1, "big") + proc.stderr

            proc = subprocess.run(
                [
                    "git",
                    "fetch",
                    bundle,
                    f"{local_ref}:{remote_ref}",
                    *(["--force"] if force else []),
                ],
                cwd=_repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.debug(f"git bundle unbundle return code: {proc.returncode}")

        return proc.returncode.to_bytes(1, "big") + proc.stderr

    except Exception:
        return b"\1" + traceback.format_exc().encode()


def on_delete_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
):
    try:
        global _write_list
        if remote_identity is None:
            return b"\1Not allowed"

        if remote_identity.hexhash not in _write_list:
            return b"\1Not allowed"

        global _repo_path
        assert _repo_path is not None
        ref = data
        log.debug(f"REQUEST {path} {data}")

        proc = subprocess.run(
            ["git", "update-ref", "-d", ref],
            cwd=_repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        log.debug(f"git update-ref return code: {proc.returncode}")
        return b"\0" + proc.stderr

    except Exception:
        return b"\1" + traceback.format_exc().encode()


def main(argv: Sequence[str] | None = None) -> int:
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
    _ = parser.add_argument(
        "-w",
        "--allow-write",
        action="append",
        default=[],
        help="Identities allowed to write to the repository. Will automatically be allowed to read.",
        dest="allow_write",
    )
    _ = parser.add_argument(
        "-r",
        "--allow-read",
        action="append",
        default=[],
        help="Identities allowed to read the repository",
        dest="allow_read",
    )
    args = parser.parse_args(argv)

    assert isinstance(args.repo, str)  # pyright: ignore[reportAny] # nosec B101
    repo_path = os.path.realpath(args.repo)
    if not os.path.exists(repo_path):
        raise FileNotFoundError(repo_path)

    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

    global _repo_path
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

    assert isinstance(args.allow_read, list)  # pyright: ignore[reportAny]
    assert all(x for x in args.allow_read if isinstance(x, str))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    read_list = set(cast(list[str], args.allow_read))

    for allow in read_list:
        if not is_valid_hexhash(allow):
            raise ValueError(f"Invalid read hexhash: {allow}")

    assert isinstance(args.allow_write, list)  # pyright: ignore[reportAny]
    assert all(x for x in args.allow_write if isinstance(x, str))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    write_list = set(cast(list[str], args.allow_write))

    global _write_list
    _write_list = write_list
    global _read_list
    _read_list = read_list

    for allow in write_list:
        if not is_valid_hexhash(allow):
            raise ValueError(f"Invalid write hexhash: {allow}")

    read_list |= write_list

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

    log.info("Destination: %s", RNS.prettyhexrep(server_destination.hash))  # pyright: ignore[reportUnknownMemberType]
    log.info("Read list: %s", read_list)
    log.info("Write list: %s", write_list)
    allow_list = (bytes.fromhex(x) for x in read_list)
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "list",
        on_list_request,
        RNS.Destination.ALLOW_ALL,
        allow_list,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "list-for-push",
        on_list_request,
        RNS.Destination.ALLOW_ALL,
        allow_list,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "fetch",
        on_fetch_request,
        RNS.Destination.ALLOW_ALL,
        allow_list,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "push",
        on_push_request,
        RNS.Destination.ALLOW_ALL,
        allow_list,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "delete",
        on_delete_request,
        RNS.Destination.ALLOW_ALL,
        allow_list,
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
