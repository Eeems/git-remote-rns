import pytest
from rnsremote.protocol import (
    HandshakePacket,
    RefListPacket,
    PackPacket,
    DonePacket,
    WantPacket,
    HavePacket,
    ErrorPacket,
    Packet,
    parse_packet,
    PACKET_HANDSHAKE,
    PACKET_REF_LIST,
    PACKET_WANT,
    PACKET_HAVE,
    PACKET_ERROR,
)


class TestHandshakePacket:
    def test_serialize_and_parse(self):
        packet = HandshakePacket(1, "/repo")
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, HandshakePacket)
        assert parsed.version == 1
        assert parsed.repo_path == "/repo"
        assert parsed.packet_type == 0x01

    def test_empty_repo_path(self):
        packet = HandshakePacket(1, "")
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, HandshakePacket)
        assert parsed.repo_path == ""

    def test_invalid_version(self):
        packet = HandshakePacket(256, "/repo")
        with pytest.raises(ValueError):
            packet.serialize()


class TestRefListPacket:
    def test_serialize_and_parse(self):
        refs = {
            "refs/heads/main": "abc123",
            "refs/tags/v1": "def456",
        }
        packet = RefListPacket(refs)
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, RefListPacket)
        assert parsed.packet_type == 0x02
        assert parsed.refs == refs

    def test_empty_refs(self):
        packet = RefListPacket({})
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, RefListPacket)
        assert parsed.refs == {}

    def test_malformed_line_raises(self):
        packet = RefListPacket({"refs/heads/main": "abc123"})
        data = packet.serialize()
        corrupted_data = data + b"\nsingleword\n"
        with pytest.raises(ValueError, match="Malformed ref line"):
            parse_packet(corrupted_data)


class TestPackPacket:
    def test_serialize(self):
        packet = PackPacket(b"\x00\x01\x02\x03")
        data = packet.serialize()
        assert data[0] == 0x05
        assert data[1:] == b"\x00\x01\x02\x03"

    def test_serialize_and_parse(self):
        original_data = b"\x00\x01\x02\x03\x04\x05"
        packet = PackPacket(original_data)
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, PackPacket)
        assert parsed.payload == original_data


class TestDonePacket:
    def test_serialize(self):
        packet = DonePacket()
        data = packet.serialize()
        assert data[0] == 0x08

    def test_empty_payload(self):
        packet = DonePacket()
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, DonePacket)

    def test_with_payload_raises(self):
        packet = DonePacket()
        data = packet.serialize() + b"extra"
        with pytest.raises(ValueError, match="no payload"):
            parse_packet(data)


class TestWantPacket:
    def test_serialize_and_parse(self):
        packet = WantPacket("abc123def456")
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, WantPacket)
        assert parsed.payload == b"abc123def456"
        assert parsed.packet_type == PACKET_WANT

    def test_empty_sha(self):
        packet = WantPacket()
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, WantPacket)
        assert parsed.payload == b""


class TestHavePacket:
    def test_serialize_and_parse(self):
        packet = HavePacket("abc123def456")
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, HavePacket)
        assert parsed.payload == b"abc123def456"
        assert parsed.packet_type == PACKET_HAVE


class TestErrorPacket:
    def test_serialize_and_parse(self):
        packet = ErrorPacket("Something went wrong")
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, ErrorPacket)
        assert parsed.message == "Something went wrong"
        assert parsed.packet_type == PACKET_ERROR

    def test_empty_message(self):
        packet = ErrorPacket()
        data = packet.serialize()
        parsed = parse_packet(data)
        assert isinstance(parsed, ErrorPacket)
        assert parsed.message == ""


class TestParsePacket:
    def test_empty_data_raises(self):
        with pytest.raises(ValueError, match="Empty packet data"):
            parse_packet(b"")

    def test_handshake_packet_type(self):
        packet = HandshakePacket(1, "/test")
        data = packet.serialize()
        assert data[0] == PACKET_HANDSHAKE

    def test_ref_list_packet_type(self):
        packet = RefListPacket({"refs/heads/main": "abc123"})
        data = packet.serialize()
        assert data[0] == PACKET_REF_LIST

    def test_unknown_packet_type_returns_base_packet(self):
        data = bytes([0xFF, 0xDE, 0xAD])  # Unknown type with payload
        parsed = parse_packet(data)
        assert isinstance(parsed, Packet)
        assert parsed.packet_type == 0xFF
        assert parsed.payload == b"\xDE\xAD"


class TestPacketBase:
    def test_serialize_packet_type(self):
        packet = HandshakePacket(1, "")
        data = packet.serialize()
        assert data[0] == PACKET_HANDSHAKE

    def test_deserialize_empty_raises(self):
        with pytest.raises(ValueError, match="Empty packet data"):
            Packet.deserialize(b"")
