PROTOCOL_VERSION = 1
APP_NAME = "git"

PACKET_HANDSHAKE = 0x01
PACKET_REF_LIST = 0x02
PACKET_WANT = 0x03
PACKET_HAVE = 0x04
PACKET_PACK = 0x05

PACKET_DONE = 0x08
PACKET_ERROR = 0xFF


class Packet:
    def __init__(self, packet_type: int, payload: bytes = b""):
        self.packet_type = packet_type
        self.payload = payload

    def serialize(self) -> bytes:
        return bytes([self.packet_type]) + self.payload

    @classmethod
    def deserialize(cls, data: bytes) -> "Packet":
        if not data:
            raise ValueError("Empty packet data")
        packet_type = data[0]
        payload = data[1:] if len(data) > 1 else b""
        return cls(packet_type, payload)


class HandshakePacket(Packet):
    def __init__(self, version: int, repo_path: str = ""):
        super().__init__(PACKET_HANDSHAKE)
        self.version = version
        self.repo_path = repo_path

    def serialize(self) -> bytes:
        if not (0 <= self.version <= 255):
            raise ValueError(f"Version must be 0-255, got {self.version}")
        try:
            payload = self.version.to_bytes(1, "big") + self.repo_path.encode("utf-8")
        except UnicodeEncodeError as e:
            raise ValueError(f"Failed to encode repo_path: {e}") from e
        return bytes([self.packet_type]) + payload

    @classmethod
    def deserialize(cls, data: bytes) -> "HandshakePacket":
        if not data:
            raise ValueError("Empty handshake data")
        try:
            version = data[0]
            repo_path = data[1:].decode("utf-8") if len(data) > 1 else ""
        except UnicodeDecodeError as e:
            raise ValueError(f"Failed to decode handshake data: {e}") from e
        return cls(version, repo_path)


class RefListPacket(Packet):
    def __init__(self, refs: dict[str, str]):
        super().__init__(PACKET_REF_LIST)
        self.refs = refs

    def serialize(self) -> bytes:
        lines = []
        for name, sha in self.refs.items():
            lines.append(f"{sha} {name}")
        payload = "\n".join(lines).encode("utf-8")
        return bytes([self.packet_type]) + payload

    @classmethod
    def deserialize(cls, data: bytes) -> "RefListPacket":
        refs = {}
        if data:
            decoded = data.decode("utf-8")
            for line in decoded.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(" ", 1)
                if len(parts) != 2:
                    raise ValueError(f"Malformed ref line: {line!r}")
                sha, name = parts
                refs[name] = sha
        return cls(refs)


class PackPacket(Packet):
    def __init__(self, data: bytes):
        super().__init__(PACKET_PACK, data)

    @classmethod
    def deserialize(cls, data: bytes) -> "PackPacket":
        return cls(data)


class DonePacket(Packet):
    def __init__(self):
        super().__init__(PACKET_DONE)

    @classmethod
    def deserialize(cls, data: bytes) -> "DonePacket":
        if data:
            raise ValueError("DonePacket should have no payload")
        return cls()


class WantPacket(Packet):
    def __init__(self, sha: str = ""):
        super().__init__(PACKET_WANT, sha.encode("utf-8") if sha else b"")

    @classmethod
    def deserialize(cls, data: bytes) -> "WantPacket":
        return cls(data.decode("utf-8") if data else "")


class HavePacket(Packet):
    def __init__(self, sha: str = ""):
        super().__init__(PACKET_HAVE, sha.encode("utf-8") if sha else b"")

    @classmethod
    def deserialize(cls, data: bytes) -> "HavePacket":
        return cls(data.decode("utf-8") if data else "")


class ErrorPacket(Packet):
    def __init__(self, message: str = ""):
        super().__init__(PACKET_ERROR, message.encode("utf-8") if message else b"")
        self.message = message

    @classmethod
    def deserialize(cls, data: bytes) -> "ErrorPacket":
        return cls(data.decode("utf-8") if data else "")


def parse_packet(data: bytes) -> Packet:
    if not data:
        raise ValueError("Empty packet data")

    packet_type = data[0]
    payload = data[1:] if len(data) > 1 else b""

    packet_classes = {
        PACKET_HANDSHAKE: HandshakePacket,
        PACKET_REF_LIST: RefListPacket,
        PACKET_WANT: WantPacket,
        PACKET_HAVE: HavePacket,
        PACKET_PACK: PackPacket,
        PACKET_DONE: DonePacket,
        PACKET_ERROR: ErrorPacket,
    }

    if packet_type in packet_classes:
        return packet_classes[packet_type].deserialize(payload)
    return Packet.deserialize(data)
