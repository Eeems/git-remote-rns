import sys

from .client import main as _client
from .server import main as _server


def client():
    sys.exit(_client())


def server():
    sys.exit(_server())


__all__ = ["client", "server"]

if __name__ == "__main__":
    executable = sys.argv.pop(1)
    match executable:
        case "rngit":
            server()

        case "git-remote-rns":
            client()

        case _:
            raise NotImplementedError(executable)
