import copy
import unittest

from scripts.ui_baseline_governance import is_additive_manifest_extension


class ManifestExtensionTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "schemaVersion": 1,
            "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
            "states": ["default", "kpi-hover"],
            "governance": {"lastAcceptedProposalId": "previous-approved"},
        }

    def test_additive_viewports_and_states_are_allowed(self):
        updated = copy.deepcopy(self.original)
        updated["viewports"].append({"name": "mobileSmall", "width": 320, "height": 700})
        updated["states"].append("tab-pipeline")
        self.assertTrue(is_additive_manifest_extension(self.original, updated))

    def test_old_viewport_dimensions_cannot_be_changed(self):
        updated = copy.deepcopy(self.original)
        updated["viewports"][0]["width"] = 1500
        self.assertFalse(is_additive_manifest_extension(self.original, updated))

    def test_old_states_cannot_be_removed(self):
        updated = copy.deepcopy(self.original)
        updated["states"] = ["default", "tab-pipeline"]
        self.assertFalse(is_additive_manifest_extension(self.original, updated))

    def test_governance_cannot_be_rewritten(self):
        updated = copy.deepcopy(self.original)
        updated["states"].append("tab-pipeline")
        updated["governance"]["lastAcceptedProposalId"] = "unreviewed"
        self.assertFalse(is_additive_manifest_extension(self.original, updated))

    def test_duplicate_names_cannot_be_added(self):
        updated = copy.deepcopy(self.original)
        updated["viewports"].append({"name": "desktop", "width": 1440, "height": 900})
        self.assertFalse(is_additive_manifest_extension(self.original, updated))


if __name__ == "__main__":
    unittest.main()
