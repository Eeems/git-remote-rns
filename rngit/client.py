# pylint: disable=R0801
import argparse
import io
import logging
import os
import selectors
import subprocess
import sys
import threading
from collections.abc import Sequence
from tempfile import TemporaryDirectory
from typing import (
    IO,
    Callable,
    cast,
)

import RNS

from . import __version__
from .shared import (
    APP_NAME,
    BytesIOWrapper,
    ExitCodes,
    configure_logging,
    is_valid_hexhash,
    packets,
)

__all__ = [
    "main",
]


log: logging.Logger = logging.getLogger(__name__)

_linkEvent: threading.Event = threading.Event()
_identity: RNS.Identity | None = None
_repo_path: str | None = None


def git(
    *args: str,
    stdout: IO[bytes] | int | None = None,
    stderr: IO[bytes] | int | None = None,
) -> None:
    cmd = ["git", *args]
    process = subprocess.Popen(  # nosec B607 B603 # pylint: disable=R1732
        cmd,
        stdout=subprocess.PIPE if isinstance(stdout, io.IOBase) else stdout,
        stderr=subprocess.PIPE if isinstance(stderr, io.IOBase) else stderr,
        text=False,
    )

    with selectors.DefaultSelector() as selector:

        def wrap(
            stream: IO[bytes] | None,
            output: IO[bytes] | int | None,
        ):
            if stream is None or not isinstance(output, io.IOBase):
                return

            def fn(stream: IO[bytes]) -> None:
                _ = output.write(stream.readline())
                output.flush()

            _ = selector.register(stream, selectors.EVENT_READ, fn)

        def flush(stream: IO[bytes] | None, output: IO[bytes] | int | None):
            if stream is None or not isinstance(output, io.IOBase):
                return

            while True:
                line = stream.read()
                if not line:
                    break

                _ = output.write(line)
                output.flush()

        wrap(process.stdout, stdout)
        wrap(process.stderr, stderr)
        if process.stdout is not None or process.stderr is not None:
            while process.poll() is None:
                events = selector.select(timeout=1)
                for key, _ in events:
                    fn = cast(Callable[[IO[bytes]], None], key.data)
                    stream = cast(IO[bytes], key.fileobj)
                    fn(stream)

                if process.poll() is not None:
                    break

    returncode = process.wait()
    flush(process.stdout, stdout)
    flush(process.stderr, stderr)
    if returncode:
        raise subprocess.CalledProcessError(returncode, cmd)


def log_and_stdout(stdout: IO[bytes], msg: str):
    log.debug(msg)
    _ = stdout.write(msg.encode())


def on_link_established(link: RNS.Link):
    global _identity  # pylint: disable=W0602 # noqa: F999
    assert _identity is not None
    log.debug("ESTABLISHED: %s", link)
    link.set_packet_callback(on_packet)  # pyright: ignore[reportUnknownMemberType]
    _ = link.identify(_identity)  # pyright: ignore[reportUnknownMemberType]


def on_link_closed(link: RNS.Link):
    global _linkEvent  # pylint: disable=W0602 # noqa: F999
    log.debug("CLOSED: %s", link)
    _linkEvent.clear()


def on_packet(message: bytes, _packet: RNS.Packet):
    global _linkEvent  # pylint: disable=W0602 # noqa: F999
    log.debug("PACKET: %s", message)
    match message:
        case packets.PACKET_IDENTIFIED.value:
            _linkEvent.set()

        case _:
            log.error("Invalid packet: %d", message)


def request(
    link: RNS.Link, path: str, data: bytes = b""
) -> tuple[str | None, bytes | None]:
    global _repo_path  # pylint: disable=W0602 # noqa: F999
    assert _repo_path is not None
    event = threading.Event()
    log.debug("REQUEST %s", path)
    receipt = link.request(  # pyright: ignore[reportUnknownMemberType]
        path,
        _repo_path.encode() + b"\n" + data,
        response_callback=lambda _, e=event: e.set(),  # pyright: ignore[reportUnknownLambdaType]
        failed_callback=lambda _, e=event: e.set(),  # pyright: ignore[reportUnknownLambdaType]
    )
    if not receipt:
        return "Failed to send request", None

    _ = event.wait()
    match receipt.get_status():
        case RNS.RequestReceipt.FAILED:
            return "Failed to send request", None

        case RNS.RequestReceipt.READY:
            data = receipt.get_response()  # pyright: ignore[reportUnknownVariableType, reportAssignmentType]
            assert isinstance(data, bytes)
            returncode = int.from_bytes(data[0:1], "big")
            if returncode:
                return "Remote error: " + data[1:].decode(), None

            return None, data[1:]

        case _:
            return f"Invalid status: {receipt.get_status()}", None


