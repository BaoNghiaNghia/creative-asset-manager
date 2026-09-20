import assert from "node:assert/strict";
import test from "node:test";
import { parseSingleByteRange } from "../src/range";

test("parseSingleByteRange supports bounded, open-ended, suffix and EOF clamping", () => {
  assert.deepEqual(parseSingleByteRange(null, 10), { kind: "none" });
  assert.deepEqual(parseSingleByteRange("bytes=0-3", 10), { kind: "valid", start: 0, end: 3, length: 4 });
  assert.deepEqual(parseSingleByteRange("bytes=4-", 10), { kind: "valid", start: 4, end: 9, length: 6 });
  assert.deepEqual(parseSingleByteRange("bytes=-4", 10), { kind: "valid", start: 6, end: 9, length: 4 });
  assert.deepEqual(parseSingleByteRange("bytes=7-999", 10), { kind: "valid", start: 7, end: 9, length: 3 });
});

test("parseSingleByteRange rejects malformed, multi and invalid ranges without overflow", () => {
  for (const value of ["bytes=0-1,4-5", "items=0-1", "bytes=-0", "bytes=5-1", "bytes=-", "bytes=9999999999999999-"]) {
    assert.equal(parseSingleByteRange(value, 10).kind, "invalid", value);
  }
  for (const value of ["bytes=10-", "bytes=0-", "bytes=-1"]) {
    assert.equal(parseSingleByteRange(value, 0).kind, "unsatisfiable", value);
  }
  assert.equal(parseSingleByteRange("bytes=10-", 10).kind, "unsatisfiable");
});
