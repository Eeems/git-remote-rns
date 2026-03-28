import argparse
import logging
import os
import subprocess
from collections.abc import Sequence
from typing import cast

import RNS

from . import __version__
from .app import (
    Application,
    Request,
)
from .shared import (
    configure_logging,
    find_repos,
    is_repo,
    is_valid_hexhash,
)


def git(repo: str, *args: str) -> bytes:
    assert app.args is not None
    assert isinstance(app.args.repo, str)  # pyright: ignore[reportAny]
    if ".." in repo:
        raise InvalidRepoPath(repo)

    repo_dir = os.path.join(app.args.repo, repo)
    if not is_repo(repo_dir):
        raise InvalidRepoPath(repo)

    return subprocess.check_output(["git", *args], cwd=repo_dir)


log: logging.Logger = logging.getLogger(__name__)
app = Application(
    "nomadnetwork",
    ["node"],
    templates={
        "repo-link": "`_`[{0}`:/page/repo.mu`repo={0}]`_",
    },
)


@app.request("/page/index.mu", ttl=10, permissions=["read"])
def _(_request: Request) -> bytes | None:
    assert app.args is not None
    assert isinstance(app.args.repo, str)  # pyright: ignore[reportAny]
    return b"> Repositories\n" + b"\n".join(
        [b">> " + app.template("repo-link")(x) for x in find_repos(app.args.repo)]
    )


class InvalidRepoPath(Exception):
    pass


@app.request("/page/repo.mu", ttl=60, permissions=["read"])
def _(_request: Request, repo: str) -> bytes | None:
    return (
        git(repo, "refs", "list") + b"\n" + git(repo, "ls-tree", "--full-tree", "HEAD")
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rngit-web")
    _ = parser.add_argument("repo", help="Path to git repository to serve")
    _ = parser.add_argument(
        "--version",
        action="version",
        version=f"git-remote-rns {__version__}",
    )
    _ = parser.add_argument(
        "-c",
        "--config",
        help="Path to Reticulum config directory",
        dest="config",
    )
    _ = parser.add_argument(
        "-i",
        "--identity",
        help="Path identity file",
        dest="identity",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    _ = parser.add_argument(
        "-n",
        "--name",
        help="Name to annouce",
        dest="name",
        default=f"rngit {__version__}",
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
    args = parser.parse_args(argv)

    assert isinstance(args.repo, str)  # pyright: ignore[reportAny] # nosec B101
    repo_path = os.path.realpath(args.repo)
    if not os.path.exists(repo_path):
        raise FileNotFoundError(repo_path)

    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

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

    assert isinstance(args.name, str)  # pyright: ignore[reportAny] # nosec B101
    name = args.name.encode()

    assert isinstance(args.allow_all_read, bool)  # pyright: ignore[reportAny] # nosec B101
    allow_all_read = args.allow_all_read

    assert isinstance(args.allow_read, list)  # pyright: ignore[reportAny]# nosec B101
    assert all(x for x in args.allow_read if isinstance(x, str))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]# nosec B101
    read_list = set(cast(list[str], args.allow_read))

    for allow in read_list:
        if not is_valid_hexhash(allow):
            raise ValueError(f"Invalid read hexhash: {allow}")

    if allow_all_read and read_list:
        raise ValueError(
            "--allow-read and --allow-all-read cannot be used at the same time"
        )

    configure_logging("rngit-web", logging.DEBUG if verbose else logging.WARNING)

    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    app.identity = identity_path
    app.announce_name = name
    app.announce_interval = announce_interval

    log.info("Destination: %s", RNS.prettyhexrep(app.destination.hash))  # pyright: ignore[reportUnknownMemberType]
    log.info("Read list: %s", "(any)" if allow_all_read else read_list)
    for hexhash in read_list:
        app.permit(hexhash, "read")

    app.run(args)