def c_style_quote(value: bytes | str) -> str:
    if isinstance(value, bytes):
        value = value.decode()

    escaped = '"'
    for char in value:
        match char:
            case "\\":
                escaped += "\\\\"

            case '"':
                escaped += '\\"'

            case "\n":
                escaped += "\\n"

            case "\t":
                escaped += "\\t"

            case "\r":
                escaped += "\\r"

            case "\b":
                escaped += "\\b"

            case "\f":
                escaped += "\\f"

            case "\a":
                escaped += "\\a"

            case "\v":
                escaped += "\\v"

            case _:
                if ord(char) < 32 or ord(char) > 126:  # non-printable
                    escaped += f"\\x{ord(char):02x}"

                else:
                    escaped += char

    return escaped + '"'


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="git-remote-rns")
    _ = parser.add_argument("remote", help="Remote name (ignored)")
    _ = parser.add_argument("url", help="Remote URL (<hash>[/path])")
    _ = parser.add_argument(
        "--version",
        action="version",
        version=f"git-remote-rns {__version__}",
    )
    _ = parser.add_argument(
        "-i",
        "--identity",
        help="Path to identity file",
        dest="identity",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    args = parser.parse_args(argv)

    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    identity_path = args.identity

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    verbose = args.verbose or bool(os.environ.get("VERBOSE", 0))
    configure_logging("git-remote-rns", logging.DEBUG if verbose else logging.WARNING)

    assert isinstance(args.url, str)  # pyright: ignore[reportAny]
    url = args.url
    parts = url.split("/", 1)
    destination_hexhash = parts[0]
    if not is_valid_hexhash(destination_hexhash):
        log.error("error: Invalid URL. Hexhash invalid: %s", destination_hexhash)
        return ExitCodes.BAD_ARGUMENT.value

    destination = bytes.fromhex(destination_hexhash)

    global _repo_path
    _repo_path = parts[1] if len(parts) > 1 else "."

    config_path = os.environ.get("RNS_CONFIG_PATH", None)
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType]
    if identity_path is None:
        identity_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    assert identity_path is not None
    log.info("Identity: %s", identity_path)
    log.info("Destination: %s", destination_hexhash)
    identity: RNS.Identity | None = None
    if os.path.exists(identity_path):
        identity = RNS.Identity.from_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    if identity is None:
        identity = RNS.Identity(True)
        _ = identity.to_file(identity_path)  # pyright: ignore[reportUnknownMemberType]

    global _identity
    _identity = identity

    stdout = BytesIOWrapper(sys.stdout)
    stderr = BytesIOWrapper(sys.stderr)
    try:
        stdin_loop(destination, sys.stdin, stdout, stderr)

    except ClientException as e:
        log.exception(e.message)
        return e.exitcode.value

    except (UnicodeDecodeError, UnicodeEncodeError):
        log.exception("Unicode error")
        return ExitCodes.UNICODE_ERROR.value

    except subprocess.CalledProcessError:
        log.exception("Child process error")
        return ExitCodes.CHILD_EXCEPTION.value

    except Exception:
        log.exception("Unexpected error")
        return ExitCodes.EXCEPTION.value

    finally:
        _ = stdout.detach()
        _ = stderr.detach()

    return ExitCodes.SUCCESS.value


class ClientException(Exception):
    def __init__(self, exitcode: ExitCodes, message: str) -> None:
        super().__init__(message)
        self.exitcode: ExitCodes = exitcode
        self.message: str = message


