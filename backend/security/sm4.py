from __future__ import annotations

"""SM4 primitives migrated from AirportGroup_Project.zip (GB/T 32907-2016).

Files use SM4-CBC with a random IV and encrypt-then-MAC (HMAC-SHA256).
Only one environment key is needed. No field encryption or Base64 transport.
"""

import hashlib
import hmac
import os
from typing import Mapping, Optional

class SM4Error(ValueError):
    pass

_SBOX = (
    0xd6,0x90,0xe9,0xfe,0xcc,0xe1,0x3d,0xb7,0x16,0xb6,0x14,0xc2,0x28,0xfb,0x2c,0x05,
    0x2b,0x67,0x9a,0x76,0x2a,0xbe,0x04,0xc3,0xaa,0x44,0x13,0x26,0x49,0x86,0x06,0x99,
    0x9c,0x42,0x50,0xf4,0x91,0xef,0x98,0x7a,0x33,0x54,0x0b,0x43,0xed,0xcf,0xac,0x62,
    0xe4,0xb3,0x1c,0xa9,0xc9,0x08,0xe8,0x95,0x80,0xdf,0x94,0xfa,0x75,0x8f,0x3f,0xa6,
    0x47,0x07,0xa7,0xfc,0xf3,0x73,0x17,0xba,0x83,0x59,0x3c,0x19,0xe6,0x85,0x4f,0xa8,
    0x68,0x6b,0x81,0xb2,0x71,0x64,0xda,0x8b,0xf8,0xeb,0x0f,0x4b,0x70,0x56,0x9d,0x35,
    0x1e,0x24,0x0e,0x5e,0x63,0x58,0xd1,0xa2,0x25,0x22,0x7c,0x3b,0x01,0x21,0x78,0x87,
    0xd4,0x00,0x46,0x57,0x9f,0xd3,0x27,0x52,0x4c,0x36,0x02,0xe7,0xa0,0xc4,0xc8,0x9e,
    0xea,0xbf,0x8a,0xd2,0x40,0xc7,0x38,0xb5,0xa3,0xf7,0xf2,0xce,0xf9,0x61,0x15,0xa1,
    0xe0,0xae,0x5d,0xa4,0x9b,0x34,0x1a,0x55,0xad,0x93,0x32,0x30,0xf5,0x8c,0xb1,0xe3,
    0x1d,0xf6,0xe2,0x2e,0x82,0x66,0xca,0x60,0xc0,0x29,0x23,0xab,0x0d,0x53,0x4e,0x6f,
    0xd5,0xdb,0x37,0x45,0xde,0xfd,0x8e,0x2f,0x03,0xff,0x6a,0x72,0x6d,0x6c,0x5b,0x51,
    0x8d,0x1b,0xaf,0x92,0xbb,0xdd,0xbc,0x7f,0x11,0xd9,0x5c,0x41,0x1f,0x10,0x5a,0xd8,
    0x0a,0xc1,0x31,0x88,0xa5,0xcd,0x7b,0xbd,0x2d,0x74,0xd0,0x12,0xb8,0xe5,0xb4,0xb0,
    0x89,0x69,0x97,0x4a,0x0c,0x96,0x77,0x7e,0x65,0xb9,0xf1,0x09,0xc5,0x6e,0xc6,0x84,
    0x18,0xf0,0x7d,0xec,0x3a,0xdc,0x4d,0x20,0x79,0xee,0x5f,0x3e,0xd7,0xcb,0x39,0x48,
)
_FK = (0xa3b1bac6, 0x56aa3350, 0x677d9197, 0xb27022dc)
_CK = (
    0x00070e15,0x1c232a31,0x383f464d,0x545b6269,0x70777e85,0x8c939aa1,0xa8afb6bd,0xc4cbd2d9,
    0xe0e7eef5,0xfc030a11,0x181f262d,0x343b4249,0x50575e65,0x6c737a81,0x888f969d,0xa4abb2b9,
    0xc0c7ced5,0xdce3eaf1,0xf8ff060d,0x141b2229,0x30373e45,0x4c535a61,0x686f767d,0x848b9299,
    0xa0a7aeb5,0xbcc3cad1,0xd8dfe6ed,0xf4fb0209,0x10171e25,0x2c333a41,0x484f565d,0x646b7279,
)


def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def _tau(a: int) -> int:
    out = 0
    for i in range(4):
        out |= _SBOX[(a >> (8 * (3 - i))) & 0xFF] << (8 * (3 - i))
    return out


def _l_lin(b: int) -> int:
    return b ^ _rotl(b, 2) ^ _rotl(b, 10) ^ _rotl(b, 18) ^ _rotl(b, 24)


