"""Reference preview tests: no DB, no external IO, no original exposure."""
import io
import hashlib
import unittest
from PIL import Image
from fastapi import HTTPException
from app.modules.etsy_spy.sms_design_preview import signature, verify, render_preview, TTL

SECRET = "abcdeFGHIJ1234567890!@#$%^&*()-=+"
AGENT = "12345678-1234-1234-1234-123456789abc"
ID = "4562154184"
SHA = "a" * 64


class SmsPreviewTests(unittest.TestCase):
    def test_exact_scope_and_expiry(self):
        exp = 1000 + TTL
        token = signature(SECRET, AGENT, "boostgame", ID, SHA, exp)
        self.assertTrue(verify(SECRET, AGENT, "boostgame", ID, SHA, exp, token, now=1000))
        self.assertFalse(verify(SECRET, AGENT, "other", ID, SHA, exp, token, now=1000))
        self.assertFalse(verify(SECRET, AGENT, "boostgame", "9999999999", SHA, exp, token, now=1000))
        self.assertFalse(verify(SECRET, AGENT, "boostgame", ID, "b"*64, exp, token, now=1000))
        self.assertFalse(verify(SECRET, AGENT, "boostgame", ID, SHA, exp, token, now=exp))
        self.assertFalse(verify(SECRET, AGENT, "boostgame", ID, SHA, exp, token, now=999))
        self.assertFalse(verify("", AGENT, "boostgame", ID, SHA, exp, token, now=1000))
        self.assertFalse(verify(SECRET, AGENT, "boostgame", ID, SHA, exp, "0"*64, now=1000))

    def test_resize_exact_source_no_alpha_master(self):
        src = Image.new("RGBA", (1440,1080), (200,10,30,128))
        raw = io.BytesIO()
        src.save(raw, "PNG")
        digest = hashlib.sha256(raw.getvalue()).hexdigest()
        preview = render_preview(raw.getvalue(), digest)
        with Image.open(io.BytesIO(preview)) as result:
            self.assertEqual(result.size, (720,540))
            self.assertEqual(result.mode,"RGB")
        self.assertNotEqual(hashlib.sha256(preview).hexdigest(), digest)

    def test_wrong_sha_and_non_png_rejected(self):
        with self.assertRaises(HTTPException) as error:
            render_preview(b"not png", SHA)
        self.assertEqual(error.exception.status_code,409)
        raw=b"not png"
        with self.assertRaises(HTTPException) as error:
            render_preview(raw, hashlib.sha256(raw).hexdigest())
        self.assertEqual(error.exception.status_code,422)


if __name__ == "__main__":
    unittest.main()
