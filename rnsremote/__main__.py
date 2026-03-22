import argparse

from . import __version__
from .helper import run as client  # noqa: F401
from .server import serve_forever as server  # noqa: F401


def main():
    parser = argparse.ArgumentParser(prog="python -m rnsremote")
    parser.add_argument("mode", choices=["client", "server"], help="Operation mode")
    parser.add_argument("--version", "-V", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    if args.mode == "client":
        client()
    elif args.mode == "server":
        server()


if __name__ == "__main__":
    main()
