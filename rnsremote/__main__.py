import argparse
import sys

from . import __version__
from .client import main as client  # noqa: F401
from .server import main as server  # noqa: F401


def main():
    parser = argparse.ArgumentParser(prog="python -m rnsremote")
    _ = parser.add_argument("mode", choices=["client", "server"], help="Operation mode")
    _ = parser.add_argument(
        "--version", "-V", action="version", version=f"%(prog)s {__version__}"
    )

    args = parser.parse_args()

    assert isinstance(args.mode, str)  # pyright: ignore[reportAny] # nosec B101
    match args.mode:
        case "client":
            client()

        case "server":
            server()

        case _:
            parser.print_usage()
            sys.exit(1)


if __name__ == "__main__":
    main()
