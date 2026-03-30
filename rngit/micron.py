import re
from typing import cast
from urllib.parse import (
    quote,
    unquote,
)

import marko
import marko.block
import marko.inline


def paramescape(val: str | bytes) -> str:
    return quote(val, safe="")


def paramunescape(val: str | bytes | None) -> str | None:
    if val is None:
        return None

    return unquote(val)


m1 = re.compile("^>", flags=re.MULTILINE)
m2 = re.compile("^-", flags=re.MULTILINE)


def escape(mu: str | bytes) -> bytes:
    if isinstance(mu, bytes):
        mu = mu.decode()

    return m2.sub(
        "\\-",
        m1.sub(
            "\\>",
            mu.replace("\\", "\\\\").replace("`", "\\`"),
        ),
    ).encode()


def link(
    path: str,
    text: str | None = None,
    params: dict[str, str] | None = None,
    address: str | None = None,
) -> bytes:
    path = escape(path).decode()
    text = escape(text or path).decode()
    address = address or ""
    fragment = (
        "`"
        + "|".join(
            [f"{paramescape(key)}={paramescape(val)}" for key, val in params.items()]
        )
        if params
        else ""
    )
    return f"`_`[{text}`{address}:{path}{fragment}]`_".encode()


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


class MicronRenderer(marko.renderer.Renderer):  # pylint: disable=R0904
    def render_blank_line(self, _: marko.block.BlankLine) -> str:
        return "\n"

    def render_code_block(self, element: marko.block.CodeBlock) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        return f"`F222`Bddd{children}``"

    def render_fenced_code(self, element: marko.block.FencedCode) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        lang = escape(element.lang).decode()
        return f"`F222`Bddd\n`*{lang}`*\n{children}\n``"

    def render_html_block(self, element: marko.block.HTMLBlock) -> str:
        return escape(element.body).decode()

    def render_heading(self, element: marko.block.Heading) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        return f"{'>' * element.level} {children}\n"

    def render_link_ref_def(self, _: marko.block.LinkRefDef) -> str:
        return ""

    def render_list(self, element: marko.block.List) -> str:
        result: list[str] = []
        if element.ordered:
            for num, child in enumerate(element.children, element.start):
                children = self.render(child) if not isinstance(child, str) else child
                assert isinstance(children, str)
                result.append(f"{num}. {children}")

        else:
            for child in element.children:
                children = self.render(child) if not isinstance(child, str) else child
                assert isinstance(children, str)
                result.append(f"{escape(element.bullet).decode()} {children}")

        return "".join(result)

    def render_list_item(self, element: marko.block.ListItem) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        return children

    def render_paragraph(self, element: marko.block.Paragraph) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        if children and not children.endswith("\n"):
            children += "\n"

        return children

    def render_quote(self, element: marko.block.Quote) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        return f"\n`B5d5`F222\n\n`*{children}\n``\n"

    def render_setext_heading(self, element: marko.block.SetextHeading) -> str:
        return self.render_heading(cast(marko.block.Heading, element))  # pyright: ignore[reportInvalidCast]

    def render_thematic_break(self, _: marko.block.ThematicBreak) -> str:
        return "-\n"

    def render_auto_link(self, element: marko.inline.AutoLink) -> str:
        dest = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(dest, str)
        dest = escape(dest).decode()
        # TODO parse link to get params and address # pylint: disable=W0511
        return f"`_`[{dest}]`_"

    def render_code_span(self, element: marko.inline.CodeSpan) -> str:
        assert isinstance(element.children, str)
        return f"`F222`Bddd{element.children}``"

    def render_emphasis(self, element: marko.inline.Emphasis) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        return f"`*{children}`*"

    def render_image(self, element: marko.inline.Image) -> str:
        dest = escape(element.dest).decode()
        title = None
        if element.title:
            title = element.title

        elif element.children:
            title = self.render(element.children[0])  # pyright: ignore[reportAny,reportArgumentType]
            assert isinstance(title, str)

        if title is not None:
            title = escape(title).decode()
            return f"`_`[{title}`{dest}]`_"

        return f"`_`[{dest}]`_"

    def render_inline_html(self, element: marko.inline.InlineHTML) -> str:
        assert isinstance(element.children, str)
        return escape(element.children).decode()

    def render_line_break(self, _: marko.inline.LineBreak) -> str:
        return "\n"

    def render_link(self, element: marko.inline.Link) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        children = escape(children).decode()
        # TODO parse link to get params and address # pylint: disable=W0511
        dest = escape(element.dest).decode()
        return f"`_`[{children}`{dest}]`_"

    def render_literal(self, element: marko.inline.Literal) -> str:
        assert isinstance(element.children, str)
        return f"\\{element.children}"

    def render_raw_text(self, element: marko.inline.RawText) -> str:
        assert isinstance(element.children, str)
        return escape(element.children).decode()

    def render_strong_emphasis(self, element: marko.inline.StrongEmphasis) -> str:
        children = self.render_children(element)  # pyright: ignore[reportAny]
        assert isinstance(children, str)
        return f"`!{children}`!"


md = marko.Markdown(renderer=MicronRenderer)


def convert_markdown(markdown: str | bytes) -> bytes:
    if isinstance(markdown, bytes):
        markdown = markdown.decode()

    return md.convert(markdown).encode()
