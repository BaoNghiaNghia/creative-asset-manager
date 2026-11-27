"""Pre-SMS draft regression tests; runnable with stdlib unittest, no DB."""
import unittest
from types import SimpleNamespace
from app.modules.etsy_spy.listing_idea_preflight import compose, validate_draft, clean_video_links, table_video_links
from app.modules.muse_bridge.ui import video_ugc_view

SHA = "a" * 64
TEMPLATE = {"id": "template-1", "name": "C1717 Print", "version": 3,
            "shop": "test-shop", "garment_code": "C1717", "prices": [{"size":"S","price":26}],
            "sizes": ["S"], "colors": ["Black"], "size_chart": "C1717",
            "package": {"weight": "1 POUND", "dimensions": "12 x 10 x 2 INCH"},
            "currency": "USD"}
SOURCE = {"title":"Research source", "keyword":"Kindness tee"}
SEO = {"status":"needs_fact_check", "job_id":"seo-1", "result":{
       "title":"Original kindness graphic tee", "description":"A draft description.",
       "highlights":["one","two","three","four","five"],
       "search_terms":["a","b","c","d","e","f"]}}
DESIGN = {"source":"mapped", "output_sha256": SHA, "position":"front", "id":"asset"}
DRAFT = {"design_sha256":SHA,"design_position":"front","garment_code":"C1717",
         "shop":"test-shop", "template":TEMPLATE, "colors":["Black"],
         "brief":"A creative brief", "customer_insight":"Draft insight",
         "occasion":"Everyday", "niche":"Kindness", "rights_review_sha256":SHA,
         "seo_review_job_id":"seo-1", "video_links":[
           {"aspect":"9:16","url":"https://drive.google.com/file/d/exampleVideo123/view"}]}


class PreflightTests(unittest.TestCase):
    def view(self, **kw):
        args = dict(listing_key="1234567890", source=SOURCE, seo=SEO,
                    designs=[DESIGN], video_ugc={"status":"not_queued","count":0,"videos":[]},
                    listing_image_count=0, saved=validate_draft(DRAFT))
        args.update(kw)
        return compose(**args)

    def test_all_fields_no_images_no_native_video_upload(self):
        view = self.view()
        self.assertEqual(view["blockers"], [])
        self.assertTrue(view["ready_for_sms_payload_review"])
        self.assertFalse(view["ready_for_sms_submit"])
        self.assertFalse(view["submitted_to_sms"])
        self.assertFalse(view["video_links"][0]["content_verified"])
        self.assertIn("no_listing_images_yet", view["warnings"])

    def test_missing_data_explicit(self):
        view = self.view(saved={})
        self.assertIn("google_drive_video_links_missing", view["warnings"])
        self.assertIn("sms_template_selection_required", view["blockers"])
        self.assertIn("seo_fact_review_required", view["blockers"])
        self.assertFalse(view["ready_for_sms_payload_review"])

    def test_changed_design_and_seo_do_not_reuse_reviews(self):
        view = self.view(designs=[{**DESIGN,"output_sha256":"b"*64}],
                         seo={**SEO,"job_id":"seo-2"})
        self.assertIn("select_current_design", view["blockers"])
        self.assertIn("design_rights_review_required", view["blockers"])
        self.assertIn("seo_fact_review_required", view["blockers"])

    def test_wrong_template_color_or_shop(self):
        draft = validate_draft({**DRAFT,"colors":["Ivory"],"shop":"other-shop"})
        view = self.view(saved=draft)
        self.assertIn("sms_template_selection_required",view["blockers"])
        self.assertIn("template_color_selection_required",view["blockers"])

    def test_manual_folder_link(self):
        links = clean_video_links([{"url":"https://drive.google.com/drive/folders/exampleFolder123?usp=sharing"}])
        self.assertEqual(links[0]["kind"],"folder")
        self.assertIsNone(links[0]["aspect"])

    def test_drive_link_invalid_and_duplicate(self):
        for entries in [
            [{"url":"http://drive.google.com/file/d/exampleVideo123/view"}],
            [{"url":"https://drive.google.com.evil.test/file/d/exampleVideo123/view"}],
            [{"url":"https://drive.google.com/file/d/exampleVideo123/view?access_token=secret"}],
            [{"url":"https://drive.google.com/file/d/exampleVideo123/view"},
             {"url":"https://drive.google.com/open?id=exampleVideo123"}],
            [{"aspect":"9:16","url":"https://drive.google.com/drive/folders/exampleFolder123"}],
        ]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                clean_video_links(entries)

    def test_visible_table_video_exports_even_when_content_qa_held_or_pending(self):
        row = {"storageVerified": True, "storage_provider": "google_drive_managed",
               "drive_file_id": "exampleVideo123", "drive_folder_id": "exampleFolder123",
               "sha256": "b" * 64}
        for verdict in ("hold", "pass", None):
            job = SimpleNamespace(listing_key="1234567890", artwork_sha256=SHA,
                                  status="qa_hold", output_receipts={
                "drive_uploads": {"9:16": row},
                "independentVideoReviewV33": {"artworkSha256": SHA,
                    "reviewedVideoSha256ByAspect": {"9:16": row["sha256"]},
                    "verdict": verdict},
            })
            projection = video_ugc_view(job)
            links = table_video_links(job, projection, job.listing_key)
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["source"], "cam_video_ugc_table")
            self.assertFalse(links[0]["content_verified"])
            self.assertFalse(links[0]["qa_required_for_sms"])
            self.assertEqual(links[0]["url"], "https://drive.google.com/file/d/exampleVideo123/view")
            view = self.view(video_ugc=projection, table_links=links)
            self.assertEqual(len(view["video_links"]), 1)
            self.assertEqual(view["video_links"][0]["source"], "cam_video_ugc_table")
            self.assertEqual(view["sms_video_fields"], [{"name":"UGC Video 9:16 (Drive link)",
                                                        "value":links[0]["url"]}])
            plan = view["sms_target_video_plan"]
            self.assertEqual(plan["candidate_video_urls"], [links[0]["url"]])
            self.assertEqual(plan["expected_target_video_count"], 1)
            self.assertEqual(plan["candidates"][0]["sha256"], row["sha256"])
            self.assertTrue(plan["prepare_required"])
            self.assertTrue(plan["readback_required"])
            self.assertFalse(plan["fields_alone_satisfy_target"])
            self.assertFalse(view["submitted_to_sms"])
            self.assertFalse(view["ready_for_sms_submit"])

    def test_no_table_video_does_not_block_draft(self):
        draft = validate_draft({**DRAFT, "video_links":[]})
        view = self.view(saved=draft)
        self.assertFalse(view["blockers"])
        self.assertIn("google_drive_video_links_missing", view["warnings"])
        self.assertEqual(view["sms_video_fields"], [])

    def test_missing_invalid_or_stale_storage_not_exported(self):
        row = {"storageVerified": True, "storage_provider": "google_drive_managed",
               "drive_file_id": "exampleVideo123", "drive_folder_id": "exampleFolder123",
               "sha256": "b" * 64}
        for bad in ({**row,"storageVerified":False},
                    {**row,"drive_file_id":"https://example.com"},
                    {**row,"sha256":"invalid"},
                    {**row,"drive_folder_id":None}):
            job = SimpleNamespace(listing_key="1234567890", artwork_sha256=SHA,
                                  status="qa_hold", output_receipts={"drive_uploads":{"9:16":bad}})
            self.assertEqual(table_video_links(job,video_ugc_view(job),job.listing_key),[])
        job = SimpleNamespace(listing_key="1234567890", artwork_sha256=SHA,
                              status="qa_hold", output_receipts={"drive_uploads":{"9:16":row}})
        projection = video_ugc_view(job)
        self.assertEqual(table_video_links(job,projection,"9999999999"),[])
        projection["videos"][0]["sha256"] = "c"*64
        self.assertEqual(table_video_links(job,projection,job.listing_key),[])

    def test_reject_unknown_write_fields(self):
        with self.assertRaises(ValueError):
            validate_draft({**DRAFT,"submitted_to_sms":True})
        with self.assertRaises(ValueError):
            validate_draft({**DRAFT,"template":{**TEMPLATE,"prices":[{"size":"S","price":float("nan")}]}})


