"""DOM-snapshot regression: geometry matches pins without repeated image layouts.

Node's built-in VM runs the exact JS passed to Pinterest's page.evaluate.
This uses a synthetic DOM, never opens a real Pinterest profile or account.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


SCOUT_FILE = Path(__file__).with_name("scout.py")

_NODE_FIXTURE = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[1], 'utf8');
const begin = source.indexOf('async def extract_visible(page: Any)');
assert.ok(begin > 0, 'extract_visible function missing');
const marker = source.indexOf('r"""', begin);
assert.ok(marker > 0, 'browser DOM evaluation missing');
const end = source.indexOf('"""', marker + 4);
const expression = source.slice(marker + 4, end);

let imageLayoutReads = 0;
const images = [];
const anchors = [];
for (let i = 0; i < 64; i += 1) {
  const x = (i % 8) * 200, y = Math.floor(i / 8) * 200;
  const bounds = (dx) => ({
    left: x + dx, top: y + dx, right: x + dx + 65,
    bottom: y + dx + 65, width: 65, height: 65,
  });
  const imageUrl = `https://i.pinimg.com/originals/image-${i}.jpg`;
  images.push({
    src: imageUrl, currentSrc: imageUrl, naturalWidth: 512, alt: '',
    parentElement: null,
    getBoundingClientRect: () => { imageLayoutReads += 1; return bounds(5); },
    getAttribute: () => null, closest: () => null,
  });
  anchors.push({
    href: `https://www.pinterest.com/pin/${100000 + i}/`,
    parentElement: null, textContent: '',
    getBoundingClientRect: () => bounds(0),
    getAttribute: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  });
}
const document = {
  querySelectorAll(selector) {
    if (selector.startsWith('a[')) return anchors;
    if (selector.startsWith('img[')) return images;
    throw new Error('Unexpected selector: ' + selector);
  },
};
const extracted = vm.runInNewContext(expression, {document}, {timeout: 10000});
const rows = extracted();
assert.equal(rows.length, 64, 'must preserve all Pin candidates');
for (let i = 0; i < 64; i += 1) {
  assert.equal(rows[i].pin_url, anchors[i].href);
  assert.equal(rows[i].image_url, images[i].src);
  assert.equal(rows[i].pairing_strategy, 'geometry');
}
assert.ok(imageLayoutReads <= 64,
  'Pin/image fallback must measure each image at most once, saw ' + imageLayoutReads);
const geometryLayoutReads = imageLayoutReads;

// Directly nested images take precedence over proximity geometry, and ties
// on visible area still prefer the larger natural image rendition.
const directAnchor = {
  ...anchors[0],
  querySelectorAll(selector) {
    return selector.startsWith('img[') ? [images[0], images[1]] : [];
  },
};
images[1].naturalWidth = 1024;
document.querySelectorAll = (selector) =>
  selector.startsWith('a[') ? [directAnchor] : [images[0], images[1]];
const direct = extracted();
assert.equal(direct[0].pin_url, directAnchor.href);
assert.equal(direct[0].image_url, images[1].src);
assert.equal(direct[0].pairing_strategy, 'direct');

process.stdout.write(JSON.stringify({pins: rows.length, imageLayoutReads: geometryLayoutReads}));
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
def test_pinterest_pin_geometry_reads_each_image_layout_at_most_once():
    result = subprocess.run(
        [shutil.which("node"), "-e", _NODE_FIXTURE, str(SCOUT_FILE)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"pins":64' in result.stdout
    assert '"imageLayoutReads":64' in result.stdout


def test_visible_thumbnail_probe_is_bounded_to_twelve_layout_reads():
    source = Path(__file__).with_name("network_load.py").read_text(encoding="utf-8")
    start = source.index("async def wait_for_visible_pin_images(")
    end = source.index("async def wait_between_products(", start)
    probe_source = source[start:end]
    assert "for (const img of images)" in probe_source
    assert 'img[srcset*="pinimg.com"]' in probe_source
    assert "if (visible >= 12) break;" in probe_source
    assert "Array.from(document.querySelectorAll('img[src*" not in probe_source


_NODE_IMAGE_PROBE = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[1], 'utf8');
const begin = source.indexOf('async def wait_for_visible_pin_images(');
const marker = source.indexOf('r"""', begin);
assert.ok(begin >= 0 && marker > begin);
const end = source.indexOf('"""', marker + 4);
const expression = source.slice(marker + 4, end);

let geometryReads = 0;
const images = Array.from({length: 80}, (_, i) => ({
  // Simulates modern Pinterest thumbnails with srcset but no src.
  src: '', srcset: `https://i.pinimg.com/236x/pin-${i}.jpg 236w`,
  complete: true, naturalWidth: 236,
  getBoundingClientRect() {
    geometryReads += 1;
    return {left: 0, top: 0, width: 90, height: 120, bottom: 120};
  },
}));
const document = {
  querySelectorAll(selector) {
    if (selector.startsWith('a[')) return [{}, {}];
    if (selector.includes('srcset*="pinimg.com"')) return images;
    return [];
  },
};
const fn = vm.runInNewContext(expression,
  {document, window: {innerHeight: 900}}, {timeout: 5000});
const sample = fn();
assert.equal(sample.pin_links, 2);
assert.equal(sample.images_visible, 12);
assert.equal(sample.images_loaded, 12);
assert.equal(geometryReads, 12, 'must stop at the twelfth visible image');
process.stdout.write(JSON.stringify(sample));
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
def test_srcset_only_thumbnails_can_be_ready_without_full_feed_layout():
    result = subprocess.run(
        [shutil.which("node"), "-e", _NODE_IMAGE_PROBE,
         str(SCOUT_FILE.with_name("network_load.py"))],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"images_visible":12' in result.stdout
    assert '"images_loaded":12' in result.stdout
