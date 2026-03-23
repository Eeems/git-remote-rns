import logging
import string
import sys

import RNS

APP_NAME = "git"
EXPECTED_HEXHASH_LENGTH = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2


def configure_logging(level: int = logging.WARNING):
    while logging.root.handlers:
        logging.root.removeHandler(logging.root.handlers[0])

    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr
    )


def is_valid_hexhash(hexhash: str) -> bool:
    return len(hexhash) == EXPECTED_HEXHASH_LENGTH and all(
        c in string.hexdigits for c in hexhash
    )
