import argparse
import logging
import os
import subprocess
import sys
import time
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
_repo_path: str | None = None
_write_list: set[str] = set()
_read_list: set[str] | None = set()


def on_link_closed(link: RNS.Link):
    log.debug("CLOSED: %s %s", link, link.get_remote_identity())  # pyright: ignore[reportUnknownArgumentType]


def on_link_established(link: RNS.Link):
    try:
        log.debug("ESTABLISHED: %s", link)
        link.set_link_closed_callback(on_link_closed)  # pyright: ignore[reportUnknownMemberType]
        link.set_remote_identified_callback(on_identified)  # pyright: ignore[reportUnknownMemberType]

    except Exception:
        traceback.print_exc()
        raise


def on_identified(link: RNS.Link, identity: RNS.Identity):
    try:
        assert link.get_remote_identity() == identity
        _ = RNS.Packet(link, packets.PACKET_IDENTIFIED.value).send()
        log.debug("IDENTIFIED: %s %s", link, identity)

    except Exception:
        traceback.print_exc()
        raise


def identity_allowed_error(
    identity: RNS.Identity | None, allow_list: set[str]
) -> str | None:
    if identity is None:
        return "Not identified"

    if identity.hexhash not in allow_list:
        return "Not allowed"

    return None


def read_allowed_error(identity: RNS.Identity | None) -> str | None:
    global _read_list  # noqa: PLW0602
    if _read_list is None:
        return None

    return identity_allowed_error(identity, _read_list)


def write_allowed_error(identity: RNS.Identity | None) -> str | None:
    global _write_list  # noqa: PLW0602
    return identity_allowed_error(identity, _write_list)


def request_repo_path(data: bytes) -> tuple[str | None, tuple[str, bytes] | None]:
    global _repo_path  # noqa: PLW0602
    try:
        assert isinstance(data, bytes), "data must be bytes"
        assert _repo_path is not None, "_repo_path not set"
        parts = data.split(b"\n", maxsplit=1)
        path = parts[0].decode()
        if ".." in path.split("/"):
            return "Invalid path", None

        base_path = os.path.abspath(_repo_path)
        repo_path = os.path.abspath(os.path.join(base_path, path))
        if os.path.commonpath([base_path, repo_path]) != base_path:
            return "Invalid path", None

        if not os.path.exists(repo_path):
            return "Path not Found", None

        if not os.path.isdir(repo_path):
            return "Path is not directory", None

        proc = subprocess.run(  # nosec B607 B603# nosec B607 B603
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_path,
            capture_output=True,
            check=False,
        )
        if proc.returncode:
            return (
                proc.stderr.rstrip().decode()
                or proc.stdout.rstrip().decode()
                or "Unknown error",
                None,
            )

        git_dir = proc.stdout.rstrip().decode()
        if git_dir not in (".", ".git"):
            return "Path not a valid repository", None

        return (None, (repo_path, b"" if len(parts) == 1 else parts[1]))

    except Exception as e:
        traceback.print_exc()
        return str(e), None


def log_request(path: str, repo_path: str, *args: object):
    global _repo_path  # noqa: PLW0602
    repo_path = os.path.relpath(repo_path, _repo_path)
    log.debug("REQUEST %s %s %s", path, repo_path, " ".join(f"{f}" for f in args))


def on_list_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
) -> bytes | None:
    try:
        err = (
            write_allowed_error(remote_identity)
            if path == "list-for-push"
            else read_allowed_error(remote_identity)
        )
        if err is not None:
            return b"\1" + err.encode()

        err, res = request_repo_path(data)
        if err is not None:
            return b"\1" + err.encode()

        assert res is not None
        repo_path, data = res

        log_request(path, repo_path)
        head_path = os.path.join(repo_path, ".git", "HEAD")
        if not os.path.exists(head_path):
            head_path = os.path.join(repo_path, "HEAD")

        with open(head_path, "rb") as f:
            ref = f.read()[5:].rstrip().decode()

        proc = subprocess.run(  # nosec B607 B603
            ["git", "refs", "list", "--format", "%(objectname) %(refname)"],
            text=False,
            cwd=repo_path,
            capture_output=True,
            check=False,
        )
        log.debug("git refs list code: %d", proc.returncode)
        if proc.returncode:
            return proc.returncode.to_bytes(1, "big") + proc.stderr

        return b"\0" + proc.stdout + f"@{ref} HEAD\n".encode()

    except Exception as e:
        traceback.print_exc()
        return b"\1" + str(e).encode()


