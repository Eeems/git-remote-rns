# pylint: disable=R0801
import argparse
import logging
import math
import os
import subprocess
import traceback
from collections.abc import (
    Generator,
    Sequence,
)
from datetime import datetime
from typing import cast

import RNS
from humanize import naturalday

from . import (
    __version__,
    micron,
)
from .app import (
    Application,
    Request,
    SpecialPermissions,
)
from .shared import (
    configure_logging,
    find_repos,
    is_repo,
    is_valid_hexhash,
)


class InvalidRepoPath(Exception):
    pass


def repo_dir(repo: str) -> str:
    if ".." in repo.split("/"):
        raise InvalidRepoPath("Paths cannot contain ..")

    repo = os.path.normpath(repo)
    assert app.args is not None
    assert isinstance(app.args.repo, str)  # pyright: ignore[reportAny]
    base_path = os.path.abspath(app.args.repo)
    path = os.path.abspath(os.path.join(base_path, repo))
    if os.path.commonpath([base_path, path]) != base_path:
        raise InvalidRepoPath("Path traversal detected ..")

    if not is_repo(path):
        raise InvalidRepoPath(f"{repo} is not a repository")

    return path


def git(repo: str, *args: str, timeout: float | None = 10.0) -> bytes:
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=repo_dir(repo),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode:
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            proc.stdout,
            proc.stderr,
        )

    return proc.stdout


def refs(repo: str) -> list[tuple[str, str]]:
    try:
        return [
            cast(tuple[str, str], tuple(x.split(" ", 1)))
            for x in git(repo, "show-ref", "--head").decode().splitlines(False)
        ]

    except subprocess.CalledProcessError as e:
        if e.stdout or e.stderr:  # pyright: ignore[reportAny]
            raise

        return []


def branches(repo: str) -> list[str]:
    try:
        return (
            git(repo, "branch", "--format=%(refname:short)").decode().splitlines(False)
        )

    except subprocess.CalledProcessError as e:
        if e.stdout or e.stderr:  # pyright: ignore[reportAny]
            raise

        return []


def tags(repo: str) -> list[str]:
    try:
        return git(repo, "tag", "--format=%(refname:short)").decode().splitlines(False)

    except subprocess.CalledProcessError as e:
        if e.stdout or e.stderr:  # pyright: ignore[reportAny]
            raise

        return []


def tree(repo: str, ref: str) -> Generator[tuple[str, str, str, str], None, None]:
    try:
        for line in git(repo, "ls-tree", ref).decode().splitlines(False):
            parts = line.split(maxsplit=3)
            if len(parts) != 4:
                raise RuntimeError("Data returned by git doesn't match expected format")

            perms, ref_type, sha, name = parts
            yield perms, ref_type, sha, name.lstrip()

    except subprocess.CalledProcessError as e:
        if e.stdout or e.stderr:  # pyright: ignore[reportAny]
            raise


def readme(repo: str, ref: str = "HEAD") -> bytes:
    for _, ref_type, sha, name in tree(repo, ref):
        if ref_type != "blob":
            continue

        lowername = name.lower()
        if lowername != "readme" and os.path.splitext(lowername)[0] != "readme":
            continue

        data = git(repo, "cat-file", "blob", sha)
        if lowername.endswith(".md"):
            try:
                data = micron.convert_markdown(data)

            except Exception:
                log.error(traceback.format_exc())
                data = micron.escape(data)

        else:
            data = micron.escape(data)

        return data

    return b""


def commits(
    repo: str, ref: str | None = None, count: int = 50, skip: int = 0
) -> Generator[tuple[tuple[str, str], tuple[str, str], datetime, list[str], str]]:
    for line in (
        git(
            repo,
            "log",
            "--pretty=oneline",
            "--format=format:%h\t%H\t%aN\t%aE\t%ai\t%(decorate:tag=tags/,prefix=,suffix=,separator=|)\t%s",
            f"--max-count={count}",
            f"--skip={skip}",
            ref or "HEAD",
        )
        .decode()
        .splitlines(False)
    ):
        parts = line.split("\t", maxsplit=6)
        if len(parts) != 7:
            raise RuntimeError("Data returned doesn't match expected format")

        short_sha, sha, author_name, author_email, date, refs, subject = parts  # pylint: disable=W0621

        yield (
            (short_sha, sha),
            (author_name, author_email),
            datetime.fromisoformat(date),
            refs.split("|"),
            subject,
        )


