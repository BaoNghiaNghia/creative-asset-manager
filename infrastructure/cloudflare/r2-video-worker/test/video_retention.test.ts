import assert from "node:assert/strict";
import test from "node:test";
import { VIDEO_RETENTION_SECONDS, VIDEO_EDGE_MAX_AGE_SECONDS, internalVideoCacheTtl, videoExpirySeconds } from "../src/video_retention";

test("worker retention limits", () => {
  assert.equal(VIDEO_RETENTION_SECONDS, 30 * 86400);
  assert.equal(VIDEO_EDGE_MAX_AGE_SECONDS, 3600);
  assert.equal(internalVideoCacheTtl(150,100),50);
  assert.equal(internalVideoCacheTtl(5000,100),3600);
  assert.equal(internalVideoCacheTtl(100,100),0);
  assert.equal(videoExpirySeconds(new Date(1000*1000),1000),1000+VIDEO_RETENTION_SECONDS);
  assert.equal(videoExpirySeconds(undefined,1000),null);
});
