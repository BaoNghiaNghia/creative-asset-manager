"""Dependency-free smoke of large design PNG's restricted API contract."""
import ast
import binascii
import pathlib
import struct
import zlib

SOURCE = pathlib.Path(__file__).parents[3] / "app/modules/etsy_spy/image_agent.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
constants = {"MAX_RECEIPT_BYTES", "MAX_PNG_BYTES",
             "MAX_DESIGN_PNG_BYTES", "MAX_DESIGN_RECEIPT_BYTES"}
target_nodes = [node for node in tree.body if
    isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id in constants for t in node.targets)]
target_nodes += [node for node in tree.body if
    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "_verify_png"]
class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)
scope = {"binascii": binascii, "HTTPException": HTTPException}
exec(compile(ast.fix_missing_locations(ast.Module(body=target_nodes, type_ignores=[])),
             str(SOURCE), "exec"), scope)

def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF))
header = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
png = (bytes([137, 80, 78, 71, 13, 10, 26, 10]) +
       chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\\x00")) +
       chunk(b"tEXt", b"x" * (23 * 1024 * 1024)) + chunk(b"IEND", b""))
verify = scope["_verify_png"]
assert scope["MAX_PNG_BYTES"] == 22_000_000
assert scope["MAX_RECEIPT_BYTES"] == 31_000_000
assert len(png) > scope["MAX_PNG_BYTES"]
try: verify(png)
except HTTPException as error: assert error.status_code == 422
else: raise AssertionError("ordinary PNG cap was bypassed")
verify(png, max_bytes=scope["MAX_DESIGN_PNG_BYTES"])
bad = bytearray(png)
bad[-15] ^= 1
try: verify(bytes(bad), max_bytes=scope["MAX_DESIGN_PNG_BYTES"])
except HTTPException as error: assert error.status_code == 422
else: raise AssertionError("large design CRC validation was bypassed")
design = next(x for x in tree.body if isinstance(x, ast.AsyncFunctionDef)
              and x.name == "upload_design_mapping")
assert "MAX_DESIGN_RECEIPT_BYTES" in ast.unparse(design)
assert "max_bytes=MAX_DESIGN_PNG_BYTES" in ast.unparse(design)
assert (4 * ((scope["MAX_DESIGN_PNG_BYTES"]+2)//3)) < scope["MAX_DESIGN_RECEIPT_BYTES"]
print("PASS: ordinary cap, large design cap, CRC and design route wiring")
