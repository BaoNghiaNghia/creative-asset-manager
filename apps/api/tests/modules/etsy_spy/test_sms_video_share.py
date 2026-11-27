import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
from app.modules.etsy_spy.sms_video_share import (
    MARKER, Proof, ShareError, select_proof, handoff, Drive, probe_mp4,
)

P = Proof('test-tenant', '4380517795', '9:16', 'a'*64, 'b'*64,
          'exampleFile123', 'exampleFolder123')
REQ = dict(action='prepare', platform='TiktokShop', aspect='9:16',
           expected_sha256=P.digest, expected_file_id=P.file, confirm=True)
PERMISSION = {'id': 'anyoneWithLink', 'type': 'anyone', 'role': 'reader', 'allowFileDiscovery': False}


def job():
    return SimpleNamespace(tenant_id=P.tenant, listing_key=P.listing, artwork_sha256=P.artwork,
        output_receipts={'drive_uploads': {'9:16': {'storageVerified': True,
        'storage_provider': 'google_drive_managed', 'contentType': 'video/mp4',
        'drive_file_id': P.file, 'drive_folder_id': P.folder, 'sha256': P.digest}}})


class FakeDrive:
    def __init__(self):
        self.data = dict(id=P.file, trashed=False, mimeType='video/mp4', parents=[P.folder],
            appProperties={'cam_tenant_id': P.tenant, 'cam_asset_id': P.asset,
                'cam_content_hash': P.digest}, capabilities={'canShare': True},
            permissions=[{'type': 'user', 'role': 'owner', 'id': 'owner123'}])
        self.calls = []

    async def metadata(self, f):
        assert f == P.file
        return copy.deepcopy(self.data)

    async def grant(self, f):
        assert f == P.file
        self.calls.append(('grant', f))
        self.data['permissions'].append(dict(PERMISSION))
        return PERMISSION['id']

    async def marker(self, f, value):
        assert f == P.file
        self.calls.append(('marker', f, value))
        if value is None:
            self.data['appProperties'].pop(MARKER, None)
        else:
            self.data['appProperties'][MARKER] = value

    async def revoke(self, f, value):
        assert f == P.file and value == PERMISSION['id']
        self.calls.append(('revoke', f, value))
        self.data['permissions'] = [v for v in self.data['permissions'] if v['id'] != value]


