from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE = Path(__file__).with_name("cam_db_restore_rehearsal.py")
SPEC = importlib.util.spec_from_file_location("cam_db_restore_rehearsal", MODULE)
assert SPEC and SPEC.loader
import sys
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


class FakeRunner:
    def __init__(self, *, identity: str, table_count: str = "3", fail_restore: bool = False):
        self.calls: list[list[str]] = []
        self.environments: list[dict] = []
        self.identity = identity
        self.table_count = table_count
        self.fail_restore = fail_restore

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        self.environments.append(kwargs.get("env", {}))
        if argv[0] == "psql":
            psql_calls = len([c for c in self.calls if c[0] == "psql"])
            result = self.identity if psql_calls == 1 else self.table_count
            return SimpleNamespace(returncode=0, stdout=result.encode("utf-8"))
        if argv[0] == "pg_restore":
            return SimpleNamespace(returncode=int(self.fail_restore and "--list" not in argv), stdout=b"")
        raise AssertionError("Unexpected command")


class RestoreRehearsalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dump = self.root / "verified.dump"
        self.dump.write_bytes(b"not-a-real-dump-but-used-with-fake-runner")
        self.dump.chmod(0o600)
        self.sha = hashlib.sha256(self.dump.read_bytes()).hexdigest()
        self.md5 = hashlib.md5(self.dump.read_bytes(), usedforsecurity=False).hexdigest()
        self.target = "cam_restore_rehearsal_20261018"
        self.request = tool.Request(
            self.dump, self.sha, self.md5, self.target, "cam_rehearsal_user",
            port=55432, evidence=self.root / "evidence.json",
        )

    def runner(self):
        return FakeRunner(identity=f"{self.target}|127.0.0.1|f|0")

    def test_success_is_single_transaction_and_never_creates_drops_database(self):
        fake = self.runner()
        report = tool.rehearse(self.request, runner=fake)
        self.assertEqual(report["RESTORE_REHEARSAL"], "PASS")
        self.assertEqual(report["RESTORED_TABLES"], 3)
        self.assertEqual(fake.calls[2][fake.calls[2].index("-p") + 1], "55432")
        self.assertTrue(report["REHEARSAL_DATABASE_VALIDATED"])
        self.assertEqual([v[0] for v in fake.calls], ["psql", "pg_restore", "pg_restore", "psql"])
        command = fake.calls[2]
        for arg in ("--single-transaction", "--exit-on-error", "--no-owner",
                    "--no-privileges", "--no-tablespaces", "--no-subscriptions"):
            self.assertIn(arg, command)
        self.assertNotIn("DROP DATABASE", " ".join(map(str, fake.calls)))
        self.assertNotIn("CREATE DATABASE", " ".join(map(str, fake.calls)))
        self.assertIn("-h", command)
        self.assertIn("127.0.0.1", command)
        self.assertEqual(stat.S_IMODE(self.request.evidence.stat().st_mode), 0o600)
        self.assertEqual(tool.json.loads(self.request.evidence.read_text()), report)

    def test_server_address_preflight_removes_postgresql_inet_mask(self):
        # PostgreSQL formats inet_server_addr()::text as 127.0.0.1/32.
        # The host() function gives the address used by identity verification.
        self.assertIn("host(inet_server_addr())", tool.PRECHECK)
        self.assertNotIn("inet_server_addr()::text", tool.PRECHECK)

    def test_production_name_rejected_before_any_db_call(self):
        fake = self.runner()
        for name in ("creative_assets", "postgres", "cam_restore_rehearsal_", "cam_restore_rehearsal_TEST"):
            with self.subTest(name=name), self.assertRaises(tool.RestoreGateError):
                tool.rehearse(tool.Request(self.dump, self.sha, self.md5, name, "cam_rehearsal_user"), runner=fake)
        self.assertEqual(fake.calls, [])

    def test_wrong_checksums_rejected_before_db(self):
        fake = self.runner()
        for sha, md5 in [("0" * 64, self.md5), (self.sha, "0" * 32)]:
            with self.assertRaisesRegex(tool.RestoreGateError, "checksum mismatch"):
                tool.rehearse(tool.Request(self.dump, sha, md5, self.target, "cam_rehearsal_user"), runner=fake)
        self.assertEqual(fake.calls, [])

    def test_symlink_and_world_readable_dump_rejected(self):
        link = self.root / "other.dump"
        link.symlink_to(self.dump)
        with self.assertRaisesRegex(tool.RestoreGateError, "nonsymlink"):
            tool.verify_request(tool.Request(link, self.sha, self.md5, self.target, "cam_rehearsal_user"))
        self.dump.chmod(0o644)
        with self.assertRaisesRegex(tool.RestoreGateError, "group/others"):
            tool.verify_request(self.request)

    def test_superuser_or_nonempty_target_rejected(self):
        for status in ("t|0", "f|1"):
            fake = FakeRunner(identity=f"{self.target}|127.0.0.1|{status}")
            with self.assertRaisesRegex(tool.RestoreGateError, "nonempty or role is superuser"):
                tool.rehearse(self.request, runner=fake)
            self.assertEqual(len(fake.calls), 1)

    def test_cross_database_identity_or_network_redirection_rejected(self):
        for identity in ("creative_assets|127.0.0.1|f|0",
                         f"{self.target}|10.10.10.10|f|0",
                         f"{self.target}||f|0"):
            fake = FakeRunner(identity=identity)
            with self.assertRaisesRegex(tool.RestoreGateError, "identity"):
                tool.rehearse(self.request, runner=fake)
            self.assertEqual(len(fake.calls), 1)

    def test_pg_restore_failure_no_pass_evidence(self):
        fake = FakeRunner(identity=f"{self.target}|127.0.0.1|f|0", fail_restore=True)
        with self.assertRaises(tool.RestoreGateError):
            tool.rehearse(self.request, runner=fake)
        self.assertFalse(self.request.evidence.exists())

    def test_no_restored_tables_fail_closed(self):
        fake = FakeRunner(identity=f"{self.target}|127.0.0.1|f|0", table_count="0")
        with self.assertRaisesRegex(tool.RestoreGateError, "no user tables"):
            tool.rehearse(self.request, runner=fake)
        self.assertFalse(self.request.evidence.exists())

    def test_existing_evidence_never_overwritten(self):
        self.request.evidence.write_text("operator evidence")
        with self.assertRaisesRegex(tool.RestoreGateError, "new"):
            tool.rehearse(self.request, runner=self.runner())
        self.assertEqual(self.request.evidence.read_text(), "operator evidence")

    def test_production_port_is_forbidden(self):
        fake = self.runner()
        unsafe = tool.Request(self.dump, self.sha, self.md5, self.target,
                              "cam_rehearsal_user", port=5432)
        with self.assertRaisesRegex(tool.RestoreGateError, "not production port"):
            tool.rehearse(unsafe, runner=fake)
        self.assertEqual(fake.calls, [])

    def test_libpq_host_redirect_environment_is_removed(self):
        fake = self.runner()
        previous = {key: os.environ.get(key) for key in
                    ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGPASSWORD")}
        try:
            os.environ["PGHOSTADDR"] = "198.51.100.7"
            os.environ["PGSERVICE"] = "prod"
            os.environ["PGOPTIONS"] = "-c search_path=unsafe"
            os.environ["PGPASSWORD"] = "secret-for-rehearsal"
            tool.rehearse(self.request, runner=fake)
            # Environment passed to each subprocess is not included in argv.
            # Other PG* connection overrides cannot redirect the destination.
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(len(fake.calls), 4)
        for env in fake.environments:
            self.assertNotIn("PGHOSTADDR", env)
            self.assertNotIn("PGSERVICE", env)
            self.assertNotIn("PGOPTIONS", env)
            self.assertEqual(env.get("PGPASSWORD"), "secret-for-rehearsal")
            self.assertEqual(env.get("PGSSLMODE"), "disable")

    def test_required_tables_are_counted_only_on_isolated_restore(self):
        request = tool.Request(
            self.dump, self.sha, self.md5, self.target, "cam_rehearsal_user",
            required_tables=("cam_agent_trial_exposures", "cam_agent_trial_qa_reviews"),
        )
        fake = self.runner()
        report = tool.rehearse(request, runner=fake)
        self.assertTrue(report["REQUIRED_TABLE_COUNTS_VERIFIED"])
        self.assertEqual(report["REQUIRED_TABLE_COUNT"], 2)
        self.assertEqual(len(fake.calls), 6)
        self.assertIn('FROM public."cam_agent_trial_exposures"', fake.calls[4][-1])
        self.assertIn('FROM public."cam_agent_trial_qa_reviews"', fake.calls[5][-1])

    def test_expected_counts_match_exact_required_tables(self):
        fake = self.runner()
        request = tool.Request(
            self.dump, self.sha, self.md5, self.target, "cam_rehearsal_user",
            required_tables=("cam_agent_trial_exposures", "cam_agent_trial_qa_reviews"),
            expected_table_counts=(("cam_agent_trial_exposures", 3),
                                   ("cam_agent_trial_qa_reviews", 3)),
        )
        report = tool.rehearse(request, runner=fake)
        self.assertTrue(report["EXPECTED_TABLE_COUNTS_MATCHED"])
        self.assertEqual(report["EXPECTED_COUNTS_PROVENANCE"], "OPERATOR_SUPPLIED_UNVERIFIED")
        self.assertEqual(len(fake.calls), 6)
        self.assertNotIn("cam_agent_trial_exposures", str(report))
        self.assertNotIn("cam_agent_trial_qa_reviews", str(report))

    def test_expected_count_mismatch_fails_without_success_evidence(self):
        fake = self.runner()
        evidence = self.root / "mismatch.json"
        request = tool.Request(
            self.dump, self.sha, self.md5, self.target, "cam_rehearsal_user",
            evidence=evidence, required_tables=("cam_agent_trial_exposures",),
            expected_table_counts=(("cam_agent_trial_exposures", 4),),
        )
        with self.assertRaisesRegex(tool.RestoreGateError, "differs from independent expected"):
            tool.rehearse(request, runner=fake)
        self.assertFalse(evidence.exists())
        self.assertEqual(len(fake.calls), 5)

    def test_expected_table_coverage_duplicates_and_invalid_types_fail_before_db(self):
        fake = self.runner()
        for expected in (
            (("not_required", 3),),
            (("cam_agent_trial_exposures", 3), ("cam_agent_trial_exposures", 3)),
            (("cam_agent_trial_exposures", -1),),
            (("cam_agent_trial_exposures", True),),
            (("cam_agent_trial_exposures", "3"),),
            (("cam_agent_trial_exposures", 9223372036854775808),),
            (("bad;drop", 3),),
        ):
            request = tool.Request(
                self.dump, self.sha, self.md5, self.target, "cam_rehearsal_user",
                required_tables=("cam_agent_trial_exposures",),
                expected_table_counts=expected,
            )
            with self.subTest(expected=expected), self.assertRaises(tool.RestoreGateError):
                tool.rehearse(request, runner=fake)
        self.assertEqual(fake.calls, [])

    def test_expected_table_cli_parser_rejects_injection_and_noninteger(self):
        for value in (
            "cam_agent_trial_exposures=-1", "cam_agent_trial_exposures=1.5",
            "cam_agent_trial_exposures=1;DROP", "WrongCase=1",
            "cam_agent_trial_exposures", "cam_agent_trial_exposures=",
            "cam_agent_trial_exposures=9223372036854775808",
        ):
            with self.subTest(value=value), self.assertRaises(tool.RestoreGateError):
                tool._parse_expected_table_counts([value])
        self.assertEqual(
            tool._parse_expected_table_counts(
                ["cam_agent_trial_exposures=0", "cam_agent_trial_qa_reviews=123"]
            ),
            (("cam_agent_trial_exposures", 0), ("cam_agent_trial_qa_reviews", 123)),
        )

    def test_required_table_identifiers_fail_closed_before_database_commands(self):
        fake = self.runner()
        for names in (("foo;DROP TABLE x",), ("UpperCase",),
                      ("same_name", "same_name"), ("missing.dot",),
                      tuple("table_" + str(n) for n in range(21))):
            request = tool.Request(
                self.dump, self.sha, self.md5, self.target,
                "cam_rehearsal_user", required_tables=names,
            )
            with self.subTest(names=names), self.assertRaises(tool.RestoreGateError):
                tool.rehearse(request, runner=fake)
        self.assertEqual(fake.calls, [])

    def test_required_table_failure_never_marks_restore_evidence_pass(self):
        class MissingTableRunner(FakeRunner):
            def __call__(self, argv, **kwargs):
                if argv[0] == "psql" and 'FROM public."' in argv[-1]:
                    self.calls.append(argv)
                    return SimpleNamespace(returncode=1, stdout=b"")
                return super().__call__(argv, **kwargs)

        fake = MissingTableRunner(identity=f"{self.target}|127.0.0.1|f|0")
        request = tool.Request(
            self.dump, self.sha, self.md5, self.target,
            "cam_rehearsal_user", evidence=self.root / "failed-required.json",
            required_tables=("cam_agent_trial_exposures",),
        )
        with self.assertRaises(tool.RestoreGateError):
            tool.rehearse(request, runner=fake)
        self.assertFalse(request.evidence.exists())

    def test_no_password_in_subprocess_argv_or_report(self):
        fake = self.runner()
        original = os.environ.get("PGPASSWORD")
        try:
            os.environ["PGPASSWORD"] = "secret-that-must-not-appear"
            report = tool.rehearse(self.request, runner=fake)
        finally:
            if original is None:
                os.environ.pop("PGPASSWORD", None)
            else:
                os.environ["PGPASSWORD"] = original
        self.assertNotIn("secret-that-must-not-appear", str(report) + str(fake.calls))


if __name__ == "__main__":
    unittest.main()
