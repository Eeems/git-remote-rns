import logging
import os
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
        configure_logging,
        is_valid_hexhash,
    )

configure_logging("fuzz", logging.FATAL)
corpus = os.path.join("corpus", os.path.splitext(os.path.basename(__file__))[0])
with tempfile.TemporaryDirectory(prefix="rngit_fuzz_") as t:

    def TestOneInput(data: bytes) -> None:
        text = atheris.FuzzedDataProvider(data).ConsumeUnicode(sys.maxsize)
        text_no_surrogates = atheris.FuzzedDataProvider(
            data
        ).ConsumeUnicodeNoSurrogates(sys.maxsize)

        _ = c_style_quote(text)
        _ = paramunescape(text)
        _ = is_valid_hexhash(text)
        _ = link(text_no_surrogates)
        _ = page_link(text_no_surrogates)
        _ = file_link(text_no_surrogates)
        _ = paramescape(text_no_surrogates)
        _ = escape(text_no_surrogates)
        _ = escape_inline(text_no_surrogates)
        _ = convert_markdown(text_no_surrogates)

        if "\x00" in text or text in (".", "..", ".git", ""):
            return

        _ = _normalize_repo(text, t)

    argv = [sys.argv[0], corpus, *sys.argv[1:]]
    print("argv: ", end="")
    print(argv)
    _ = atheris.Setup(argv, TestOneInput)
    atheris.Fuzz()