def test_sms_video_transport_requires_target_when_available():
    from app.modules.etsy_spy.listing_idea_preflight import compose
    view = compose(listing_key="4380517795", source={}, seo={}, designs=[],
                   video_ugc={}, listing_image_count=0, saved={})
    assert view["sms_video_payload_policy"] == "target_video_urls_required_when_available"
    assert view["sms_video_upload_requested"] is False
    assert view["sms_video_share_prepare_required"] is True
    assert view["sms_target_video_plan"]["state"] == "no_verified_vertical_video"
    assert not view["sms_target_video_plan"]["fields_alone_satisfy_target"]

def test_direct_download_normalized_to_view():
    entries = clean_video_links([{"aspect":"9:16",
        "url":"https://drive.google.com/uc?id=exampleVideo123&export=download&resourcekey=0-exampleResourceKey"}])
    assert entries[0]["url"] == "https://drive.google.com/file/d/exampleVideo123/view?resourcekey=0-exampleResourceKey"


def test_tiktok_target_plan_one_vertical_and_no_manual_fallback():
    from app.modules.etsy_spy.listing_idea_preflight import sms_tiktok_target_video_plan
    row = {"kind":"file", "aspect":"9:16", "source":"cam_video_ugc_table",
           "storage_verified":True, "sha256":"a"*64,
           "url":"https://drive.google.com/file/d/exampleVertical123/view"}
    horizontal = {**row,"aspect":"16:9","url":"https://drive.google.com/file/d/exampleHorizontal123/view"}
    manual = {**row,"source":"operator"}
    folder = {**row,"kind":"folder","url":"https://drive.google.com/drive/folders/exampleFolder123"}
    unverified = {**row,"storage_verified":False}
    plan = sms_tiktok_target_video_plan([horizontal, manual, folder, unverified, row, row])
    assert plan["candidate_video_urls"] == [row["url"]]
    assert plan["expected_target_video_count"] == 1
    assert plan["state"] == "needs_prepare"
    assert sms_tiktok_target_video_plan([horizontal, manual, folder, unverified])["expected_target_video_count"] == 0


if __name__ == "__main__":
    unittest.main()
