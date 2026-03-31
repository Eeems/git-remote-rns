# pylint: disable=R0801
import io
import logging
import os
import random
import re
import select
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout

import atheris
import RNS

with atheris.instrument_imports():
    from rngit import client  # pyright: ignore[reportImplicitRelativeImport]
    from rngit.shared import (  # pyright: ignore[reportImplicitRelativeImport]
        ExitCodes,
        configure_logging,
    )


RETICULUM_CONFIG = """
[reticulum]
  instance_name = rns_fuzz{randomword}

[interfaces]
  [[AutoInterface]]
    type = AutoInterface
    enabled = no

  [[Dummy]]
    type = BackboneInterface
    enable = yes
    listen_on = 127.0.0.2
"""


def randomword(length: int) -> str:
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for _ in range(length))


rnsd_process: subprocess.Popen[bytes] | None = None
rngit_process: subprocess.Popen[str] | None = None


def start_rnsd(config_dir: str) -> subprocess.Popen[bytes]:
    global rnsd_process
    rns_config = os.path.join(config_dir, "config")
    with open(rns_config, "w") as f:
        _ = f.write(RETICULUM_CONFIG.format(randomword=randomword(5)))

    print("Starting rnsd...")
    rnsd_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "RNS.Utilities.rnsd",
            "--config",
            str(config_dir),
            "-vvv",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    tries = 3
    timeout = 5
    start = time.time()
    remaining = tries
    last_rnstatus_output = ""
    while True:
        if rnsd_proc.returncode is not None:
            stdout = rnsd_proc.stdout.read().decode() if rnsd_proc.stdout else ""
            raise RuntimeError(
                f"rnsd exited early: {rnsd_proc.returncode}\n  stdout: {stdout}"
            )

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "RNS.Utilities.rnstatus",
                "--config",
                config_dir,
                "-a",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        last_rnstatus_output = proc.stdout + proc.stderr
        if not proc.returncode:
            break

        if time.time() - start < timeout:
            continue

        rnsd_proc.terminate()
        try:
            _ = rnsd_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            rnsd_proc.kill()
            _ = rnsd_proc.wait()

        if remaining:
            rnsd_proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "RNS.Utilities.rnsd",
                    "--config",
                    config_dir,
                    "-vvv",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            remaining -= 1
            start = time.time()
            continue

        stdout = rnsd_proc.stdout.read().decode() if rnsd_proc.stdout else ""
        raise RuntimeError(
            f"rnsd failed to start in {tries} tries...\n  stdout: {stdout}\n  rnstatus: {last_rnstatus_output}"
        )

    rnsd_process = rnsd_proc

    def log_output(proc: subprocess.Popen[bytes]):
        while proc.returncode is None:
            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    print(line.decode(), end="")

    threading.Thread(target=log_output, args=(rnsd_proc,)).start()

    return rnsd_proc


def start_rngit_server(config_dir: str, server_repo: str, client_hexhash: str) -> str:
    global rngit_process
    print("Starting rngit server...")

    rngit_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "rngit",
            "rngit",
            server_repo,
            "--verbose",
            "--config",
            config_dir,
            "--announce-interval",
            "1",
            "--allow-write",
            client_hexhash,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "RNS_CONFIG_PATH": config_dir},
    )

    dest_hash = None
    assert rngit_proc.stdout is not None

    while True:
        ready, _, _ = select.select([rngit_proc.stdout], [], [], 5)
        if ready:
            line = rngit_proc.stdout.readline()
            if not line:
                break

            print(f"SERVER: {line.rstrip()}")
            match = re.search(r"\[INFO\] Destination: <([a-f0-9]+)>", line)
            if match:
                dest_hash = match.group(1)
                break

            if "error" in line.lower():
                raise RuntimeError(f"Server error: {line}")
        else:
            break

    if rngit_proc.poll() is not None and dest_hash is None:
        raise RuntimeError(f"rngit exited early with code {rngit_proc.returncode}")

    assert dest_hash is not None, "Could not get destination hash from server"
    assert len(dest_hash) == 32, f"Invalid destination hash length: {dest_hash}"

    while subprocess.run(
        [
            sys.executable,
            "-m",
            "RNS.Utilities.rnpath",
            "--config",
            config_dir,
            "-w1",
            dest_hash,
        ],
        check=False,
    ).returncode:
        if rngit_proc.returncode is not None:
            raise RuntimeError(
                f"Server exited early: {rngit_proc.returncode}\n{rngit_proc.stdout.read() if rngit_proc.stdout else ''}"
            )

    rngit_process = rngit_proc

    def log_output(proc: subprocess.Popen[str]):
        while proc.returncode is None:
            for f in (proc.stdout, proc.stderr):
                if f is not None:
                    line = f.readline()
                    if line:
                        print(line, end="")

    threading.Thread(target=log_output, args=(rngit_proc,)).start()

    return dest_hash


def cleanup():
    global rnsd_process, rngit_process
    print("Cleaning up...")

    if rngit_process is not None:
        rngit_process.terminate()
        try:
            _ = rngit_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rngit_process.kill()
            _ = rngit_process.wait()
        rngit_process = None

    if rnsd_process is not None:
        rnsd_process.terminate()
        try:
            _ = rnsd_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rnsd_process.kill()
            _ = rnsd_process.wait()
        rnsd_process = None


configure_logging("fuzz", logging.FATAL)

allowed_exit_codes = [x.value for x in ExitCodes if x is not ExitCodes.EXCEPTION]

