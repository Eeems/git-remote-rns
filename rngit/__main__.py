import sys

from .client import main as _client
from .server import main as _server
from .web import main as _web


def client():
    sys.exit(_client())


def server():
    sys.exit(_server())


def web():
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
