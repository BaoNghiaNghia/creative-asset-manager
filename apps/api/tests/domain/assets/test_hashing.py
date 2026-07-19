import hashlib
import unittest

from app.domain.assets.hashing import sha256_stream


async def chunks(*values: bytes):
    for value in values:
        yield value


class StreamingHashTest(unittest.IsolatedAsyncioTestCase):
    async def test_sha256_is_calculated_incrementally(self) -> None:
        result = await sha256_stream(chunks(b"creative", b"-", b"asset"))

        self.assertEqual(
            result.content_hash,
            hashlib.sha256(b"creative-asset").hexdigest(),
        )
        self.assertEqual(result.size_bytes, len(b"creative-asset"))

    async def test_non_byte_chunk_is_rejected(self) -> None:
        async def invalid_chunks():
            yield "not-bytes"

        with self.assertRaises(TypeError):
            await sha256_stream(invalid_chunks())
