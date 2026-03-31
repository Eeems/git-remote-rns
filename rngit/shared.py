import errno
import io
import logging
import os
import string
import subprocess
import sys
from enum import Enum
from typing import (
    IO,
    cast,
)

import RNS

from ._compat import override

APP_NAME = "git"
EXPECTED_HEXHASH_LENGTH = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2


class packets(Enum):
    PACKET_IDENTIFIED = 0x01.to_bytes(1, "big")


class ExitCodes(Enum):
    SUCCESS = 0
    EXCEPTION = errno.EFAULT
    UNKNOWN_COMMAND = errno.EBADRQC
    REMOTE_ERROR = errno.EBADMSG
    BAD_ARGUMENT = errno.EINVAL
    NETWORK_ERROR = errno.ECANCELED
    UNICODE_ERROR = errno.EBADE
    CHILD_EXCEPTION = errno.ECHILD


def configure_logging(name: str, level: int = logging.WARNING):
    while logging.root.handlers:
        logging.root.removeHandler(logging.root.handlers[0])

    logging.basicConfig(
        level=level,
        format=f"%(asctime)s {name} [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )


def is_valid_hexhash(hexhash: str) -> bool:
    return len(hexhash) == EXPECTED_HEXHASH_LENGTH and all(
        c in string.hexdigits for c in hexhash
    )


def is_repo(path: str) -> bool:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            cwd=path,
            text=True,
        ).rstrip() in (".", ".git")

    except subprocess.CalledProcessError:
        return False


def _normalize_repo(repo: str, root_dir: str) -> str:
    if os.path.basename(repo) == ".git":
        repo = os.path.dirname(repo)

    return os.path.relpath(repo, root_dir)


def find_repos(root_dir: str) -> list[str]:
    return [
        _normalize_repo(x, root_dir)
        for x in subprocess.check_output(
            [
                "find",
                root_dir,
                "-name",
                "*.git",
                "-type",
                "d",
                "-exec",
                "bash",
                "-c",
                'cd "$0"; realpath $(git rev-parse --git-dir)',
                "{}",
                ";",
            ],
            text=True,
        ).splitlines(False)
    ]


class BytesIOWrapper(io.BufferedWriter):
    """Wrap a buffered bytes stream over TextIOBase string stream."""

    def __init__(
        self,
        buffer: IO[str],
        encoding: str | None = None,
        errors: str | None = None,
        buffer_size: int = 131072,
    ):
        super().__init__(buffer, buffer_size=buffer_size)  # pyright: ignore[reportArgumentType]
        self.encoding: str = encoding or getattr(buffer, "encoding", None) or "utf-8"
        self.errors: str = errors or getattr(buffer, "errors", None) or "strict"

    @override
    def write(self, data: bytes) -> int:
        return cast(IO[str], cast(object, self.raw)).write(data.decode())

    @override
    def flush(self):
        cast(IO[str], cast(object, self.raw)).flush()
