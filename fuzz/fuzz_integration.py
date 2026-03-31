# pylint: disable=R0801
import os
import re
import subprocess
import sys
import tempfile

import atheris
from RNS import Identity

from rngit.shared import ExitCodes

client_container_id: str | None = None
server_container_id: str | None = None
try:
    with tempfile.TemporaryDirectory() as temp_dir:
        identity_path = os.path.join(temp_dir, "identity")
        identity = Identity(True)
        _ = identity.to_file(identity_path)
        client_identity = identity.hexhash

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

        print("Setting up integration fuzzer...")
        _ = subprocess.run(
            ["docker", "network", "create", "rngit-fuzz-net"],
            capture_output=True,
            check=False,
        )

        print("Starting server container...")
        server_run_result = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--network",
                "rngit-fuzz-net",
                "--hostname=rnsd",
                "--env=IS_HOST=1",
                "--env=ANNOUNCE_INTERVAL=1",
                f"--env=ALLOW_WRITE={client_identity}",
                f"--volume={os.path.dirname(os.path.dirname(__file__))}:/src",
                "eeems/reticulum:rngit",
                "ash",
                "-c",
                """
set -e
pip install --root-user-action=ignore --editable /src
mkdir -p /data
cd /data
git config --global init.defaultBranch master
git config --global user.name rngit
git config --global user.email root@localhost
git init
touch a
git add a
git commit -m a
cd
exec /entrypoint
                """,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        server_container_id = server_run_result.stdout.strip()

        print("Waiting for server to start (isup)...")
        while True:
            isup_result = subprocess.run(
                ["docker", "exec", server_container_id, "isup"],
                capture_output=True,
                check=False,
                text=True,
            )
            if isup_result.returncode == 0:
                break

            if isup_result.stderr.startswith("Error response from daemon:"):
                raise RuntimeError(f"Server failed to start:\n{isup_result.stderr}")

        print("Waiting for server hash...")
        while True:
            logs_result = subprocess.run(
                ["docker", "logs", server_container_id],
                capture_output=True,
                text=True,
                check=False,
            )
            match = re.search(
                r"Destination: <([a-f0-9]{32})>",
                logs_result.stdout + logs_result.stderr,
            )
            if match:
                break

            if logs_result.stderr.startswith("Error response from daemon:"):
                raise RuntimeError(f"Server failed to start:\n{logs_result.stderr}")

        hash_from_server = match.group(1)
        print(f"Server hash: {hash_from_server}")

        print("Starting client container...")
        source_dir = os.path.dirname(os.path.dirname(__file__))
        result = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--network",
                "rngit-fuzz-net",
                f"--volume={source_dir}:/src",
                f"--volume={temp_dir}:/config",
                "eeems/reticulum:rngit",
                "ash",
                "-c",
                """
set -e
pip install --root-user-action=ignore --editable /src
mkdir -p /data
cd /data
git config --global init.defaultBranch master
git config --global user.name rngit
git config --global user.email root@localhost
git init
cd
exec -a rnsd /usr/local/bin/rnsd -vvv
                """,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        client_container_id = result.stdout.strip()

        print("Waiting for RNS to establish connections...")
        while True:
            isup_result = subprocess.run(
                ["docker", "exec", client_container_id, "isup"],
                capture_output=True,
                check=False,
                text=True,
            )
            if isup_result.returncode == 0:
                break

            if isup_result.stderr.startswith("Error response from daemon:"):
                raise RuntimeError(f"Client failed to start:\n{isup_result.stderr}")

        allowed_exit_codes = [
            x.value for x in ExitCodes if x is not ExitCodes.EXCEPTION
        ]

        def TestOneInput(data: bytes) -> None:
            assert client_container_id is not None
            assert server_container_id is not None
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
            stdin_data: bytes
            if cmd_type == 3:
                sha = fdp.ConsumeBytes(20).hex().encode()
                ref = fdp.ConsumeBytes(30)
                stdin_data = command + b" " + sha + b" refs/heads/" + ref + b"\n\n"

            elif cmd_type >= 4:
                ref1 = fdp.ConsumeBytes(30)
                ref2 = fdp.ConsumeBytes(30)
                stdin_data = (
                    command + b" refs/heads/" + ref1 + b":refs/heads/" + ref2 + b"\n\n"
                )

            else:
                stdin_data = command + b"\n\n"

            cmd = [
                "docker",
                "exec",
                "--interactive",
                "--workdir=/data",
                "--environment=RNS_CONFIG_PATH=/config",
                client_container_id,
                "git-remote-rns",
                "--verbose",
                "origin",
                hash_from_server,
            ]
            try:
                with subprocess.Popen(
                    cmd,
                    env={"VERBOSE": "1", **os.environ},
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ) as proc:
                    stdout, stderr = proc.communicate(stdin_data)
                    if proc.returncode in allowed_exit_codes:
                        return

                    print(stdout.decode())
                    print(stderr.decode())
                    print(proc.returncode)
                    raise subprocess.CalledProcessError(
                        proc.returncode, cmd, stdout, stderr
                    )

            except Exception:
                print("Client <<EOF")
                _ = subprocess.call(["docker", "logs", client_container_id])
                print("EOF")
                print("Server  <<EOF")
                _ = subprocess.call(["docker", "logs", server_container_id])
                print("EOF")
                print(f"stdin_data: {stdin_data}")
                raise

        argv = [sys.argv[0], corpus, "-timeout=30", *sys.argv[1:]]
        print("argv: ", end="")
        print(argv)
        _ = atheris.Setup(argv, TestOneInput)
        atheris.Fuzz()

except Exception:
    if client_container_id is not None:
        print("Client <<EOF")
        _ = subprocess.call(["docker", "logs", client_container_id])
        print("EOF")

    if server_container_id is not None:
        print("Server  <<EOF")
        _ = subprocess.call(["docker", "logs", server_container_id])
        print("EOF")

    raise

finally:
    print("Cleaning up...")
    for x in [client_container_id, server_container_id]:
        if x is None:
            continue

        _ = subprocess.run(
            ["docker", "kill", x],
            capture_output=True,
            check=False,
        )
        _ = subprocess.run(
            ["docker", "rm", x],
            capture_output=True,
            check=False,
        )

    _ = subprocess.run(
        ["docker", "network", "rm", "rngit-fuzz-net"],
        capture_output=True,
        check=False,
    )
