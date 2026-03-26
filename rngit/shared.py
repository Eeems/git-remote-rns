import logging
import string
import sys
from enum import Enum

import RNS

APP_NAME = "git"
EXPECTED_HEXHASH_LENGTH = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2


class packets(Enum):
    PACKET_IDENTIFIED = 0x01.to_bytes(1, "big")


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
