import sys
import tempfile

import atheris

with atheris.instrument_imports():
    from rngit.client import (  # pyright: ignore[reportImplicitRelativeImport]
        c_style_quote,
    )
    from rngit.micron import (  # pyright: ignore[reportImplicitRelativeImport]
        convert_markdown,
        escape,
        escape_inline,
        file_link,
        link,
        page_link,
        paramescape,
        paramunescape,
    )
    from rngit.shared import (  # pyright: ignore[reportImplicitRelativeImport]
        _normalize_repo,  # pyright: ignore[reportPrivateUsage]
        is_valid_hexhash,
    )

with tempfile.TemporaryDirectory(prefix="rngit_fuzz_") as t:

    def TestOneInput(data: bytes) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return

        if not text:
            return

        _ = c_style_quote(text)
        _ = paramescape(text)
        _ = paramunescape(text)
        _ = escape(text)
        _ = escape_inline(text)
        _ = convert_markdown(text)
        _ = link(text)
        _ = page_link(text)
        _ = file_link(text)
        _ = is_valid_hexhash(text)

        if "\x00" in text or text in (".", "..", ".git"):
            return

        _ = _normalize_repo(text, t)

    _ = atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
