import logging
import sys
from collections.abc import Callable

log: logging.Logger = logging.getLogger(__name__)


def _exec(fn: Callable[[], int]) -> int:
    res = fn()
    log.debug(f"Exit code: {res}")
    return res


def client() -> int:
    from .client import main as _client  # noqa: PLC0415

    return _exec(_client)


def server() -> int:
    from .server import main as _server  # noqa: PLC0415

    return _exec(_server)


def web() -> int:
    from .web import main as _web  # noqa: PLC0415

    return _exec(_web)


__all__ = ["client", "server", "web"]

if __name__ == "__main__":
    executable = sys.argv.pop(1)
    match executable:
        case "rngit":
            res = server()

        case "git-remote-rns":
            res = client()

        case "rngit-web":
            res = web()

        case _:
            raise NotImplementedError(executable)

    sys.exit(res)
