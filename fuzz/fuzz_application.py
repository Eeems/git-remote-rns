import json
import logging
import os
import sys
import tempfile
from hashlib import sha256
from subprocess import CalledProcessError

import atheris

with atheris.instrument_imports():  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    from rngit.app import (  # noqa: PLC0415
        Application,
        InvalidParameterType,
        Request,
    )
    from rngit.shared import (  # noqa: PLC0415
        configure_logging,
        is_valid_hexhash,
    )

app = Application("fuzz", [])


def fn(_request: Request, _int: int, _float: float, _bool: bool) -> None:
    pass


corpus = os.path.join("corpus", os.path.splitext(os.path.basename(__file__))[0])
seed_path = os.path.join(corpus, "seed", "seed0")
MINIMUM_DATA_SIZE = 107
if os.path.exists(seed_path) and os.stat(seed_path).st_size < MINIMUM_DATA_SIZE:
    print("seed0 size mismatch", file=sys.stderr)
    os.unlink(seed_path)

if not os.path.exists(seed_path):
    import struct

    os.makedirs(os.path.join(corpus, "seed"), exist_ok=True)
    with open(seed_path, "wb") as f:
        _ = f.write(b"ce20a22807b4c8354180a1e292f98818")
        _ = f.write(b"/a_really_long_path/that/is_about_50_characters123")
        _ = f.write(b"a_long_permission12")
        _ = f.write((1).to_bytes())
        _ = f.write(struct.pack("f", 1.0))
        _ = f.write(True.to_bytes())

configure_logging("fuzz", logging.FATAL)
with tempfile.TemporaryDirectory(prefix="rngit_fuzz_") as t:

    def TestOneInput(data: bytes) -> None:
        if len(data) < MINIMUM_DATA_SIZE:
            return

        fdp = atheris.FuzzedDataProvider(data)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
        request_id = fdp.ConsumeBytes(16)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert isinstance(request_id, bytes)
        hexhash: str = request_id.hex()
        if not is_valid_hexhash(hexhash):
            return

        path = fdp.ConsumeUnicodeNoSurrogates(50)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert isinstance(path, str)
        if not path:
            return

        permission = fdp.ConsumeUnicode(20)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert isinstance(permission, str)
        if not permission:
            return

        data_dict: dict[str, int | float | bool] = {
            "_int": fdp.ConsumeInt(1),  # pyright: ignore[reportUnknownMemberType]
            "_float": fdp.ConsumeFloat(),  # pyright: ignore[reportUnknownMemberType]
            "_bool": fdp.ConsumeBool(),  # pyright: ignore[reportUnknownMemberType]
        }
        try:
            parameters = app._get_parameters(fn)  # pyright: ignore[reportPrivateUsage]
            request = Request(path, data_dict, hexhash, None, 0.0)
            _ = path in request
            _ = request.param(path)  # pyright: ignore[reportAny]

            try:
                params = app._parse_params(request, parameters)  # pyright: ignore[reportPrivateUsage]

            except InvalidParameterType as e:
                raise e.exceptions[0] from e

            idx = sha256(
                path.encode() + json.dumps(params, sort_keys=True).encode()
            ).hexdigest()
            _ = app.is_cached(idx)
            app.push_cache(idx, 0.0, None)
            app.purge_cache()
            app.permit(hexhash, permission)
            _ = app.default_handler(path, data_dict, request_id, None, 0)
            _ = app.exception(request, Exception(path))
            _ = app.exception(
                request,
                CalledProcessError(1, f"{path} {path}", path, path),
            )
            _ = app.exception(request, CalledProcessError(1, [path, path], path, path))

        except Exception:
            print(f"request_id: {request_id}")
            print(f"hexhash: {hexhash}")
            print(f"path: {path.encode()}")
            print(f"permission: {permission.encode()}")
            print(f"data: {data_dict}")
            raise

    argv = [sys.argv[0], corpus, "-timeout=10", *sys.argv[1:]]
    print("argv: ", end="")
    print(argv)
    _ = atheris.Setup(argv, TestOneInput)
    atheris.Fuzz()
