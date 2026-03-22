from __future__ import annotations

import logging
import sys
import threading

import RNS  # type: ignore[import-untyped]

from . import protocol

__all__ = [
    "Link",
    "create_server_identity",
    "create_server_destination",
    "save_identity",
    "load_identity",
    "configure_logging",
    "get_reticulum",
    "APP_NAME",
]


APP_NAME = protocol.APP_NAME

_reticulum = None
_reticulum_lock = threading.Lock()


def configure_logging(verbose: bool = False, level: int | None = None):
    if level is None:
        level = logging.DEBUG if verbose else logging.INFO

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr
    )


def get_reticulum(config_path: str | None = None):
    global _reticulum
    if _reticulum is None:
        with _reticulum_lock:
            if _reticulum is None:
                _reticulum = RNS.Reticulum(config_path)

    return _reticulum


class Link:
    def __init__(self, link: RNS.Link | None = None):
        self._link: RNS.Link | None = link
        self._log: logging.Logger = logging.getLogger(__name__)
        self._connected: threading.Event = threading.Event()

    def on_link_established(self, link: RNS.Link):
        self._link = link
        self.set_connected()

    def start(self, destination: RNS.Destination) -> None:
        if self._link is not None:
            return

        link = RNS.Link(destination)
        link.set_link_established_callback(self.on_link_established)  # pyright: ignore[reportUnknownMemberType]

    def wait_for_connect(self, timeout: float = 30.0) -> bool:
        return self._connected.wait(timeout)

    def set_connected(self):
        self._connected.set()

    def send(self, data: bytes):
        if self._link is not None:
            self._link.send(data)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

    def receive(self, timeout: float | None = None) -> bytes | None:
        return None if self._link is None else self._link.receive(timeout)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def close(self):
        if self._link is not None:
            self._link.teardown()
            self._link = None


def create_server_identity(config_path: str | None = None) -> RNS.Identity:
    _ = get_reticulum(config_path)
    identity = RNS.Identity()
    return identity


def create_server_destination(
    identity: RNS.Identity,
    destination_hexhash: str | None = None,
    config_path: str | None = None,
) -> RNS.Destination:
    _ = get_reticulum(config_path)

    if destination_hexhash is None:
        hexhash = identity.hexhash
        if hexhash is None:
            raise ValueError("Identity has no hash")

        hash_str = hexhash

    else:
        hash_str = destination_hexhash

    dest_len = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2
    if len(hash_str) != dest_len:
        raise ValueError(f"Destination hash must be {dest_len} hex characters")

    destination = RNS.Destination(
        identity,
        RNS.Destination.IN,
        RNS.Destination.SINGLE,
        APP_NAME,
        hash_str,
    )
    return destination


def save_identity(identity: RNS.Identity, path: str):
    _ = identity.to_file(path)  # pyright: ignore[reportUnknownMemberType]


def load_identity(path: str) -> RNS.Identity:
    identity = RNS.Identity.from_file(path)  # pyright: ignore[reportUnknownMemberType]
    if identity is None:
        raise ValueError(f"Failed to load identity from {path}")

    return identity