class SelectionTests(unittest.TestCase):
    def test_good(self):
        self.assertEqual(select_proof(job(), P.tenant, P.listing, REQ), P)

    def test_no_confirmation(self):
        with self.assertRaises(ShareError):
            select_proof(job(), P.tenant, P.listing, {**REQ, 'confirm': False})

    def test_wrong_tenant(self):
        with self.assertRaises(ShareError):
            select_proof(job(), 'foreign', P.listing, REQ)

    def test_wrong_listing(self):
        with self.assertRaises(ShareError):
            select_proof(job(), P.tenant, '1234567890', REQ)

    def test_stale_sha_or_file(self):
        for field, value in [('expected_sha256', 'c'*64), ('expected_file_id', P.folder)]:
            with self.subTest(field=field), self.assertRaises(ShareError):
                select_proof(job(), P.tenant, P.listing, {**REQ, field: value})

    def test_tiktok_horizontal(self):
        with self.assertRaises(ShareError):
            select_proof(job(), P.tenant, P.listing, {**REQ, 'aspect':'16:9'})

    def test_unverified_receipt(self):
        j = job()
        j.output_receipts['drive_uploads']['9:16']['storageVerified'] = False
        with self.assertRaises(ShareError):
            select_proof(j, P.tenant, P.listing, REQ)

    def test_manual_url_rejected(self):
        with self.assertRaises(ShareError):
            select_proof(job(), P.tenant, P.listing, {**REQ, 'url':'https://evil.invalid'})


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_grant_idempotent_then_revoke(self):
        drive = FakeDrive()
        probe = AsyncMock(return_value=True)
        result = await handoff(P, 'prepare', drive, probe)
        self.assertEqual(result['state'], 'download_verified')
        self.assertEqual(result['video_urls'], [P.url])
        self.assertTrue(result['changed'])
        result = await handoff(P, 'prepare', drive, probe)
        self.assertFalse(result['changed'])
        self.assertEqual(len([c for c in drive.calls if c[0]=='grant']), 1)
        result = await handoff(P, 'revoke', drive, probe)
        self.assertTrue(result['changed'])
        self.assertEqual(drive.data['permissions'], [{'type':'user','role':'owner','id':'owner123'}])
        self.assertNotIn(MARKER, drive.data['appProperties'])

    async def test_probe_failure_rolls_back(self):
        drive = FakeDrive()
        with self.assertRaisesRegex(ShareError, 'ANONYMOUS_DOWNLOAD_FAILED'):
            await handoff(P, 'prepare', drive, AsyncMock(return_value=False))
        self.assertNotIn(PERMISSION, drive.data['permissions'])
        self.assertNotIn(MARKER, drive.data['appProperties'])

    async def test_wrong_metadata_no_grant(self):
        for field, value in [('mimeType','image/png'), ('parents',['wrongFolder123']), ('trashed',True)]:
            with self.subTest(field=field):
                drive = FakeDrive()
                drive.data[field] = value
                with self.assertRaises(ShareError):
                    await handoff(P, 'prepare', drive, AsyncMock(return_value=True))
                self.assertFalse(drive.calls)

    async def test_tenant_asset_hash_mismatch(self):
        for field in ['cam_tenant_id', 'cam_asset_id', 'cam_content_hash']:
            drive = FakeDrive()
            drive.data['appProperties'][field] = 'wrong'
            with self.assertRaises(ShareError):
                await handoff(P, 'prepare', drive, AsyncMock(return_value=True))
            self.assertFalse(drive.calls)

    async def test_existing_unmanaged_public_not_revoked(self):
        drive = FakeDrive()
        drive.data['permissions'].append(dict(PERMISSION))
        with self.assertRaisesRegex(ShareError, 'NOT_CREATED_BY_CAM'):
            await handoff(P, 'revoke', drive, AsyncMock(return_value=True))
        with self.assertRaisesRegex(ShareError, 'ANONYMOUS_DOWNLOAD_FAILED'):
            await handoff(P, 'prepare', drive, AsyncMock(return_value=False))
        self.assertFalse(drive.calls)
        self.assertIn(PERMISSION, drive.data['permissions'])

    async def test_existing_searchable_or_writer_blocked(self):
        for overrides in [{'allowFileDiscovery':True}, {'role':'writer'}]:
            drive = FakeDrive()
            drive.data['permissions'].append({**PERMISSION, **overrides})
            with self.assertRaises(ShareError):
                await handoff(P, 'prepare', drive, AsyncMock(return_value=True))
            self.assertFalse(drive.calls)

    async def test_ambiguous_grant_returns_review(self):
        drive = FakeDrive()
        drive.grant = AsyncMock(side_effect=TimeoutError())
        with self.assertRaisesRegex(ShareError, 'GRANT_REVIEW_REQUIRED'):
            await handoff(P, 'prepare', drive, AsyncMock(return_value=True))

    async def test_no_share_permission(self):
        drive = FakeDrive()
        drive.data['capabilities']['canShare'] = False
        with self.assertRaises(ShareError):
            await handoff(P, 'prepare', drive, AsyncMock(return_value=True))
        self.assertFalse(drive.calls)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_grant_exact_file_only(self):
        import json
        def handler(req):
            self.assertEqual(req.url.path, '/drive/v3/files/'+P.file+'/permissions')
            self.assertEqual(req.method, 'POST')
            self.assertEqual(json.loads(req.content), {'type':'anyone','role':'reader','allowFileDiscovery':False})
            return httpx.Response(200, json={'id':'anyoneWithLink'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(await Drive(client).grant(P.file), 'anyoneWithLink')

    async def test_probe_good_and_bad(self):
        valid = bytes.fromhex('00000018') + b'ftypisom'
        for status, content_type, data, expected in [
            (206,'video/mp4',valid,True),
            (200,'application/octet-stream',valid,True),
            (200,'text/html',b'<html>Sign in</html>',False),
            (200,'video/mp4',b'not an mp4 file',False),
            (302,'video/mp4',valid,False),
            (403,'text/html',b'blocked',False),
        ]:
            with self.subTest(status=status, expected=expected):
                def handler(req):
                    self.assertEqual(req.url.host, 'drive.usercontent.google.com')
                    self.assertEqual(req.url.params['id'],P.file)
                    self.assertEqual(req.headers['range'],'bytes=0-31')
                    self.assertNotIn('authorization',req.headers)
                    self.assertNotIn('cookie',req.headers)
                    return httpx.Response(status,headers={'Content-Type':content_type},content=data)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                    self.assertEqual(await probe_mp4(client,P.file),expected)
