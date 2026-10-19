# Branding DNA Product Evidence — JSON manifest intake

This intake is for an **authorized, offline listing metadata export**, not a
browser crawler or marketplace API integration. It never fetches URLs, downloads
image bytes, marks evidence verified, grants usage rights, or dispatches Scouts.

## Operator workflow

1. In **Branding DNA → Product Explorer**, select the target Amazon/Etsy shop.
2. Choose a local `.json` file (one listing, up to 512 KB in the browser).
3. Click **Xem trước · Không ghi dữ liệu**. Backend validates the exact same
   schema as import, reads only matching tenant/shop revision metadata, returns
   a canonical SHA-256, count, and one of `new`, `already_imported`,
   `revision_conflict`; no rows are written.
4. **Nhập metadata đã kiểm tra** appears only when the shop's effective
   `access_mode` is `owned` or `authorized` and the revision is new.
   A documentary declaration or review does **not** change this effective mode.
5. Imported evidence remains `imported_unverified` with
   `coverage_state=unknown`. It is not a verified asset or Brand DNA output.

Example **shape only**; compute actual media digest hashes, timestamps, and
export references from a real authorized source. The dummy hash values below
must never be treated as proof of media authenticity:

```json
{
  "listing_key": "seller-listing-1001",
  "source_revision": "export-2026-10-19-r1",
  "export_reference": "internal-authorized-export-1019",
  "title": "Embroidered cotton cap",
  "category": "caps",
  "captured_at": "2026-10-19T09:00:00+07:00",
  "images": [
    {
      "sequence_index": 0,
      "asset_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "source_url_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "role": "hero",
      "rights_state": "unknown"
    }
  ]
}
```

The strict schema refuses raw `source_url`, `media_bytes`, caller-supplied
`tenant_id`, noncontiguous image indices, and claims of approved image rights.
Do not use this importer as a substitute for an authorized Marketplace source.

**Release condition:** keep production undeployed until CI passes for the exact
commit, the scheduled backup artifact is checked, and an isolated restore
rehearsal has been independently recorded as PASS.
