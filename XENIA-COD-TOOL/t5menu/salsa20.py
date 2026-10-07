"""Minimal pure-Python Salsa20/20, used when pycryptodome is missing.

Slow (a retail ui_mp.ff takes a minute or so), but it needs nothing beyond the standard library.
"""

import struct

_SIGMA = b"expand 32-byte k"
_MASK = 0xFFFFFFFF


def _rotl(v, c):
    return ((v << c) & _MASK) | (v >> (32 - c))


def _core(state):
    x = list(state)
    for _ in range(10):
        # column round
        x[4] ^= _rotl((x[0] + x[12]) & _MASK, 7); x[8] ^= _rotl((x[4] + x[0]) & _MASK, 9)
        x[12] ^= _rotl((x[8] + x[4]) & _MASK, 13); x[0] ^= _rotl((x[12] + x[8]) & _MASK, 18)
        x[9] ^= _rotl((x[5] + x[1]) & _MASK, 7); x[13] ^= _rotl((x[9] + x[5]) & _MASK, 9)
        x[1] ^= _rotl((x[13] + x[9]) & _MASK, 13); x[5] ^= _rotl((x[1] + x[13]) & _MASK, 18)
        x[14] ^= _rotl((x[10] + x[6]) & _MASK, 7); x[2] ^= _rotl((x[14] + x[10]) & _MASK, 9)
        x[6] ^= _rotl((x[2] + x[14]) & _MASK, 13); x[10] ^= _rotl((x[6] + x[2]) & _MASK, 18)
        x[3] ^= _rotl((x[15] + x[11]) & _MASK, 7); x[7] ^= _rotl((x[3] + x[15]) & _MASK, 9)
        x[11] ^= _rotl((x[7] + x[3]) & _MASK, 13); x[15] ^= _rotl((x[11] + x[7]) & _MASK, 18)
        # row round
        x[1] ^= _rotl((x[0] + x[3]) & _MASK, 7); x[2] ^= _rotl((x[1] + x[0]) & _MASK, 9)
        x[3] ^= _rotl((x[2] + x[1]) & _MASK, 13); x[0] ^= _rotl((x[3] + x[2]) & _MASK, 18)
        x[6] ^= _rotl((x[5] + x[4]) & _MASK, 7); x[7] ^= _rotl((x[6] + x[5]) & _MASK, 9)
        x[4] ^= _rotl((x[7] + x[6]) & _MASK, 13); x[5] ^= _rotl((x[4] + x[7]) & _MASK, 18)
        x[11] ^= _rotl((x[10] + x[9]) & _MASK, 7); x[8] ^= _rotl((x[11] + x[10]) & _MASK, 9)
        x[9] ^= _rotl((x[8] + x[11]) & _MASK, 13); x[10] ^= _rotl((x[9] + x[8]) & _MASK, 18)
        x[12] ^= _rotl((x[15] + x[14]) & _MASK, 7); x[13] ^= _rotl((x[12] + x[15]) & _MASK, 9)
        x[14] ^= _rotl((x[13] + x[12]) & _MASK, 13); x[15] ^= _rotl((x[14] + x[13]) & _MASK, 18)
    return struct.pack("<16I", *[(a + b) & _MASK for a, b in zip(x, state)])


def salsa20_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    if len(key) != 32 or len(nonce) != 8:
        raise ValueError("Salsa20 needs a 32-byte key and an 8-byte nonce")
    k = struct.unpack("<8I", key)
    s = struct.unpack("<4I", _SIGMA)
    n = struct.unpack("<2I", nonce)
    stream = bytearray()
    for counter in range((len(data) + 63) // 64):
        state = (s[0], k[0], k[1], k[2], k[3], s[1], n[0], n[1],
                 counter & _MASK, counter >> 32, s[2], k[4], k[5], k[6], k[7], s[3])
        stream += _core(state)
    size = len(data)
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream[:size], "big")).to_bytes(size, "big")