log: logging.Logger = logging.getLogger(__name__)
app = Application(
    "nomadnetwork",
    ["node"],
    templates={},
)


def header(
    name: str,
    breadcrumbs: list[tuple[str, str] | tuple[str, str, dict[str, str]]] | None = None,
) -> bytes:
    if breadcrumbs is None:
        breadcrumbs = []

    breadcrumb_links = b" > ".join([micron.page_link(*args) for args in breadcrumbs])  # pyright: ignore[reportArgumentType]
    return (
        b"> "
        + micron.page_link("index", "Home")
        + (b" > " if breadcrumbs else b"")
        + breadcrumb_links
        + b" > "
        + name.encode()
        + b"\n> \n"
    )


@app.page("index", ttl=10, permissions=["read"])
def _(_request: Request) -> bytes | None:
    assert app.args is not None
    assert isinstance(app.args.repo, str)  # pyright: ignore[reportAny]
    return b"> Repositories\n" + b"\n".join(
        [
            b">> " + micron.page_link("repo", x, {"repo": x})
            for x in find_repos(app.args.repo)
        ]
    )


@app.page("repo", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
) -> bytes | None:
    return (
        header(repo)
        + b">> "
        + micron.page_link("tree", "tree", {"repo": repo})
        + b" | "
        + micron.page_link("commits", "commits", {"repo": repo})
        + b" | "
        + micron.page_link("branches", "branches", {"repo": repo})
        + b" | "
        + micron.page_link("tags", "tags", {"repo": repo})
        + b"\n>> \n"
        + readme(repo)
    )


@app.page("branches", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
) -> bytes | None:
    return (
        header("branches", [("repo", repo, {"repo": repo})])
        + b">> Branches\n"
        + b"\n".join(
            [
                b">>> "
                + micron.page_link(
                    "branch",
                    branch,
                    {"repo": repo, "branch": branch},
                )
                for branch in branches(repo)
            ]
        )
        + b"\n>>> \n"
    )


@app.page("branch", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    branch: str,
) -> bytes | None:
    return (
        header(
            f"branch: {branch}",
            [
                ("repo", repo, {"repo": repo}),
                ("branches", repo, {"repo": repo}),
            ],
        )
        + b">> "
        + micron.page_link("tree", "tree", {"repo": repo, "ref": branch})
        + b" | "
        + micron.page_link("commits", "commits", {"repo": repo, "branch": branch})
        + b"\n>> \n"
        + readme(repo, branch)
    )


@app.page("tags", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
) -> bytes | None:
    return (
        header("tags", [("repo", repo, {"repo": repo})])
        + b">> tags\n"
        + b"\n".join(
            [
                b">>> " + micron.page_link("tag", tag, {"repo": repo, "tag": tag})
                for tag in tags(repo)
            ]
        )
        + b"\n>>> \n"
    )


@app.page("tag", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    tag: str,
) -> bytes | None:
    return (
        header(
            f"tag: {tag}",
            [
                ("repo", repo, {"repo": repo}),
                ("tags", repo, {"repo": repo}),
            ],
        )
        + b">> "
        + micron.page_link("tree", "tree", {"repo": repo, "ref": tag})
        + b" | "
        + micron.page_link("commits", "commits", {"repo": repo, "tag": tag})
        + b"\n>> \n"
        + readme(repo, tag)
    )


