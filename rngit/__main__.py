import sys


def client():
    from .client import main as _client  # pylint: disable=C0415

    sys.exit(_client())


def server():
    from .server import main as _server  # pylint: disable=C0415

    sys.exit(_server())


def web():
    from .web import main as _web  # pylint: disable=C0415

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
