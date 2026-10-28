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


class Slot1PromptFirstV4SkillTest(unittest.TestCase):
    def test_v4_is_independently_versioned_and_manifest_consistent(self):
        folder = ROOT / "deploy/codex/skills/gatorhats-8869-ecommerce-front-thread-v4"
        instructions = (folder / "SKILL.md").read_text(encoding="utf-8")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        agents = (folder / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("name: gatorhats-8869-ecommerce-front-thread-v4", instructions)
        self.assertIn("version: 4.0.0", instructions)
        self.assertEqual(manifest["skill_name"], "gatorhats-8869-ecommerce-front-thread-v4")
        self.assertEqual(manifest["display_name"], "8869 Front - Prompt-first v4")
        self.assertIn("Prompt-first v4", agents)
        self.assertEqual(manifest["workflows"], ["image_studio"])
        self.assertEqual(set(manifest["required_reference_roles"]),
                         {"design_reference", "unaltered_stock_reference", "slot1_edit_mask"})
        for token in ("output/final.png", "2-pixel", "stitch", "curvature", "unaltered_stock_reference"):
            self.assertIn(token, instructions)




if __name__ == "__main__":
    unittest.main()