def on_fetch_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
) -> bytes | None:
    try:
        err = read_allowed_error(remote_identity)
        if err is not None:
            return b"\1" + err.encode()

        err, res = request_repo_path(data)
        if err is not None:
            return b"\1" + err.encode()

        assert res is not None
        repo_path, data = res

        sha, ref = data.decode().split(" ", maxsplit=1)
        log_request(path, repo_path, sha, ref)
        with TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, f"{sha}.bundle")
            proc = subprocess.run(  # nosec B607 B603
                ["git", "bundle", "create", "--no-progress", bundle, ref],
                cwd=repo_path,
                capture_output=True,
                check=False,
            )
            log.debug("git bundle create return code: %d", proc.returncode)
            if proc.returncode:
                return proc.returncode.to_bytes(1, "big") + proc.stderr

            with open(bundle, "rb") as f:
                return b"\0" + f.read()

    except Exception as e:
        traceback.print_exc()
        return b"\1" + str(e).encode()


def on_push_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
) -> bytes | None:
    try:
        err = write_allowed_error(remote_identity)
        if err is not None:
            return b"\1" + err.encode()

        err, res = request_repo_path(data)
        if err is not None:
            return b"\1" + err.encode()

        assert res is not None
        repo_path, data = res

        info, data = data.split(b"\n", maxsplit=1)
        local_ref, remote_ref = info.decode().split(":", maxsplit=1)
        force = local_ref.startswith("+")
        if force:
            local_ref = local_ref[1:]

        log_request(path, repo_path, local_ref, remote_ref, "(force) " if force else "")
        with TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            with open(bundle, "wb") as f:
                _ = f.write(data)

            proc = subprocess.run(  # nosec B607 B603
                ["git", "bundle", "verify", bundle],
                cwd=repo_path,
                capture_output=True,
                check=False,
            )
            log.debug("git bundle verify return code: %d", proc.returncode)
            if proc.returncode:
                return proc.returncode.to_bytes(1, "big") + proc.stderr

            proc = subprocess.run(  # nosec B607 B603
                [
                    "git",
                    "fetch",
                    bundle,
                    f"{local_ref}:{remote_ref}",
                    *(["--force"] if force else []),
                ],
                cwd=repo_path,
                capture_output=True,
                check=False,
            )
            log.debug("git bundle unbundle return code: %d", proc.returncode)
            if proc.returncode:
                return proc.returncode.to_bytes(1, "big") + proc.stderr

        return b"\0"

    except Exception as e:
        traceback.print_exc()
        return b"\1" + str(e).encode()


