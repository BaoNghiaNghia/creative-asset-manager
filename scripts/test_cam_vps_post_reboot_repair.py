"""Negative-path tests for conservative post-reboot fstab repair."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("cam-vps-post-reboot-repair.py")
spec = importlib.util.spec_from_file_location("cam_vps_post_reboot_repair", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

OLD = "/swapfile swap swap defaults 0 0\n"
ACTIVE = "/swapfile-camqa none swap sw,nofail 0 0\n"


class SwapRepairTests(unittest.TestCase):
    def test_queries_swapon_in_show_mode(self):
        from unittest.mock import patch
        import subprocess
        with patch.object(module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=module.ACTIVE_SWAP + "\n",
            )
            self.assertEqual(module.active_swap_names(), {module.ACTIVE_SWAP})
        run.assert_called_once_with(
            ["swapon", "--show=NAME", "--noheadings", "--raw"],
            capture_output=True, text=True, check=True,
        )

    def test_legacy_exact_only(self):
        original = "# /swapfile none swap defaults 0 0\n" + ACTIVE + OLD
        self.assertEqual(module.legacy_entry_indices(original), [2])
        converted = module.rewrite_old_swap(original)
        self.assertIn("# CAM 2026-10-11: invalid legacy swap retired; " + OLD, converted)
        self.assertIn(ACTIVE, converted)
        self.assertEqual(module.legacy_entry_indices(converted), [])
        self.assertEqual(module.rewrite_old_swap(converted), converted)

    def test_refuse_without_verified_active_cam_swap(self):
        with self.assertRaisesRegex(RuntimeError, "absent"):
            module.validate_fstab(ACTIVE + OLD, swaps=set())

    def test_refuse_without_persisted_cam_swap(self):
        with self.assertRaisesRegex(RuntimeError, "no live fstab"):
            module.validate_fstab(OLD, swaps={module.ACTIVE_SWAP})

    def test_refuse_ambiguous_old_swap(self):
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            module.validate_fstab(ACTIVE + OLD + OLD, swaps={module.ACTIVE_SWAP})

    def test_fix_with_backup_and_no_second_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            fstab = Path(temp) / "fstab"
            original = "# filesystem entries\n" + ACTIVE + OLD
            fstab.write_text(original, encoding="utf-8")
            backup = module.repair_fstab(fstab, swaps={module.ACTIVE_SWAP})
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            repaired = fstab.read_text(encoding="utf-8")
            self.assertEqual(module.legacy_entry_indices(repaired), [])
            self.assertIn(ACTIVE, repaired)
            self.assertIsNone(module.repair_fstab(fstab, swaps={module.ACTIVE_SWAP}))

    def test_network_audit_flags_configuring_ipv6_gap_without_proposing_route(self):
        eth0 = """
                        State: routable (configuring)
                 Online state: online
                      Address: 103.77.214.225
                               2401:3660:0:11ed::1
                               fe80::216:3eff:fec9:527
                """
        result, observations = module.assess_network_readiness(
            eth0,
            "default via 103.77.214.1 dev eth0 proto static",
            "",
            "ActiveState=failed\\nResult=exit-code",
        )
        self.assertEqual(result, "DEGRADED_REVIEW_REQUIRED")
        self.assertTrue(any("IPv6 default route" in o for o in observations))
        self.assertTrue(any("still configuring" in o for o in observations))
        self.assertTrue(any("Confirm gateway" in o for o in observations))
        self.assertFalse(any("netplan apply" in o or "on-link: true" in o for o in observations))

    def test_network_audit_does_not_treat_link_local_or_dns_as_global_ipv6(self):
        eth0 = """
                        State: routable (configured)
                      Address: 10.0.0.1
                               fe80::216:3eff:fec9:527
                          DNS: 2001:4860:4860::8888
                """
        result, observations = module.assess_network_readiness(
            eth0, "default via 10.0.0.254 dev eth0", "", "ActiveState=inactive",
        )
        self.assertEqual(result, "NETWORK_READY")
        self.assertFalse(any("IPv6 default route" in o for o in observations))

    def test_network_audit_fails_closed_if_ipv4_route_missing(self):
        result, observations = module.assess_network_readiness(
            "State: routable (configuring)", "", "", "ActiveState=failed"
        )
        self.assertEqual(result, "BLOCKED_NETWORK")
        self.assertIn("out-of-band console", observations[0])

    def test_network_check_read_only_runs_allowlisted_commands(self):
        from unittest.mock import patch
        expected = ["eth0", "ipv4", "ipv6", "wait"]
        commands = []
        samples = [
            "State: routable (configuring)\\nAddress: 2401:3660:0:11ed::1",
            "default via 103.77.214.1 dev eth0", "",
            "ActiveState=failed\\nResult=exit-code",
        ]
        def mocked_command_summary(argv):
            commands.append(argv)
            return 0, samples[len(commands) - 1]
        with patch.object(module, "command_summary", side_effect=mocked_command_summary):
            self.assertEqual(module.network_check(), 0)
        self.assertEqual(len(commands), 4)
        self.assertEqual(commands[0], ["networkctl", "status", "eth0", "--no-pager"])
        self.assertEqual(commands[1], ["ip", "-4", "route", "show", "default"])
        self.assertEqual(commands[2], ["ip", "-6", "route", "show", "default"])
        self.assertEqual(commands[3][:2], ["systemctl", "show"])

    def test_refuse_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            target.write_text(ACTIVE + OLD)
            symlink = Path(temp) / "fstab"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "non-symlink"):
                module.repair_fstab(symlink, swaps={module.ACTIVE_SWAP})
            self.assertEqual(target.read_text(), ACTIVE + OLD)


if __name__ == "__main__":
    unittest.main()
