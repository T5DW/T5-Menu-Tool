"""Minimal pure-Python AES block encryption, used when pycryptodome is missing.

Only encryption is needed: the fastfile pad is 64 AES blocks, so speed doesn't matter.
"""


def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _sbox():
    sbox = [0] * 256
    p = q = 1
    while True:
        p ^= _xtime(p) & 0xFF          # p *= 3 in GF(2^8)
        q ^= q << 1                    # q /= 3
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (q << 1 | q >> 7) ^ (q << 2 | q >> 6) ^ (q << 3 | q >> 5) ^ (q << 4 | q >> 4)
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


SBOX = _sbox()


def _expand(key):
    nk = len(key) // 4
    rounds = nk + 6
    w = [list(key[i:i + 4]) for i in range(0, len(key), 4)]
    rcon = 1
    for i in range(nk, 4 * (rounds + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon) & 0xFF
        elif nk > 6 and i % nk == 4:
            t = [SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(rounds + 1)]


class AESEncryptor:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16, 24 or 32 bytes")
        self.round_keys = _expand(key)

    def encrypt(self, block: bytes) -> bytes:
        s = [b ^ k for b, k in zip(block, self.round_keys[0])]
        last = len(self.round_keys) - 1
        for r in range(1, last + 1):
            s = [SBOX[b] for b in s]
            # ShiftRows: state is column-major, byte (row, col) at col*4 + row
            s = [s[((c + row) % 4) * 4 + row] for c in range(4) for row in range(4)]
            if r != last:
                m = []
                for c in range(4):
                    a = s[c * 4:c * 4 + 4]
                    t = a[0] ^ a[1] ^ a[2] ^ a[3]
                    m += [a[i] ^ t ^ (_xtime(a[i] ^ a[(i + 1) % 4]) & 0xFF) for i in range(4)]
                s = m
            s = [b ^ k for b, k in zip(s, self.round_keys[r])]
        return bytes(s)