@app.page("tree", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    ref: str | None = None,
    path: str | None = None,
) -> bytes | None:
    links: list[bytes] = []
    effective_ref = ref or "HEAD"
    for perms, ref_type, _, name in tree(repo, f"{effective_ref}:{path or ''}"):
        params = {"repo": repo, "path": os.path.join(path or "", name)}
        if ref is not None:
            params["ref"] = ref

        match ref_type:
            case "blob":
                page = "blob"

            case "tree":
                page = "tree"

            case _:
                raise RuntimeError(f"Unknown tree ref_type: {ref_type}")

        links.append(f"{perms} ".encode() + micron.page_link(page, name, params))

    breadcrumbs: list[tuple[str, str] | tuple[str, str, dict[str, str]]] = [
        ("repo", repo, {"repo": repo}),
    ]
    if path is not None:
        breadcrumbs.append(
            ("tree", "tree", {"repo": repo}),
        )
        name = os.path.basename(path)
        parent = os.path.dirname(path)
        while parent:
            params = {"repo": repo, "path": parent}
            if ref is not None:
                params["ref"] = ref

            breadcrumbs.insert(2, ("tree", os.path.basename(parent), params))
            parent = os.path.dirname(parent)

    else:
        name = "tree"

    return (
        header(name, breadcrumbs)
        + f">> tree: {effective_ref}\n".encode()
        + b"\n".join(links)
        + b"\n>>> \n"
    )


@app.page("commits", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    branch: str | None = None,
    tag: str | None = None,
    page: int = 0,
) -> bytes | None:
    if page < 0:
        raise ValueError("Page cannot be negative")

    if branch is not None and tag is not None:
        raise ValueError("branch and tag cannot be set at the same time")

    breadcrumbs: list[tuple[str, str] | tuple[str, str, dict[str, str]]] = [
        ("repo", repo, {"repo": repo})
    ]
    name = "commits"
    if branch is not None:
        name += ": branch " + branch
        breadcrumbs.append(("branch", branch, {"repo": repo, "branch": branch}))
    elif tag is not None:
        name += ": tag " + tag
        breadcrumbs.append(("tag", tag, {"repo": repo, "tag": tag}))

    params = {"repo": repo}
    if branch:
        params["branch"] = branch

    elif tag:
        params["tag"] = tag

    content: list[bytes] = []
    ref = branch or tag or "HEAD"
    for (short_sha, sha), (author, _), date, _, subject in commits(
        repo,
        ref,
        count=50,
        skip=page * 50,
    ):
        content.append(
            micron.page_link("commit", short_sha, {"sha": sha, **params})
            + b" | "
            + author.encode()
            + b" | "
            + naturalday(date).encode()
            + b" | "
            + subject.encode()
        )

    total = int(git(repo, "rev-list", "--count", ref).decode())
    footer: list[bytes] = []
    if page:
        footer.append(
            micron.page_link(
                "commits",
                "|<",
                {"page": "0", **params},
            )
        )
        footer.append(
            micron.page_link(
                "commits",
                "<",
                {"page": str(page - 1), **params},
            )
        )

    last_page = math.ceil(total / 50)
    footer.append(f"page {page + 1} of {last_page}".encode())
    if (page + 1) * 50 < total:
        footer.append(
            micron.page_link(
                "commits",
                ">",
                {"page": str(page + 1), **params},
            )
        )
        footer.append(
            micron.page_link(
                "commits",
                ">|",
                {"page": str(last_page), **params},
            )
        )

    return header(name, breadcrumbs) + b"\n".join(content) + b"\n`r" + b" ".join(footer)


@app.page("blob", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    path: str,
    ref: str | None = None,
) -> bytes | None:
    parent = os.path.dirname(path)
    breadcrumbs: list[tuple[str, str] | tuple[str, str, dict[str, str]]] = [
        ("repo", repo, {"repo": repo}),
        ("tree", "tree", {"repo": repo}),
    ]
    while parent:
        params = {"repo": repo, "path": parent}
        if ref is not None:
            params["ref"] = ref

        breadcrumbs.insert(2, ("tree", os.path.basename(parent), params))
        parent = os.path.dirname(parent)

    params = {"repo": repo, "path": path}
    if ref is not None:
        params["ref"] = ref

    content = git(repo, "cat-file", "blob", f"{ref or 'HEAD'}:{path}")
    try:
        if path.endswith(".md"):
            try:
                content = micron.convert_markdown(content)

            except Exception:
                log.error(traceback.format_exc())
                content = micron.escape(content)

        else:
            content = micron.escape(content)

    except UnicodeDecodeError:
        content = b"(binary content)"

    return header(f"blob: {os.path.basename(path)}", breadcrumbs) + content


