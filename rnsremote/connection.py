from __future__ import annotations

import logging
import sys
import threading
import time

import RNS  # type: ignore[import-untyped]

from . import protocol

__all__ = [
    "Link",
    "ClientLink",
    "connect",
    "create_server_identity",
    "create_server_destination",
    "save_identity",
    "load_identity",
    "configure_logging",
    "get_reticulum",
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

    def _set_link(self, link: RNS.Link) -> None:
        self._link = link

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


class ClientLink(Link):
    def __init__(
        self, link: RNS.Link | None, destination_hexhash: str, repo_path: str = ""
    ):
        super().__init__(link)
        self.destination_hexhash: str = destination_hexhash
        self.repo_path: str = repo_path

    def request_refs(self, timeout: float = 30.0) -> dict[str, str]:
        self._log.debug(
            "Requesting refs from server (repo: %s)", self.repo_path or "default"
        )

        self.send(
            protocol.HandshakePacket(
                protocol.PROTOCOL_VERSION, self.repo_path
            ).serialize()
        )

        refs: dict[str, str] = {}
        while True:
            data = self.receive(timeout)
            if not data:
                self._log.warning("No data received")
                break

            packet = protocol.parse_packet(data)
            match packet.packet_type:
                case protocol.PACKET_HANDSHAKE:
                    self._log.debug("Received handshake")

                case protocol.PACKET_REF_LIST:
                    if isinstance(packet, protocol.RefListPacket):
                        refs.update(packet.refs)
                        self._log.debug("Received %d refs", len(refs))

                case protocol.PACKET_DONE:
                    self._log.debug("Ref negotiation complete")
                    break

                case protocol.PACKET_ERROR:
                    error_msg = packet.payload.decode("utf-8", errors="replace")
                    self._log.error("Server error: %s", error_msg)
                    break

                case _:
                    continue

        return refs


def connect(
    destination_hexhash: str,
    config_path: str | None = None,
    repo_path: str = "",
    timeout: float = 60.0,
):
    log = logging.getLogger(__name__)

    _ = get_reticulum(config_path)

    dest_len = (RNS.Reticulum.TRUNCATED_HASHLENGTH // 8) * 2
    if len(destination_hexhash) != dest_len:
        raise ValueError(
            f"Invalid destination hash: expected {dest_len} hex characters, got {len(destination_hexhash)}. "
            + "Ensure you are using the correct RNS destination hash."
        )

    try:
        destination_hash = bytes.fromhex(destination_hexhash)

    except ValueError as e:
        raise ValueError(
            f"Invalid destination hash format: {destination_hexhash[:8]}... - {e}"
        ) from e

    log.debug("Looking for path to %s...", destination_hexhash[:8])

    if not RNS.Transport.has_path(destination_hash):  # pyright: ignore[reportUnknownMemberType]
        log.debug("Path not known, requesting...")
        RNS.Transport.request_path(destination_hash)  # pyright: ignore[reportUnknownMemberType]

        waited = 0.0
        while not RNS.Transport.has_path(destination_hash) and waited < timeout:  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.5)
            waited += 0.5

        if not RNS.Transport.has_path(destination_hash):  # pyright: ignore[reportUnknownMemberType]
            raise ValueError(
                f"Connection timeout: could not find path to destination {destination_hexhash[:8]}... "
                + f"after {timeout}s. Verify the server is running and the destination hash is correct."
            )

    identity = RNS.Identity.recall(destination_hash)  # pyright: ignore[reportUnknownMemberType]
    if identity is None:
        raise ValueError(
            f"Unknown destination: server identity not found for {destination_hexhash[:8]}... "
            + "The server may need to be restarted or the destination hash is incorrect."
        )

    destination = RNS.Destination(
        identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        APP_NAME,
        destination_hexhash,
    )

    log.debug("Connecting to %s...", destination_hexhash[:8])
    client_link = ClientLink(None, destination_hexhash, repo_path)  # type: ignore[arg-type]

    def on_link_established(link: RNS.Link):
        log.debug("Link established")
        client_link._set_link(link)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        client_link.set_connected()

    link = RNS.Link(destination, established_callback=on_link_established)
    client_link._set_link(link)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    return client_link


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
