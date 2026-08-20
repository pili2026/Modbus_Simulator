import struct


def encode_float32_little_swap(value: float) -> list[int]:
    """Encode a float using minimalmodbus BYTEORDER_LITTLE_SWAP semantics."""
    raw = struct.pack("<f", float(value))
    low_word, high_word = struct.unpack("<HH", raw)
    return [high_word & 0xFFFF, low_word & 0xFFFF]


def decode_float32_little_swap(words: list[int]) -> float:
    """Decode two Modbus words using BYTEORDER_LITTLE_SWAP semantics."""
    if len(words) != 2:
        raise ValueError("float32 decoding requires exactly two words")
    high_word, low_word = (int(words[0]) & 0xFFFF, int(words[1]) & 0xFFFF)
    raw = struct.pack("<HH", low_word, high_word)
    return struct.unpack("<f", raw)[0]