with tempfile.TemporaryDirectory() as temp_dir:
    config_dir = os.path.join(temp_dir, "config")
    os.makedirs(config_dir, exist_ok=True)

    server_repo = os.path.join(temp_dir, "server_repo")
    os.makedirs(server_repo, exist_ok=True)

    client_repo = os.path.join(temp_dir, "client_repo")
    os.makedirs(client_repo, exist_ok=True)
    client._repo_path = client_repo  # pyright: ignore[reportPrivateUsage]

    identity_path = os.path.join(config_dir, "identity")
    identity = RNS.Identity(True)
    _ = identity.to_file(identity_path)  # pyright: ignore[reportUnknownMemberType]
    client_identity = identity.hexhash
    assert client_identity is not None
    client._identity = identity  # pyright: ignore[reportPrivateUsage]

    print(f"Client identity: {client_identity}")

    corpus = os.path.join("corpus", os.path.splitext(os.path.basename(__file__))[0])
    seed_dir = os.path.join(corpus, "seed")
    os.makedirs(seed_dir, exist_ok=True)
    for seed, seed_name in [
        (b"\x00" + b"A" * 89, "capabilities"),
        (b"\x01" + b"A" * 89, "list"),
        (b"\x02" + b"A" * 89, "list-for-push"),
        (b"\x03" + b"B" * 20 + b"C" * 30 + b"A" * 39, "fetch"),
        (b"\x03" + b"\x00" * 20 + b"refs/heads/main", "fetch-min"),
        (b"\x04" + b"D" * 30 + b"E" * 30 + b"A" * 29, "push"),
        (b"\x05" + b"F" * 30 + b"G" * 30 + b"A" * 29, "push-force"),
        (b"\x06" + b"H" * 30 + b"I" * 30 + b"A" * 29, "push-delete"),
    ]:
        seed_path = os.path.join(seed_dir, seed_name)
        if not os.path.exists(seed_path):
            with open(seed_path, "wb") as f:
                _ = f.write(seed)

    try:
        subprocess.run(
            ["git", "config", "--global", "init.defaultBranch", "master"],
            check=True,
        )
        subprocess.run(
            ["git", "config", "--global", "user.name", "rngit"],
            check=True,
        )
        subprocess.run(
            ["git", "config", "--global", "user.email", "root@localhost"],
            check=True,
        )

        _ = subprocess.check_call(["git", "init"], cwd=server_repo)
        _ = subprocess.check_call(["git", "init"], cwd=client_repo)
        _ = subprocess.check_call(["touch", "a"], cwd=server_repo)
        _ = subprocess.check_call(["git", "add", "a"], cwd=server_repo)
        _ = subprocess.check_call(["git", "commit", "-m", "a"], cwd=server_repo)

        _ = start_rnsd(config_dir)
        server_hash = start_rngit_server(config_dir, server_repo, client_identity)
        print(f"Server hash: {server_hash}")

        _ = RNS.Reticulum(config_dir, RNS.LOG_WARNING)
        print("Reticulum started, beginning test")

        destination = bytes.fromhex(server_hash)

        class FuzzedDataProvider(atheris.FuzzedDataProvider):
            def ConsumeUnicodeNoSurrogates(self, len: int) -> str:
                return self.ConsumeUnicodeNoSurrogates(len).replace("\0", "?")

        def TestOneInput(data: bytes) -> None:
            if len(data) < 90:
                return

            fdp = atheris.FuzzedDataProvider(data)

            cmd_type = fdp.ConsumeIntInRange(0, 6)
            command: bytes = [
                b"capabilities",
                b"list",
                b"list for-push",
                b"fetch",
                b"push",
                b"push +",
                b"push :",
            ][cmd_type]
            stdin_data: str
            if cmd_type == 3:
                sha: bytes = fdp.ConsumeBytes(20).hex().encode()
                ref: bytes = fdp.ConsumeUnicodeNoSurrogates(30).encode()
                stdin_data: bytes = (
                    command + b" " + sha + b" refs/heads/" + ref + b"\n\n"
                )

            elif cmd_type >= 4:
                ref1: bytes = fdp.ConsumeUnicodeNoSurrogates(30).encode()
                ref2: bytes = fdp.ConsumeUnicodeNoSurrogates(30).encode()
                stdin_data: bytes = (
                    command + b" refs/heads/" + ref1 + b":refs/heads/" + ref2 + b"\n\n"
                )

            else:
                stdin_data: bytes = command + b"\n\n"

            with io.StringIO() as output:
                try:
                    with (
                        io.StringIO(stdin_data.decode()) as stdin_io,
                        redirect_stdout(output),
                        redirect_stderr(output),
                    ):
                        client.stdin_loop(destination, stdin_io)

                except client.ClientException as e:
                    if e.exitcode.value in allowed_exit_codes:
                        return

                    raise

                except subprocess.CalledProcessError:
                    pass

                except ValueError as e:
                    if str(e) != "embedded null byte":
                        raise

                except Exception:
                    print(output.getvalue())
                    print(f"stdin_data: {stdin_data}")
                    raise

        argv = [sys.argv[0], corpus, "-timeout=30", *sys.argv[1:]]
        print("argv: ", end="")
        print(argv)
        _ = atheris.Setup(argv, TestOneInput)
        atheris.Fuzz()

    except Exception:
        print("rnsd output <<EOF")
        if rnsd_process is not None and rnsd_process.stdout is not None:
            print(rnsd_process.stdout.read().decode(errors="replace"))

        print("EOF")

        print("rngit output <<EOF")
        if rngit_process is not None and rngit_process.stdout is not None:
            print(rngit_process.stdout.read())

        print("EOF")

        raise

    finally:
        cleanup()
