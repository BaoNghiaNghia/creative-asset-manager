#!/usr/bin/env python3
"""Offline validation of the actual native-thumbnail implementation.

Pillow is installed in the system interpreter. This isolates the function
AST rather than importing CAM's production service dependencies or OAuth.
The integration tests remain in apps/api/tests/modules/etsy_spy/.
"""
from __future__ import annotations
import ast
import hashlib
import io
from pathlib import Path
import unittest
from PIL import Image, UnidentifiedImageError

SOURCE = Path(__file__).resolve().parents[1] / "apps/api/app/modules/etsy_spy/imagegen_drive.py"
tree = ast.parse(SOURCE.read_text())
fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_thumbnail")
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


namespace = {"Image": Image, "io": io, "HTTPException": HTTPException,
             "UnidentifiedImageError": UnidentifiedImageError}
exec(compile(module, str(SOURCE), "exec"), namespace)
thumbnail = namespace["_thumbnail"]


def rgba(width=512, height=512, opaque=False):
    image = Image.new("RGBA", (width, height), (10, 30, 50, 255 if opaque else 0))
    if not opaque:
        image.paste((10, 30, 50, 255), (128, 128, 300, 300))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class NativeThumbnailContract(unittest.TestCase):
    def test_native_png_preserved_and_preview_small(self):
        original = rgba()
        before = hashlib.sha256(original).hexdigest()
        preview, dims = thumbnail(original, native_master=True)
        self.assertEqual(dims, (512, 512))
        self.assertEqual(hashlib.sha256(original).hexdigest(), before)
        with Image.open(io.BytesIO(preview)) as p:
            self.assertEqual(p.size, (192, 192))
            self.assertEqual(p.mode, "RGBA")

    def test_opaque_rejected(self):
        with self.assertRaises(HTTPException) as error:
            thumbnail(rgba(opaque=True), native_master=True)
        self.assertEqual(error.exception.status_code, 422)

    def test_wrong_dimensions_rejected(self):
        for data in (rgba(4200, 512), rgba(128, 128)):
            with self.assertRaises(HTTPException):
                thumbnail(data, native_master=True)

    def test_non_png_rejected(self):
        with self.assertRaises(HTTPException):
            thumbnail(b"this is not a PNG", native_master=True)

    def test_print_contract_does_not_accept_native_size(self):
        with self.assertRaises(HTTPException):
            thumbnail(rgba(), native_master=False)


if __name__ == "__main__":
    unittest.main()
