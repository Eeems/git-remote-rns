# pylint: disable=R0801
import json
import logging
import os
import sys
import tempfile
from hashlib import sha256
from subprocess import CalledProcessError

import atheris

with atheris.instrument_imports():
    from rngit.app import (  # pyright: ignore[reportImplicitRelativeImport]
        Application,
        InvalidParameterType,
        Request,
    )
    from rngit.shared import (  # pyright: ignore[reportImplicitRelativeImport]
        configure_logging,
        is_valid_hexhash,
    )

app = Application("fuzz", [])


def fn(_request: Request, _int: int, _float: float, _bool: bool) -> None:
    pass


corpus = os.path.join("corpus", os.path.splitext(os.path.basename(__file__))[0])
seed_path = os.path.join(corpus, "seed", "seed0")
if not os.path.exists(seed_path):
    import struct

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
        if len(data) < 107:
            return

        fdp = atheris.FuzzedDataProvider(data)
        request_id: bytes = fdp.ConsumeBytes(16)
        hexhash: str = request_id.hex()
        if not is_valid_hexhash(hexhash):
            return

        path: str = fdp.ConsumeUnicodeNoSurrogates(50)
        if not path:
            return

        permission = fdp.ConsumeUnicode(20)
        if not permission:
            return

        data: dict[str, int | float | bool] = {
            "_int": fdp.ConsumeInt(1),
            "_float": fdp.ConsumeFloat(),
            "_bool": fdp.ConsumeBool(),
        }
        try:
            parameters = app._get_parameters(fn)  # pyright: ignore[reportPrivateUsage] # pylint: disable=W0212
            request = Request(path, data, hexhash, None, 0.0)
            try:
                params = app._parse_params(request, parameters)  # pyright: ignore[reportPrivateUsage] # pylint: disable=W0212

            except InvalidParameterType as e:
                raise e.exceptions[0] from e  # pylint: disable=E1136

            idx = sha256(
                path.encode() + json.dumps(params, sort_keys=True).encode()
            ).hexdigest()
            _ = app.is_cached(idx)
            app.push_cache(idx, 0.0, None)
            app.purge_cache()
            app.permit(hexhash, permission)
            _ = app.default_handler(path, data, request_id, None, 0)
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
            print(f"data: {data}")
            raise

    argv = [sys.argv[0], corpus, *sys.argv[1:]]
    print("argv: ", end="")
    print(argv)
    _ = atheris.Setup(argv, TestOneInput)
    atheris.Fuzz()
