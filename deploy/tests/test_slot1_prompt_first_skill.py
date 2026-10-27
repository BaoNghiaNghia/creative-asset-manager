"""Contract checks for the Prompt-first GatorHats Products Stage 2 Slot 1 Skill."""
from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "deploy/codex/skills/gatorhats-8869-ecommerce-front-thread-v3"


class Slot1PromptFirstSkillTest(unittest.TestCase):
    def test_original_edit_contract_and_imagegen_workflow_preserved(self):
        instructions = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        manifest = json.loads((SKILL_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("name: gatorhats-8869-ecommerce-front-thread-v3", instructions)
        self.assertEqual(manifest["skill_name"], "gatorhats-8869-ecommerce-front-thread-v3")
        self.assertIn("image_studio", manifest["workflows"])
        self.assertIn("output/final.png", instructions)
        self.assertIn("slot1_edit_mask", instructions)
        self.assertIn("unaltered_stock_reference", instructions)
        self.assertIn("design_reference", instructions)
        self.assertIn("outside the", instructions)

    def test_prompt_first_curvature_and_contact_within_mask(self):
        instructions = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "Surface-conforming embroidery",
            "curved front crown",
            "existing crown curvature",
            "stitch-foot contact",
            "Critical mask limitation",
            "cannot be produced by prompt alone",
            "Do NOT draw an outer groove",
            "flat printed fill",
            "3D puff",
        ):
            self.assertIn(phrase, instructions)
        self.assertNotIn("geometric warp the letters", instructions)
        self.assertIsNone(re.search(r"(?m)^version:\s*4\s*$", instructions))


if __name__ == "__main__":
    unittest.main()
