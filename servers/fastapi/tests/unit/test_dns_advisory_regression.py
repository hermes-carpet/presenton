import pytest


def test_dns_decompression_bounds_pointer_chains_and_preserves_compressed_names():
    import dns.exception
    import dns.name

    def compressed_root(pointers):
        wire = bytearray(b"\0")
        previous = 0
        for _ in range(pointers):
            offset = len(wire)
            wire.extend((0xC000 | previous).to_bytes(2, "big"))
            previous = offset
        return bytes(wire), previous

    for count in (0, 1, 16):
        wire, offset = compressed_root(count)
        name, consumed = dns.name.from_wire(wire, offset)
        assert name == dns.name.root
        assert consumed == (1 if count == 0 else 2)
    for count in (17, 18, 100):
        wire, offset = compressed_root(count)
        with pytest.raises(dns.exception.FormError):
            dns.name.from_wire(wire, offset)
    wire = b"\x07example\x03com\x00\x03www\xc0\x00"
    name, consumed = dns.name.from_wire(wire, 13)
    assert name.to_text() == "www.example.com."
    assert consumed == 6
