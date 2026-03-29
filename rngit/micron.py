import re
from urllib.parse import (
    quote,
    unquote,
)


def paramescape(val: str | bytes) -> str:
    return quote(val, safe="")


def paramunescape(val: str | bytes | None) -> str | None:
    if val is None:
        return None

    return unquote(val)


m1 = re.compile(r"^>", flags=re.MULTILINE)


def escape(mu: str | bytes) -> bytes:
    if isinstance(mu, bytes):
        mu = mu.decode()

    return m1.sub("\\>", mu.replace("\\", "\\\\")).replace("`", "\\`").encode()


def link(
    path: str,
    text: str | None = None,
    params: dict[str, str] | None = None,
    address: str | None = None,
) -> bytes:
    return "`_`[{text}`{address}:{path}{fragment}]`_".format(
        path=escape(path).decode(),
        text=escape(text or path).decode(),
        address=address or "",
        fragment=(
            "`"
            + "|".join(
                [
                    f"{paramescape(key)}={paramescape(val)}"
                    for key, val in params.items()
                ]
            )
            if params
            else ""
        ),
    ).encode()


def page_link(
    path: str,
    text: str | None = None,
    params: dict[str, str] | None = None,
    address: str | None = None,
) -> bytes:
    return link(f"/page/{path}.mu", text, params, address)


def file_link(
    path: str,
    text: str | None = None,
    params: dict[str, str] | None = None,
    address: str | None = None,
) -> bytes:
    return link(f"/file/{path}", text, params, address)
