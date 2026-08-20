"""Per-directory consent store for project skills (``kiro_crew.skill_trust``).

The gate must fail CLOSED on every unreadable/malformed/ambiguous input: a
``SKILL.md`` enters the agent's context and can instruct it to run anything, so
"we could not tell" has to mean "not trusted". These tests pin that direction
for each failure mode individually, plus the canonical-key identity that stops
one directory being granted twice under two names.
"""

from __future__ import annotations

import json
import os

import pytest

from kiro_crew import skill_trust


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point the data home at tmp_path and drop the memoized enforcement read."""
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    skill_trust.reset_cache_for_tests()
    yield
    skill_trust.reset_cache_for_tests()


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    (d / ".kiro" / "skills").mkdir(parents=True)
    return d


class TestCanonicalKey:
    def test_none_is_not_a_key(self):
        assert skill_trust.canonical_key(None) is None

    def test_blank_is_not_a_key(self):
        assert skill_trust.canonical_key("   ") is None

    def test_relative_path_is_refused(self):
        # A relative path cannot identify a directory independently of cwd.
        assert skill_trust.canonical_key("rel/path") is None

    def test_missing_path_is_refused(self, tmp_path):
        assert skill_trust.canonical_key(tmp_path / "nope") is None

    def test_a_file_is_refused(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert skill_trust.canonical_key(f) is None

    def test_real_directory_resolves(self, project):
        assert skill_trust.canonical_key(project) == os.path.realpath(project)

    def test_symlink_resolves_to_the_same_key_as_its_target(self, project, tmp_path):
        link = tmp_path / "alias"
        try:
            link.symlink_to(project)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")
        # The directory IS the resource: an alias must not be a second identity,
        # or it could carry its own grant for an already-refused directory.
        assert skill_trust.canonical_key(link) == skill_trust.canonical_key(project)


class TestGateBeforeConsent:
    def test_untrusted_project_is_refused(self, project):
        assert skill_trust.is_project_trusted(project) is False

    def test_no_grants_means_empty_key_set(self):
        assert skill_trust.trusted_keys() == frozenset()

    def test_unusable_key_is_refused_without_touching_the_store(self, tmp_path):
        assert skill_trust.is_key_trusted("") is False
        assert skill_trust.is_key_trusted(None) is False


class TestGrantAndRevoke:
    def test_grant_makes_the_project_trusted(self, project):
        key = skill_trust.grant_project_trust(project)
        assert key == os.path.realpath(project)
        assert skill_trust.is_project_trusted(project) is True

    def test_grant_is_idempotent(self, project):
        skill_trust.grant_project_trust(project)
        skill_trust.grant_project_trust(project)
        assert len(skill_trust.list_trusted_projects()) == 1

    def test_grant_refuses_a_path_that_cannot_name_a_directory(self, tmp_path):
        # Banking a grant against a path that will never match would leave the
        # operator believing they had consented.
        with pytest.raises(ValueError):
            skill_trust.grant_project_trust(tmp_path / "nope")

    def test_store_is_owner_only(self, project):
        skill_trust.grant_project_trust(project)
        mode = os.stat(skill_trust.store_path()).st_mode & 0o777
        assert mode == 0o600

    def test_a_grant_does_not_trust_a_sibling_directory(self, project, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        skill_trust.grant_project_trust(project)
        assert skill_trust.is_project_trusted(other) is False

    def test_revoke_removes_the_grant(self, project):
        skill_trust.grant_project_trust(project)
        assert skill_trust.revoke_project_trust(project) is True
        assert skill_trust.is_project_trusted(project) is False

    def test_revoking_an_ungranted_project_reports_no_removal(self, project):
        assert skill_trust.revoke_project_trust(project) is False

    def test_revoke_works_after_the_directory_is_gone(self, project):
        # The operator must be able to withdraw trust from a path they have
        # already deleted, so revoke matches the stored string too.
        skill_trust.grant_project_trust(project)
        stored = skill_trust.list_trusted_projects()[0]["path"]
        os.rmdir(project / ".kiro" / "skills")
        os.rmdir(project / ".kiro")
        os.rmdir(project)
        assert skill_trust.canonical_key(project) is None
        assert skill_trust.revoke_project_trust(stored) is True

    def test_revoke_is_immediate_not_deferred(self, project):
        skill_trust.grant_project_trust(project)
        assert skill_trust.is_project_trusted(project) is True
        skill_trust.revoke_project_trust(project)
        # No TTL wait: the next read must already refuse.
        assert skill_trust.is_project_trusted(project) is False


class TestListing:
    def test_listing_reports_a_grant_whose_directory_vanished(self, project):
        skill_trust.grant_project_trust(project)
        os.rmdir(project / ".kiro" / "skills")
        os.rmdir(project / ".kiro")
        os.rmdir(project)
        rows = skill_trust.list_trusted_projects()
        # A stale row must stay visible or it becomes invisible AND un-revokable.
        assert len(rows) == 1
        assert rows[0]["exists"] is False

    def test_listing_marks_a_live_grant_as_existing(self, project):
        skill_trust.grant_project_trust(project)
        assert skill_trust.list_trusted_projects()[0]["exists"] is True

    def test_listing_is_empty_without_a_store(self):
        assert skill_trust.list_trusted_projects() == []


class TestFailsClosed:
    def _grant_then_corrupt(self, project, text):
        skill_trust.grant_project_trust(project)
        skill_trust.store_path().write_text(text, encoding="utf-8")
        skill_trust.reset_cache_for_tests()

    def test_malformed_json_grants_nothing(self, project):
        self._grant_then_corrupt(project, "{not json")
        assert skill_trust.is_project_trusted(project) is False

    def test_a_json_array_grants_nothing(self, project):
        self._grant_then_corrupt(project, "[]")
        assert skill_trust.trusted_keys() == frozenset()

    def test_an_unknown_schema_version_grants_nothing(self, project):
        # A store written by a newer build is not guessed at.
        self._grant_then_corrupt(project, json.dumps({"version": 99, "granted": []}))
        assert skill_trust.is_project_trusted(project) is False

    def test_a_non_array_granted_field_grants_nothing(self, project):
        self._grant_then_corrupt(project, json.dumps({"version": 1, "granted": {}}))
        assert skill_trust.trusted_keys() == frozenset()

    def test_non_dict_and_relative_entries_are_dropped(self, project):
        key = os.path.realpath(project)
        skill_trust.store_path().parent.mkdir(parents=True, exist_ok=True)
        skill_trust.store_path().write_text(
            json.dumps(
                {
                    "version": 1,
                    "granted": ["not-a-dict", {"path": "rel/ative"}, {"path": key}],
                }
            ),
            encoding="utf-8",
        )
        skill_trust.reset_cache_for_tests()
        # The good entry survives; the junk does not become a grant.
        assert skill_trust.trusted_keys() == frozenset({key})

    def test_over_cap_entries_are_truncated_not_denied(self, project):
        key = os.path.realpath(project)
        over = skill_trust._MAX_GRANT_ENTRIES + 10
        granted = [{"path": key}] + [{"path": f"/synthetic/{i}"} for i in range(over)]
        skill_trust.store_path().parent.mkdir(parents=True, exist_ok=True)
        skill_trust.store_path().write_text(
            json.dumps({"version": 1, "granted": granted}), encoding="utf-8"
        )
        skill_trust.reset_cache_for_tests()
        keys = skill_trust.trusted_keys()
        # Append-ordered, so the operator's real grants sit at the front and a
        # pathological store costs bounded work rather than denying everything.
        assert key in keys
        assert len(keys) <= skill_trust._MAX_GRANT_ENTRIES


class TestHardOffSwitch:
    def _write_config(self, enabled):
        home = skill_trust.store_path().parent.parent
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.json").write_text(
            json.dumps({"skills": {"project_skills_enabled": enabled}}), encoding="utf-8"
        )
        skill_trust.reset_cache_for_tests()

    def test_disabled_overrides_a_live_grant(self, project):
        skill_trust.grant_project_trust(project)
        assert skill_trust.is_project_trusted(project) is True
        self._write_config(False)
        # Enforced in the SAME chokepoint as the grants, so no stale grant can
        # outlive the operator turning the feature off.
        assert skill_trust.trusted_keys() == frozenset()
        assert skill_trust.is_project_trusted(project) is False

    def test_enabled_still_requires_a_grant(self, project):
        self._write_config(True)
        assert skill_trust.is_project_trusted(project) is False


class TestCaching:
    def test_a_grant_written_behind_the_cache_is_picked_up(self, project):
        assert skill_trust.trusted_keys() == frozenset()
        skill_trust.grant_project_trust(project)
        # The writer drops the memo, so no stale empty set is served.
        assert os.path.realpath(project) in skill_trust.trusted_keys()

    def test_repeated_reads_agree(self, project):
        skill_trust.grant_project_trust(project)
        assert skill_trust.trusted_keys() == skill_trust.trusted_keys()

    def test_a_missing_store_clears_any_memo(self, project):
        skill_trust.grant_project_trust(project)
        assert skill_trust.trusted_keys()
        skill_trust.store_path().unlink()
        skill_trust.reset_cache_for_tests()
        assert skill_trust.trusted_keys() == frozenset()


class TestTrustDirIntegrity:
    def test_a_symlinked_trust_dir_is_replaced_before_writing(self, project, tmp_path):
        """A pre-planted ``trust`` symlink must not redirect the grant write.

        Before ``trust`` was keystone-gated an agent could plant a link there,
        pointing the store somewhere it can author — letting it forge a grant
        for a directory the operator never approved.
        """
        home = skill_trust.store_path().parent.parent
        home.mkdir(parents=True, exist_ok=True)
        elsewhere = tmp_path / "attacker"
        elsewhere.mkdir()
        link = home / "trust"
        try:
            link.symlink_to(elsewhere)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")

        skill_trust.grant_project_trust(project)

        real_trust = home / "trust"
        assert not real_trust.is_symlink()
        assert real_trust.is_dir()
        # The write landed inside the real directory, not the link target.
        assert skill_trust.store_path().is_file()
        assert not (elsewhere / "project-skills.json").exists()
