from __future__ import annotations

import logging
import sys
import threading
import queue

import RNS  # type: ignore[import-untyped]

from . import protocol

__all__ = [
    "Link",
    "create_server_identity",
    "create_server_destination",
    "save_identity",
    "load_identity",
    "configure_logging",
]


log = logging.getLogger(__name__)


def configure_logging(verbose: bool = False, level: int | None = None):
    if level is None:
        level = logging.DEBUG if verbose else logging.INFO

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr
    )


class Link:
    def __init__(self, link: RNS.Link | None = None):
        self._link: RNS.Link | None = None
        self._log: logging.Logger = logging.getLogger(__name__)
        self._connected: threading.Event = threading.Event()
        self._packet_queue: queue.Queue[bytes | None] = queue.Queue()
        self._link_closed: threading.Event = threading.Event()
        if link is not None:
            self.on_link_established(link)

    def on_link_established(self, link: RNS.Link):
        self._link = link
        self._connected.set()
        link.set_packet_callback(self.on_packet_received)  # pyright: ignore[reportUnknownMemberType]

    def on_link_closed(self, link: RNS.Link):
        if self._link == link:
            self._connected.clear()
            self._link = None
            self._link_closed.set()
            self._packet_queue.put(None)

    def on_packet_received(self, data: bytes, _packet: RNS.Packet) -> None:
        self._packet_queue.put(bytes(data))

    def start(self, destination: RNS.Destination, timeout: float | None = 30.0) -> None:
        if self._link is not None:
            return

        _ = RNS.Link(
            destination,
            established_callback=self.on_link_established,
            closed_callback=self.on_link_closed,
        )
        _ = self.wait_for_connect(timeout)

    def wait_for_connect(self, timeout: float | None = 30.0) -> bool:
        return self._connected.wait(timeout)

    def send(self, data: bytes):
        if self._link is not None:
            packet = RNS.Packet(self._link, data)
            send_result = packet.send()
            _ = send_result

    def receive(self, timeout: float | None = None) -> bytes | None:
        if self._link is None:
            return None
        try:
            if timeout is None:
                data = self._packet_queue.get()
            else:
                data = self._packet_queue.get(timeout=timeout)
            if data is None:
                return None
            return data
        except queue.Empty:
            return None

    def close(self):
        if self._link is not None:
            self._link.teardown()
            self._link = None


def create_server_identity() -> RNS.Identity:
    identity = RNS.Identity()
    return identity


def create_server_destination(
    identity: RNS.Identity,
    destination_hexhash: str | None = None,
) -> RNS.Destination:
    destination = RNS.Destination(
        identity,
        RNS.Destination.IN,
        RNS.Destination.SINGLE,
        protocol.APP_NAME,
    )

    if destination_hexhash is not None:
        expected_hash: str = destination.hexhash  # pyright: ignore[reportAny]
        if expected_hash != destination_hexhash:
            log.warning(
                "Destination hash mismatch: computed %s, requested %s",
                str(expected_hash),
                str(destination_hexhash),
            )

    return destination


def save_identity(identity: RNS.Identity, path: str):
    _ = identity.to_file(path)  # pyright: ignore[reportUnknownMemberType]


def load_identity(path: str) -> RNS.Identity:
    identity = RNS.Identity.from_file(path)  # pyright: ignore[reportUnknownMemberType]
    if identity is None:
        raise ValueError(f"Failed to load identity from {path}")

    return identity
