"""Pre-SMS draft regression tests; runnable with stdlib unittest, no DB."""
import unittest
from app.modules.etsy_spy.listing_idea_preflight import compose, validate_draft, clean_video_links

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
        self.assertIn("google_drive_video_links_missing", view["blockers"])
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

    def test_reject_unknown_write_fields(self):
        with self.assertRaises(ValueError):
            validate_draft({**DRAFT,"submitted_to_sms":True})
        with self.assertRaises(ValueError):
            validate_draft({**DRAFT,"template":{**TEMPLATE,"prices":[{"size":"S","price":float("nan")}]}})


if __name__ == "__main__":
    unittest.main()