def _l_lin2(b: int) -> int:
    return b ^ _rotl(b, 13) ^ _rotl(b, 23)


def _key_expansion(key: bytes) -> list:
    mk = [int.from_bytes(key[4 * i:4 * i + 4], "big") for i in range(4)]
    k = [(mk[i] ^ _FK[i]) & 0xFFFFFFFF for i in range(4)]
    rk = []
    for i in range(32):
        t = k[i + 1] ^ k[i + 2] ^ k[i + 3] ^ _CK[i]
        k4 = k[i] ^ _l_lin2(_tau(t))
        k.append(k4)
        rk.append(k4)
    return rk


def sm4_encrypt_block(block: bytes, rk: list) -> bytes:
    x = [int.from_bytes(block[4 * i:4 * i + 4], "big") for i in range(4)]
    for i in range(32):
        x0n = x[0] ^ _l_lin(_tau((x[1] ^ x[2] ^ x[3] ^ rk[i]) & 0xFFFFFFFF))
        x = [x[1], x[2], x[3], x0n]
    result = (x[3].to_bytes(4, "big") + x[2].to_bytes(4, "big") +
              x[1].to_bytes(4, "big") + x[0].to_bytes(4, "big"))
    return result


def sm4_decrypt_block(block: bytes, rk: list) -> bytes:
    return sm4_encrypt_block(block, list(reversed(rk)))


def _pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def _unpad(data: bytes) -> bytes:
    if not data or not 1 <= data[-1] <= 16 or data[-data[-1]:] != bytes([data[-1]]) * data[-1]:
        raise SM4Error("SM4 file has invalid padding")
    return data[:-data[-1]]


class SM4Cipher:
    def __init__(self, key: bytes):
        if not isinstance(key, bytes) or len(key) != 16:
            raise SM4Error("SM4 key must be exactly 16 bytes")
        self._rk = _key_expansion(key)

    def encrypt_cbc(self, data: bytes, iv: bytes) -> bytes:
        previous = iv
        output = bytearray()
        padded = _pad(data)
        for start in range(0, len(padded), 16):
            block = bytes(a ^ b for a, b in zip(padded[start:start + 16], previous))
            previous = sm4_encrypt_block(block, self._rk)
            output.extend(previous)
        return bytes(output)

    def decrypt_cbc(self, data: bytes, iv: bytes) -> bytes:
        if not data or len(data) % 16:
            raise SM4Error("SM4 ciphertext length is invalid")
        previous = iv
        output = bytearray()
        for start in range(0, len(data), 16):
            block = data[start:start + 16]
            plain = sm4_decrypt_block(block, self._rk)
            output.extend(a ^ b for a, b in zip(plain, previous))
            previous = block
        return _unpad(bytes(output))


class SM4FileCipher:
    MAGIC = b"AGSM4\x01"

    def __init__(self, key: bytes):
        self.cipher = SM4Cipher(key)
        self.mac_key = hashlib.sha256(b"AirportGroup SM4 file authentication\x00" + key).digest()

    @classmethod
    def from_environment(cls, environ: Optional[Mapping[str, str]] = None) -> "SM4FileCipher":
        raw = (os.environ if environ is None else environ).get("AIRPORT_GROUP_SM4_KEY", "")
        if len(raw) != 32:
            raise SM4Error("AIRPORT_GROUP_SM4_KEY must be configured as 32 hexadecimal characters")
        try:
            return cls(bytes.fromhex(raw))
        except ValueError as exc:
            raise SM4Error("AIRPORT_GROUP_SM4_KEY must be configured as 32 hexadecimal characters") from exc

    @classmethod
    def is_encrypted(cls, data: bytes) -> bool:
        return data.startswith(cls.MAGIC)

    def encrypt_file(self, data: bytes) -> bytes:
        if not data:
            raise SM4Error("cannot encrypt an empty file")
        iv = os.urandom(16)
        ciphertext = self.cipher.encrypt_cbc(data, iv)
        authenticated = self.MAGIC + iv + ciphertext
        return authenticated + hmac.digest(self.mac_key, authenticated, "sha256")

    def decrypt_file(self, data: bytes) -> bytes:
        if not self.is_encrypted(data) or len(data) < len(self.MAGIC) + 16 + 16 + 32:
            raise SM4Error("invalid SM4 file header or length")
        authenticated, tag = data[:-32], data[-32:]
        if not hmac.compare_digest(tag, hmac.digest(self.mac_key, authenticated, "sha256")):
            raise SM4Error("SM4 decryption failed: wrong key or damaged file")
        start = len(self.MAGIC)
        return self.cipher.decrypt_cbc(authenticated[start + 16:], authenticated[start:start + 16])
