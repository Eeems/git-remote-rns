import sys
from typing import NoReturn


def client() -> NoReturn:
    from .client import main as _client  # noqa: PLC0415

    sys.exit(_client())


def server() -> NoReturn:
    from .server import main as _server  # noqa: PLC0415

    sys.exit(_server())


def web() -> NoReturn:
    from .web import main as _web  # noqa: PLC0415

    sys.exit(_web())


__all__ = ["client", "server", "web"]

if __name__ == "__main__":
    executable = sys.argv.pop(1)
    match executable:
        case "rngit":
            server()

        case "git-remote-rns":
            client()

        case "rngit-web":
            web()

        case _:
            raise NotImplementedError(executable)