def stdin_loop(
    destination: bytes, stdin: IO[str], stdout: IO[bytes], stderr: IO[bytes]
) -> None:  # noqa: MC0001
    if not RNS.Transport.has_path(destination):  # pyright: ignore[reportUnknownMemberType]
        RNS.Transport.request_path(destination)  # pyright: ignore[reportUnknownMemberType]
        if not RNS.Transport.await_path(destination, 30):  # pyright: ignore[reportUnknownMemberType]
            raise ClientException(ExitCodes.NETWORK_ERROR, "Timed out waiting for path")

    server_identity = RNS.Identity.recall(destination)  # pyright: ignore[reportUnknownMemberType]
    if server_identity is None:
        raise ClientException(ExitCodes.NETWORK_ERROR, "Failed to get server identity")

    server_destination = RNS.Destination(
        server_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        APP_NAME,
    )
    link = RNS.Link(server_destination, on_link_established, on_link_closed)
    push_queue: list[tuple[str, str]] = []
    fetch_queue: list[tuple[str, str]] = []
    global _linkEvent  # pylint: disable=W0602 # noqa: F999
    try:  # pylint: disable=too-many-nested-blocks
        for line in stdin:
            _ = _linkEvent.wait()
            if not line:
                continue

            log.debug("STDIN %s", line.encode(errors="replace"))

            parts = line.split(maxsplit=1)
            assert isinstance(parts, list)
            if not parts:
                log.debug("\\n")
                if not push_queue and not fetch_queue:
                    log.debug("\\n but no queue was built, skipping status reporting")
                    continue

                while push_queue:
                    local_ref, remote_ref = push_queue.pop(0)
                    if local_ref.startswith("+"):
                        local_ref = local_ref[1:]

                    if not local_ref:
                        err, data = request(
                            link,
                            "delete",
                            remote_ref.encode(),
                        )
                        if err is not None:
                            _ = stderr.write(err.encode())
                            _ = stderr.write(b"\n")
                            log_and_stdout(
                                stdout,
                                f"error {remote_ref} {c_style_quote(err)}\n",
                            )

                        else:
                            assert not data
                            log_and_stdout(stdout, f"ok {remote_ref}\n")

                        if data:
                            _ = stderr.write(data)
                            _ = stderr.write(b"\n")

                    else:
                        with TemporaryDirectory() as tmpdir:
                            bundle = os.path.join(tmpdir, "bundle")
                            git(
                                "bundle",
                                "create",
                                "--progress",
                                bundle,
                                local_ref,
                                stdout=stdout,
                                stderr=stderr,
                            )
                            with open(bundle, "rb") as f:
                                data = f.read()

                            err, data = request(
                                link,
                                "push",
                                f"{local_ref}:{remote_ref}\n".encode() + data,
                            )
                            if err is not None:
                                log_and_stdout(
                                    stdout,
                                    f"error {remote_ref} {c_style_quote(err)}\n",
                                )

                            else:
                                assert not data, f"Unexpected data: {data}"
                                log_and_stdout(stdout, f"ok {remote_ref}\n")

                while fetch_queue:
                    sha, ref = fetch_queue.pop(0)
                    err, data = request(link, "fetch", f"{sha} {ref}".encode())
                    if err is not None:
                        _ = stderr.write(err.encode())
                        _ = stderr.write(b"\n")
                        raise ClientException(ExitCodes.REMOTE_ERROR, "Remote error")

                    assert data is not None
                    with TemporaryDirectory() as tmpdir:
                        bundle = os.path.join(tmpdir, f"{sha}.bundle")
                        with open(bundle, "wb") as f:
                            _ = f.write(data)

                        git(
                            "bundle",
                            "verify",
                            "--quiet",
                            bundle,
                            stderr=subprocess.DEVNULL,
                            stdout=stdout,
                        )
                        git(
                            "bundle",
                            "unbundle",
                            "--progress",
                            bundle,
                            ref,
                            stdout=subprocess.DEVNULL,
                            stderr=stderr,
                        )

                _ = stderr.flush()
                log.debug("Finished batch processing")
                _ = stdout.write(b"\n")
                try:
                    _ = stdout.flush()

                except BrokenPipeError:
                    log.error(
                        "Parent process closed stdout early, this should not have happened"
                    )
                    break

                continue

            match parts[0]:
                case "capabilities":
                    log.debug("CAPABILITIES")
                    _ = stdout.write(b"list\n")
                    _ = stdout.write(b"fetch\n")
                    _ = stdout.write(b"push\n")
                    _ = stdout.write(b"\n")
                    _ = stdout.flush()

                case "fetch":
                    push_queue.clear()
                    sha, ref = parts[1].rstrip().split(" ", maxsplit=1)
                    log.debug("FETCH %s %s", sha, ref)
                    fetch_queue.append((sha, ref))

                case "push":
                    fetch_queue.clear()
                    local_ref, remote_ref = parts[1].rstrip().split(":", maxsplit=1)
                    log.debug("PUSH %s %s", local_ref, remote_ref)
                    push_queue.append((local_ref, remote_ref))

                case "list":
                    log.debug("LIST")
                    path = "list"
                    if len(parts) > 1 and "for-push" in parts[1]:
                        path = "list-for-push"

                    err, data = request(link, path)
                    if err is not None:
                        _ = stderr.write(err.encode())
                        _ = stderr.write(b"\n")
                        raise ClientException(ExitCodes.REMOTE_ERROR, "Remote error")

                    assert data is not None
                    _ = stdout.write(data)
                    _ = stdout.write(b"\n")
                    _ = stdout.flush()

                case _:
                    _ = stderr.write(f"Unknown command: {parts[0]}\n".encode())
                    raise ClientException(
                        ExitCodes.UNKNOWN_COMMAND, f"Unknown command: {parts[0]}"
                    )

        log.debug("End of stdin")

    finally:
        log.debug("Closing link")
        link.teardown()