@app.page("commit", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    sha: str,
    branch: str | None = None,
    tag: str | None = None,
) -> bytes | None:
    params = {"repo": repo}
    if branch:
        params["branch"] = branch

    elif tag:
        params["tag"] = tag

    breadcrumbs: list[tuple[str, str] | tuple[str, str, dict[str, str]]] = [
        ("repo", repo, {"repo": repo}),
        ("commits", "commits", params),
    ]
    content: list[bytes] = []
    for line in (
        git(repo, "diff", "--name-status", f"{sha}~1..{sha}").decode().splitlines(False)
    ):
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError("Data returned by git doesn't match expected format")

        _, path = parts
        content.append(
            micron.page_link("diff", line, {"path": path, "sha": sha, **params})
        )

    return (
        header(f"commit: {sha}", breadcrumbs)
        + b">> "
        + micron.page_link("tree", "tree", {"ref": sha, **params})
        + b"\n>> \n"
        + micron.escape(git(repo, "log", "--max-count=1", sha, "--pretty=fuller"))
        + b"\n"
        + b"\n".join(content)
    )


@app.page("diff", ttl=60, permissions=["read"])
def _(  # pylint: disable=E0102 # noqa: F811
    _request: Request,
    repo: str,
    sha: str,
    path: str,
    branch: str | None = None,
    tag: str | None = None,
) -> bytes | None:
    params = {"repo": repo}
    if branch:
        params["branch"] = branch

    elif tag:
        params["tag"] = tag

    breadcrumbs: list[tuple[str, str] | tuple[str, str, dict[str, str]]] = [
        ("repo", repo, {"repo": repo}),
        ("commits", "commits", params),
        ("commit", f"commit: {sha}", {"sha": sha, **params}),
    ]
    content: list[bytes] = []
    for line in (
        git(repo, "diff", "-w", f"{sha}~1..{sha}", "--", path)
        .decode()
        .splitlines(False)
    ):
        color = b""
        if line.startswith("+"):
            color = b"`F0f2"

        elif line.startswith("-"):
            color = b"`Ff00"

        content.append(color + micron.escape(line) + b"`f")

    return header(f"diff: {path}", breadcrumbs) + b"\n".join(content)


def main(argv: Sequence[str] | None = None) -> int:  # noqa: MC0001
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
        help="Path to Reticulum config directory.",
        dest="config",
    )
    _ = parser.add_argument(
        "-i",
        "--identity",
        help="Path identity file.",
        dest="identity",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
        dest="verbose",
    )
    _ = parser.add_argument(
        "-n",
        "--name",
        help="Name to announce.",
        dest="name",
        default=f"rngit {__version__}",
    )
    _ = parser.add_argument(
        "-a",
        "--announce-interval",
        type=int,
        default=None,
        help="Interval in seconds between announces (default: announce once).",
        dest="announce_interval",
    )
    _ = parser.add_argument(
        "-r",
        "--allow-read",
        action="append",
        default=[],
        help="Identities allowed to read the repository.",
        dest="allow_read",
    )
    _ = parser.add_argument(
        "-d",
        "--allow-debug",
        action="append",
        default=[],
        help="Identities allowed to see debug information. Will automatically recieve read permissions as well.",
        dest="allow_debug",
    )
    _ = parser.add_argument(
        "-A",
        "--allow-all-read",
        action="store_true",
        dest="allow_all_read",
        help="Allow any connection to read the repository.",
    )
    args = parser.parse_args(argv)

    assert isinstance(args.repo, str)  # pyright: ignore[reportAny]
    repo_path = os.path.realpath(args.repo)
    if not os.path.exists(repo_path):
        raise FileNotFoundError(repo_path)

    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    verbose = args.verbose

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

    assert isinstance(args.allow_debug, list)  # pyright: ignore[reportAny]
    assert all(isinstance(x, str) for x in args.allow_debug)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    debug_list = set(cast(list[str], args.allow_debug))

    for allow in debug_list:
        if not is_valid_hexhash(allow):
            raise ValueError(f"Invalid debug hexhash: {allow}")

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
    if allow_all_read:
        app.permit(SpecialPermissions.ALL, "read")

    else:
        for hexhash in read_list | debug_list:
            app.permit(hexhash, "read")

    for hexhash in debug_list:
        app.permit(hexhash, "debug")

    app.run(args)
