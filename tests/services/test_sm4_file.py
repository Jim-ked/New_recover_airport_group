import unittest

from backend.security.sm4 import SM4Cipher, SM4Error, SM4FileCipher, sm4_encrypt_block


class SM4FileTests(unittest.TestCase):
    def test_standard_block_vector(self):
        key = bytes.fromhex("0123456789abcdeffedcba9876543210")
        self.assertEqual("681edf34d206965e86b3e94f536e4246",
                         sm4_encrypt_block(key, SM4Cipher(key)._rk).hex())

    def test_whole_file_roundtrip_has_random_iv_and_integrity(self):
        cipher = SM4FileCipher(b"0123456789abcdef")
        plain = "完整文件内容，非字段加密".encode() * 20
        first = cipher.encrypt_file(plain)
        self.assertTrue(cipher.is_encrypted(first))
        self.assertNotEqual(first, cipher.encrypt_file(plain))
        self.assertEqual(plain, cipher.decrypt_file(first))
        with self.assertRaises(SM4Error):
            SM4FileCipher(b"fedcba9876543210").decrypt_file(first)
        for corrupt in (b"", first[:-1], first[:30] + bytes([first[30] ^ 1]) + first[31:]):
            with self.subTest(corrupt=corrupt[:10]):
                with self.assertRaises(SM4Error): cipher.decrypt_file(corrupt)

    def test_missing_or_invalid_key_fails(self):
        for env in ({}, {"AIRPORT_GROUP_SM4_KEY": "bad"}):
            with self.assertRaises(SM4Error): SM4FileCipher.from_environment(env)
        configured = SM4FileCipher.from_environment({"AIRPORT_GROUP_SM4_KEY": "30313233343536373839616263646566"})
        self.assertEqual(b"data", configured.decrypt_file(configured.encrypt_file(b"data")))
