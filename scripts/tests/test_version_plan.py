import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import version_plan  # noqa: E402


SCRIPT = Path(__file__).resolve().parents[1] / "version_plan.py"


class VersionPlanTest(unittest.TestCase):
    def test_cross_language_canonical_digest_fixture(self):
        plan = {
            "repository": "dzshzx/example",
            "schema": 1,
            "versions": [
                {"baseline": "1.2.3", "namespace": "v", "target": "1.3.0"}
            ],
        }
        self.assertEqual(
            version_plan.canonical_json(plan),
            '{"repository":"dzshzx/example","schema":1,"versions":'
            '[{"baseline":"1.2.3","namespace":"v","target":"1.3.0"}]}',
        )
        self.assertEqual(
            version_plan.plan_digest(plan),
            "sha256:1eed417ccd593af576ef4e828eda87fd02791a4053eef7085a860475c9e5e3be",
        )

    def test_repository_urls_normalize_to_owner_repo(self):
        for remote in (
            "https://github.com/DzShZx/agent-skills.git",
            "ssh://git@github.com/dzshzx/agent-skills.git",
            "git@github.com:dzshzx/agent-skills.git",
        ):
            with self.subTest(remote=remote):
                self.assertEqual(
                    version_plan.normalize_repository(remote), "dzshzx/agent-skills"
                )

    def test_exact_next_patch_is_automatic_and_target_tag_is_excluded(self):
        plan, confirmations = version_plan.build_plan(
            "dzshzx/example",
            {"v": version_plan.Version.parse("1.2.4")},
            {"v1.2.3", "v1.2.4", "unrelated-9.9.9"},
            {"v1.2.4"},
        )
        self.assertEqual(confirmations, [])
        self.assertEqual(
            plan["versions"],
            [{"namespace": "v", "baseline": "1.2.3", "target": "1.2.4"}],
        )
        version_plan.verify_approval(plan, confirmations, None)
        with self.assertRaisesRegex(version_plan.PlanError, "version plan changed"):
            version_plan.verify_approval(plan, confirmations, "sha256:" + "0" * 64)

    def test_minor_major_and_skipped_patch_require_matching_digest(self):
        for target in ("1.3.0", "2.0.0", "1.2.5"):
            with self.subTest(target=target):
                plan, confirmations = version_plan.build_plan(
                    "dzshzx/example",
                    {"v": version_plan.Version.parse(target)},
                    {"v1.2.3"},
                )
                self.assertEqual(confirmations, ["v"])
                with self.assertRaisesRegex(version_plan.PlanError, "requires Version-Approval"):
                    version_plan.verify_approval(plan, confirmations, None)
                version_plan.verify_approval(
                    plan, confirmations, version_plan.plan_digest(plan)
                )

    def test_baseline_or_target_drift_invalidates_confirmation(self):
        original, _ = version_plan.build_plan(
            "dzshzx/example",
            {"v": version_plan.Version.parse("1.3.0")},
            {"v1.2.3"},
        )
        approval = version_plan.plan_digest(original)
        for target, tags in (("1.3.0", {"v1.2.3", "v1.2.4"}), ("1.4.0", {"v1.2.3"})):
            changed, confirmations = version_plan.build_plan(
                "dzshzx/example", {"v": version_plan.Version.parse(target)}, tags
            )
            with self.subTest(target=target, tags=tags):
                with self.assertRaisesRegex(version_plan.PlanError, "version plan changed"):
                    version_plan.verify_approval(changed, confirmations, approval)

    def test_unknown_baseline_and_downgrade_fail_closed(self):
        with self.assertRaisesRegex(version_plan.PlanError, "baseline .* is unknown"):
            version_plan.build_plan(
                "dzshzx/example", {"v": version_plan.Version.parse("1.0.0")}, set()
            )
        with self.assertRaisesRegex(version_plan.PlanError, "downgrade is not allowed"):
            version_plan.build_plan(
                "dzshzx/example",
                {"v": version_plan.Version.parse("1.2.2")},
                {"v1.2.3"},
            )

    def test_targets_are_complete_unique_and_namespace_sorted(self):
        targets = version_plan.parse_targets(["z/=2.0.1", "a/=1.0.1"])
        plan, _ = version_plan.build_plan(
            "dzshzx/example", targets, {"z/2.0.0", "a/1.0.0"}
        )
        self.assertEqual([item["namespace"] for item in plan["versions"]], ["a/", "z/"])
        with self.assertRaisesRegex(version_plan.PlanError, "at least one"):
            version_plan.parse_targets([])
        with self.assertRaisesRegex(version_plan.PlanError, "duplicate"):
            version_plan.parse_targets(["v=1.0.0", "v=1.0.1"])

    def test_annotated_tag_records_one_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
            Path(directory, "fixture").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "fixture"], cwd=directory, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                 "commit", "-qm", "fixture"],
                cwd=directory,
                check=True,
            )
            approval = "sha256:" + "a" * 64
            subprocess.run(
                ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                 "tag", "-a", "v1.0.0", "-m", "Release v1.0.0", "-m",
                 f"Version-Approval: {approval}"],
                cwd=directory,
                check=True,
            )
            previous = os.getcwd()
            try:
                os.chdir(directory)
                self.assertEqual(version_plan.tag_approvals("refs/tags/v1.0.0"), [approval])
            finally:
                os.chdir(previous)


class VersionPlanCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", self.remote], check=True)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        Path(self.repo, "fixture").write_text("fixture\n", encoding="utf-8")
        self.git("add", "fixture")
        self.git("commit", "-qm", "fixture")
        self.origin_url = "https://github.com/dzshzx/example.git"
        self.git("remote", "add", "origin", self.origin_url)
        self.git("config", f"url.{self.remote.as_uri()}.insteadOf", self.origin_url)
        self.tag("v1.2.3", "Release v1.2.3")
        self.git("push", "-q", "origin", "HEAD:refs/heads/master", "v1.2.3")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        )

    def tag(self, name: str, *messages: str) -> None:
        command = ["tag", "-a", name]
        for message in messages:
            command.extend(("-m", message))
        self.git(*command)

    def cli(self, command: str, target: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, SCRIPT, command, "--repository", "dzshzx/example",
             "--target", f"v={target}", *extra],
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def digest(result: subprocess.CompletedProcess[str]) -> str:
        for line in result.stdout.splitlines():
            if line.startswith("Confirmed-Version-Plan: "):
                return line.split(": ", 1)[1]
        raise AssertionError(result.stdout + result.stderr)

    def test_cli_exact_patch_needs_no_confirmation(self):
        self.tag("v1.2.4", "Release v1.2.4")
        self.git("push", "-q", "origin", "v1.2.4")
        result = self.cli("verify", "1.2.4", "--tag-ref", "refs/tags/v1.2.4")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_minor_needs_tag_confirmation_and_retry_plan_is_stable(self):
        before = self.cli("plan", "1.3.0")
        self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
        approval = self.digest(before)
        self.tag("v1.3.0", "Release v1.3.0", f"Version-Approval: {approval}")
        self.git("push", "-q", "origin", "v1.3.0")
        after = self.cli("plan", "1.3.0")
        self.assertEqual(self.digest(after), approval)
        verified = self.cli("verify", "1.3.0", "--tag-ref", "refs/tags/v1.3.0")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_cli_direct_confirmation_supports_local_preflight(self):
        planned = self.cli("plan", "1.3.0")
        approval = self.digest(planned)
        self.tag("v1.3.0", "Release v1.3.0")
        result = self.cli(
            "verify", "1.3.0", "--tag-ref", "refs/tags/v1.3.0",
            "--confirmed-version-plan", approval,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_minor_without_or_with_malformed_confirmation_fails(self):
        self.tag("v1.3.0", "Release v1.3.0")
        self.git("push", "-q", "origin", "v1.3.0")
        missing = self.cli("verify", "1.3.0", "--tag-ref", "refs/tags/v1.3.0")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("requires Version-Approval", missing.stderr)

        self.tag("v1.4.0", "Release v1.4.0", "Version-Approval: approved")
        self.git("push", "-q", "origin", "v1.4.0")
        malformed = self.cli("verify", "1.4.0", "--tag-ref", "refs/tags/v1.4.0")
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("malformed", malformed.stderr)

    def test_cli_ignores_local_tags_and_rejects_remote_baseline_drift(self):
        original = self.cli("plan", "1.3.0")
        approval = self.digest(original)
        self.tag("v1.2.9", "Local only")
        self.assertEqual(self.digest(self.cli("plan", "1.3.0")), approval)
        self.tag("v1.2.4", "Release v1.2.4")
        self.git("push", "-q", "origin", "v1.2.4")
        self.tag("v1.3.0", "Release v1.3.0", f"Version-Approval: {approval}")
        self.git("push", "-q", "origin", "v1.3.0")
        result = self.cli("verify", "1.3.0", "--tag-ref", "refs/tags/v1.3.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version plan changed", result.stderr)

    def test_cli_rejects_duplicate_approval_trailers(self):
        approval = self.digest(self.cli("plan", "1.3.0"))
        self.tag(
            "v1.3.0", "Release v1.3.0",
            f"Version-Approval: {approval}", f"Version-Approval: {approval}",
        )
        self.git("push", "-q", "origin", "v1.3.0")
        result = self.cli("verify", "1.3.0", "--tag-ref", "refs/tags/v1.3.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("more than one", result.stderr)


if __name__ == "__main__":
    unittest.main()
