"""Pure policy tests: no Ruby evaluation, GitHub access or credentials."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "delivery_policy.py"
TAP = "hellices/homebrew-korvid"
TAGS = ("arm64_sequoia", "sequoia")


def formula(version="0.5.0"):
    return (
        "class Korvid < Formula\n"
        f'  url "https://files.pythonhosted.org/packages/ab/korvid-{version}.tar.gz"\n'
        f'  sha256 "{"c" * 64}"\n'
        '  license "Apache-2.0"\n\n'
        '  depends_on "python@3.13"\n'
        "end\n"
    )


def release(version="0.5.0", prefix="korvid-"):
    return {
        "tag_name": prefix + version,
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": f"korvid-{version}.{tag}.bottle.tar.gz",
                "digest": "sha256:" + digest * 64,
            }
            for tag, digest in zip(TAGS, ("a", "b"))
        ],
    }


def sidecars(version="0.5.0"):
    return [
        {
            "hellices/korvid/korvid": {
                "formula": {
                    "name": "korvid",
                    "pkg_version": version,
                    "tap_git_revision": "d" * 40,
                    "tap_git_remote": "https://github.com/" + TAP,
                    "tap_git_path": "Formula/korvid.rb",
                },
                "bottle": {
                    "root_url": f"https://github.com/{TAP}/releases/download/korvid-{version}",
                    "cellar": "any",
                    "rebuild": 0,
                    "tags": {
                        tag: {
                            "filename": f"korvid-{version}.{tag}.bottle.tar.gz",
                            "local_filename": f"korvid--{version}.{tag}.bottle.tar.gz",
                            "sha256": digest * 64,
                        }
                    },
                },
            }
        }
        for tag, digest in zip(TAGS, ("a", "b"))
    ]


def bottled(version="0.5.0"):
    block = (
        "  bottle do\n"
        f'    root_url "https://github.com/{TAP}/releases/download/korvid-{version}"\n'
        f'    sha256 cellar: :any, arm64_sequoia: "{"a" * 64}"\n'
        f'    sha256 cellar: :any, sequoia:       "{"b" * 64}"\n'
        "  end\n\n"
    )
    return formula(version).replace("  depends_on", block + "  depends_on")


class TestDeliveryPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location("delivery_policy", SCRIPT)
            cls.policy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.policy)

    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "trusted delivery policy is not implemented")

    def test_stable_version_uses_numeric_ordering(self):
        self.assertGreater(
            self.policy.formula_version(formula("0.10.0")),
            self.policy.formula_version(formula("0.9.9")),
        )

    def test_version_rejects_ambiguous_or_unsupported_sources(self):
        for text in (
            formula("0.5.0rc1"),
            formula("00.5.0"),
            formula().replace("files.pythonhosted.org", "example.com"),
            formula() + formula(),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "source"):
                self.policy.formula_version(text)

    def test_release_bump_must_match_published_formula_exactly(self):
        self.assertIsNone(
            self.policy.validate_bump(
                formula("0.4.1"), formula(), formula(), release(prefix="v")
            )
        )
        with self.assertRaisesRegex(ValueError, "release formula"):
            self.policy.validate_bump(
                formula("0.4.1"),
                formula() + "# changed\n",
                formula(),
                release(prefix="v"),
            )

    def test_equal_version_cannot_remove_bottles_or_replay_obsolete_formula(self):
        with self.assertRaisesRegex(ValueError, "newer"):
            self.policy.validate_bump(
                bottled(), formula(), formula(), release(prefix="v")
            )

    def test_older_release_cannot_downgrade_main(self):
        with self.assertRaisesRegex(ValueError, "newer"):
            self.policy.validate_bump(
                formula("0.6.0"), formula(), formula(), release(prefix="v")
            )

    def test_draft_prerelease_and_wrong_tag_are_not_published_stable_releases(self):
        for field, value in (
            ("draft", True),
            ("prerelease", True),
            ("tag_name", "v0.6.0"),
        ):
            candidate = release(prefix="v")
            candidate[field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "release"),
            ):
                self.policy.validate_bump(
                    formula("0.4.1"), formula(), formula(), candidate
                )

    def test_bottle_update_preserves_formula_and_matches_published_assets(self):
        self.assertEqual(
            self.policy.validate_bottles(formula(), bottled(), release(), sidecars()),
            "d" * 40,
        )
        self.assertEqual(self.policy.without_bottle(bottled()), formula())

    def test_bottle_update_cannot_change_install_code(self):
        with self.assertRaisesRegex(ValueError, "outside the bottle"):
            self.policy.validate_bottles(
                formula(), bottled() + "# changed\n", release(), sidecars()
            )

    def test_existing_bottles_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, "already has bottles"):
            self.policy.validate_bottles(bottled(), bottled(), release(), sidecars())

    def test_both_architectures_are_mandatory(self):
        with self.assertRaisesRegex(ValueError, "platform"):
            self.policy.validate_bottles(
                formula(), bottled(), release(), sidecars()[:1]
            )

    def test_rejects_unknown_ruby_inside_bottle_block(self):
        with self.assertRaisesRegex(ValueError, "bottle block"):
            self.policy.validate_bottles(
                formula(),
                bottled().replace("  bottle do\n", '  bottle do\n    system "id"\n'),
                release(),
                sidecars(),
            )

    def test_asset_digest_must_match_metadata(self):
        published = release()
        published["assets"][0]["digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            self.policy.validate_bottles(formula(), bottled(), published, sidecars())

    def test_missing_digest_fails_closed(self):
        published = release()
        del published["assets"][0]["digest"]
        with self.assertRaisesRegex(ValueError, "digest"):
            self.policy.validate_bottles(formula(), bottled(), published, sidecars())

    def test_sidecars_must_share_build_provenance(self):
        metadata = sidecars()
        metadata[1]["hellices/korvid/korvid"]["formula"]["tap_git_revision"] = "e" * 40
        with self.assertRaisesRegex(ValueError, "revision"):
            self.policy.validate_bottles(formula(), bottled(), release(), metadata)

    def test_sidecar_source_repository_and_paths_are_pinned(self):
        for field, value in (
            ("tap_git_remote", "https://github.com/attacker/tap"),
            ("tap_git_path", "Formula/other.rb"),
            ("pkg_version", "0.4.1"),
        ):
            metadata = sidecars()
            metadata[0]["hellices/korvid/korvid"]["formula"][field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "provenance"),
            ):
                self.policy.validate_bottles(formula(), bottled(), release(), metadata)

    def test_invalid_metadata_and_cellar_cannot_be_accepted(self):
        for field, value in (("cellar", "#{system('id')}"), ("rebuild", 1)):
            metadata = sidecars()
            metadata[0]["hellices/korvid/korvid"]["bottle"][field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "metadata"),
            ):
                self.policy.validate_bottles(formula(), bottled(), release(), metadata)

    def test_publication_retry_is_noop_for_completed_matching_formula(self):
        self.assertFalse(
            self.policy.bottle_update_needed(bottled(), formula(), "0.5.0")
        )
        self.assertTrue(self.policy.bottle_update_needed(formula(), formula(), "0.5.0"))

    def test_old_bottle_build_cannot_overwrite_new_version_or_modified_source(self):
        for current in (formula("0.6.0"), formula() + "# new source\n"):
            with (
                self.subTest(current=current),
                self.assertRaisesRegex(ValueError, "changed"),
            ):
                self.policy.bottle_update_needed(current, formula(), "0.5.0")

    def test_untrusted_pr_actor_branch_or_paths_cannot_enter_automation(self):
        pr = {
            "state": "open",
            "draft": False,
            "user": {"login": "delivery[bot]", "type": "Bot"},
            "head": {
                "ref": "bump-korvid-0.5.0",
                "sha": "a" * 40,
                "repo": {"full_name": TAP},
            },
            "base": {"ref": "main", "repo": {"full_name": TAP}},
        }
        files = [{"filename": "Formula/korvid.rb", "status": "modified"}]
        self.assertEqual(
            self.policy.delivery_kind(pr, files, "delivery"), ("bump", "0.5.0")
        )
        for changed in (
            {"user": {"login": "attacker", "type": "User"}},
            {"head": {**pr["head"], "repo": {"full_name": "attacker/homebrew-korvid"}}},
            {"head": {**pr["head"], "ref": "feature/other"}},
            {"base": {**pr["base"], "ref": "feature/other"}},
            {"draft": True},
        ):
            candidate = copy.deepcopy(pr)
            candidate.update(changed)
            with (
                self.subTest(changed=changed),
                self.assertRaisesRegex(ValueError, "trusted"),
            ):
                self.policy.delivery_kind(candidate, files, "delivery")
        with self.assertRaisesRegex(ValueError, "formula-only"):
            self.policy.delivery_kind(
                pr, files + [{"filename": ".github/workflows/test.yml"}], "delivery"
            )
        with self.assertRaisesRegex(ValueError, "formula-only"):
            self.policy.delivery_kind(
                pr, [{"filename": "Formula/korvid.rb", "status": "renamed"}], "delivery"
            )


if __name__ == "__main__":
    unittest.main()
