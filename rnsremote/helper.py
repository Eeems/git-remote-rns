import argparse
import logging
import os
import subprocess  # noqa: B404
import sys
import threading

from . import client, protocol
from .connection import configure_logging


__all__ = ["run"]


GIT_DIR = os.environ.get("GIT_DIR", ".git")


def run():
    parser = argparse.ArgumentParser(prog="git-remote-rns")
    parser.add_argument("remote", help="Remote name (ignored)")
    parser.add_argument("url", help="Remote URL (rns::<hash>[/path])")
    args = parser.parse_args()

    configure_logging(False, level=logging.WARNING)
    log = logging.getLogger(__name__)

    url = args.url

    if not url.startswith("rns::"):
        print("error: Invalid URL format. Expected rns::<hash>[/path]", file=sys.stderr)
        sys.exit(1)

    url_path = url[5:]
    parts = url_path.split("/", 1)
    destination_hash = parts[0]
    repo_path = parts[1] if len(parts) > 1 else ""

    try:
        _run_helper(log, destination_hash, repo_path)
    except Exception:
        log.exception("Error")
        sys.exit(1)


def _run_helper(log, destination_hash: str, repo_path: str = ""):
    log.debug("Connecting to %s...", destination_hash[:8])
    git_link = client.connect(destination_hash, repo_path=repo_path, timeout=30)

    if not git_link.wait_for_connect(timeout=30):
        log.error("Failed to connect to remote")
        sys.exit(1)

    for line in sys.stdin:
        line = line.rstrip()
        args = line.split()

        if line == "":
            print()
            sys.stdout.flush()
            break

        if args[0] == "capabilities":
            print("connect")
            print()

        elif args[0] == "list":
            refs = git_link.request_refs()
            for name, sha in refs.items():
                print(f"{sha} {name}")
            print()

        elif args[0] == "connect":
            service = args[1] if len(args) > 1 else None
            if service not in ("git-upload-pack", "git-receive-pack"):
                print("error: Unsupported service", file=sys.stderr)
                break

            log.debug("Connecting to service: %s", service)
            print()
            sys.stdout.flush()

            pipe_git_service(git_link, service)
            break

        else:
            print(f"error: Unknown command '{args[0]}'", file=sys.stderr)
            sys.exit(1)

        sys.stdout.flush()

    git_link.close()
    log.debug("Connection closed")


def pipe_git_service(git_link, service: str):
    log = logging.getLogger(__name__)
    stdin_lock = threading.Lock()

    try:
        proc = subprocess.Popen(  # noqa: B603,consider-using-with
            [service, os.path.abspath(GIT_DIR)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError:
        log.error("Service not found: %s. Is git installed?", service)
        sys.exit(1)
    except OSError as e:
        log.error("Failed to start %s: %s", service, e)
        sys.exit(1)

    log.debug("Started %s subprocess", service)
    _pipe_service_data(git_link, proc, stdin_lock, service, log)


def _pipe_service_data(git_link, proc, stdin_lock, service: str, log):  # noqa: MC0001
    stdout = proc.stdout

    def forward_to_remote():
        try:
            while stdout is not None:
                data = stdout.read(65536)
                if not data:
                    log.debug("Git stdout closed")
                    break
                git_link.send(protocol.PackPacket(data).serialize())
        except Exception as e:
            log.debug("Error forwarding to remote: %s", e)
        finally:
            try:
                git_link.send(protocol.DonePacket().serialize())
            except Exception:  # noqa: B110
                pass

    def forward_to_git():
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            while True:
                data = git_link.receive()
                if not data:
                    log.debug("Link closed")
                    break
                packet = protocol.parse_packet(data)
                if packet.packet_type == protocol.PACKET_PACK:
                    with stdin_lock:
                        stdin.write(packet.payload)
                elif packet.packet_type == protocol.PACKET_DONE:
                    log.debug("Received DONE from server")
                    with stdin_lock:
                        stdin.close()
                        proc.stdin = None
                    break
        except Exception as e:
            log.debug("Error forwarding to git: %s", e)
        finally:
            with stdin_lock:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except Exception:  # noqa: B110
                        pass
                    proc.stdin = None

    t_remote = threading.Thread(target=forward_to_remote, daemon=True)
    t_git = threading.Thread(target=forward_to_git, daemon=True)

    t_remote.start()
    t_git.start()

    proc.wait()
    log.debug("%s exited with code %d", service, proc.returncode)

    if proc.returncode != 0:
        stderr_data = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        log.warning("%s exited with code %d: %s", service, proc.returncode, stderr_data)

    t_remote.join(timeout=5)
    t_git.join(timeout=5)

    if t_remote.is_alive():
        log.warning("forward_to_remote thread still running after timeout")
    if t_git.is_alive():
        log.warning("forward_to_git thread still running after timeout")