def on_delete_request(
    path: str,
    data: bytes,
    _request_id: bytes,
    remote_identity: RNS.Identity | None,
    _request_at: float,
):
    try:
        err = write_allowed_error(remote_identity)
        if err is not None:
            return b"\1" + err.encode()

        err, res = request_repo_path(data)
        if err is not None:
            return b"\1" + err.encode()

        assert res is not None
        repo_path, data = res

        ref = data.decode()
        if not ref:
            return b"\1No ref supplied"

        log_request(path, repo_path, ref)
        proc = subprocess.run(  # nosec B607 B603
            ["git", "update-ref", "-d", ref],
            cwd=repo_path,
            capture_output=True,
            check=False,
        )
        log.debug("git update-ref return code: %d", proc.returncode)
        if proc.returncode:
            return proc.returncode.to_bytes(1, "big") + proc.stderr

        return b"\0"

    except Exception as e:
        traceback.print_exc()
        return b"\1" + str(e).encode()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RNS Git Server", allow_abbrev=False)
    _ = parser.add_argument("repo", help="Path to git repository to serve")
    _ = parser.add_argument(
        "-c",
        "--config",
        help="Path to Reticulum config directory",
        dest="config",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    _ = parser.add_argument(
        "-i",
        "--identity",
        help="Path to identity file",
        dest="identity",
    )
    _ = parser.add_argument(
        "-n",
        "--name",
        help="Name to announce",
        dest="name",
        default=f"rngit {__version__}",
    )
    _ = parser.add_argument(
        "--version",
        action="version",
        version=f"rngit {__version__}",
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
    _ = parser.add_argument(
        "-A",
        "--allow-all-read",
        action="store_true",
        dest="allow_all_read",
        help="Allow any connection to read the repository",
    )
    _ = parser.add_argument(
        "-N",
        "--nomadnet",
        action="store_true",
        help="Enable the nomadnet host",
        dest="nomadnet",
    )
    args = parser.parse_args(argv)

    assert isinstance(args.repo, str)  # pyright: ignore[reportAny]
    repo_path = os.path.realpath(args.repo)
    if not os.path.exists(repo_path):
        raise FileNotFoundError(repo_path)

    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

    global _repo_path
    _repo_path = repo_path

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    verbose = args.verbose

    assert isinstance(args.nomadnet, bool)  # pyright: ignore[reportAny]
    nomadnet = args.nomadnet

    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    identity_path = args.identity

    assert isinstance(args.announce_interval, int | None)  # pyright: ignore[reportAny]
    announce_interval = args.announce_interval

    assert isinstance(args.name, str)  # pyright: ignore[reportAny]
    name = args.name.encode()

    assert isinstance(args.allow_all_read, bool)  # pyright: ignore[reportAny]
    allow_all_read = args.allow_all_read

    assert isinstance(args.allow_read, list)  # pyright: ignore[reportAny]
    assert all(isinstance(x, str) for x in args.allow_read)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    read_list = set(cast(list[str], args.allow_read))

    for allow in read_list:
        if not is_valid_hexhash(allow):
            raise ValueError(f"Invalid read hexhash: {allow}")

    if allow_all_read and read_list:
        raise ValueError(
            "--allow-read and --allow-all-read cannot be used at the same time"
        )

    assert isinstance(args.allow_write, list)  # pyright: ignore[reportAny]
    assert all(isinstance(x, str) for x in args.allow_write)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    write_list = set(cast(list[str], args.allow_write))

    global _write_list
    _write_list = write_list

    for allow in write_list:
        if not is_valid_hexhash(allow):
            raise ValueError(f"Invalid write hexhash: {allow}")

    read_list |= write_list

    global _read_list
    _read_list = None if allow_all_read else read_list

    configure_logging("rngit", logging.DEBUG if verbose else logging.WARNING)

    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType]
    if identity_path is None:
        identity_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    assert identity_path is not None
    log.info("Identity: %s", identity_path)
    identity: RNS.Identity | None = None
    if os.path.exists(identity_path):
        identity = RNS.Identity.from_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    if identity is None:
        identity = RNS.Identity(True)
        _ = identity.to_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    assert identity is not None
    assert identity.hexhash is not None

    server_destination = RNS.Destination(
        identity,
        RNS.Destination.IN,
        RNS.Destination.SINGLE,
        APP_NAME,
    )

    log.info("Destination: %s", RNS.prettyhexrep(server_destination.hash))  # pyright: ignore[reportUnknownMemberType]
    log.info("Read list: %s", "(any)" if allow_all_read else read_list)
    log.info("Write list: %s", write_list)
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "list",
        on_list_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "list-for-push",
        on_list_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "fetch",
        on_fetch_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "push",
        on_push_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
        "delete",
        on_delete_request,
        RNS.Destination.ALLOW_ALL,
    )
    server_destination.set_link_established_callback(on_link_established)  # pyright: ignore[reportUnknownMemberType]
    _ = server_destination.announce(name)  # pyright: ignore[reportUnknownMemberType]

    process: subprocess.Popen[bytes] | None = None
    if nomadnet:
        process = subprocess.Popen(
            [
                *(
                    []
                    if "__compiled__" in globals()
                    else [
                        sys.executable,
                        "-m",
                        "rngit",
                    ]
                ),
                "rngit-web",
                repo_path,
                f"--identity={identity_path}",
                *(["--verbose"] if verbose else []),
                f"--name={name.decode()}",
                *(
                    []
                    if announce_interval is None
                    else [f"--announce-interval={announce_interval}"]
                ),
                *(["--allow-all-read"] if allow_all_read else []),
                *([] if allow_all_read else [f"--allow-read={x}" for x in read_list]),
            ],
        )

    if announce_interval is None:
        while True:
            if process is not None and process.poll() is not None:
                return process.returncode

            time.sleep(10)

    while True:
        if process is not None and process.poll() is not None:
            return process.returncode

        time.sleep(announce_interval)
        log.debug("Sending announce")
        _ = server_destination.announce(name)  # pyright: ignore[reportUnknownMemberType]
