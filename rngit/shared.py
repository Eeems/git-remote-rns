import errno
import logging
import os
import string
import subprocess
import sys
from enum import Enum

import RNS

APP_NAME = "git"
EXPECTED_HEXHASH_LENGTH = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2


class packets(Enum):
    PACKET_IDENTIFIED = 0x01.to_bytes(1, "big")


class ExitCodes(Enum):
    SUCCESS = 0
    EXCEPTION = -errno.EFAULT
    UNKOWN_COMMAND = -errno.EBADRQC
    REMOTE_ERROR = -errno.EBADMSG
    BAD_ARGUMENT = -errno.EINVAL
    NETWORK_ERROR = -errno.ECANCELED


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


def find_repos(root_dir: str) -> list[str]:
    return [
        os.path.relpath(x, root_dir)
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
                "cd {}; realpath $(git rev-parse --git-dir)",
                ";",
            ],
            text=True,
        ).splitlines(False)
    ]
