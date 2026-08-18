"""Tests for crew-variable config scopes and layered resolution.

In-memory only: every case builds config objects or raw dicts directly, so
nothing touches the data home or the filesystem.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import patch

from kiro_crew.config import loader as loader_mod
from kiro_crew.config.loader import (
    SCOPE_CREW,
    SCOPE_GLOBAL,
    SCOPE_SESSION,
    SCOPE_WORKSPACE,
    KiroCrewAgentConfig,
    KiroCrewConfig,
    WorkspaceConfig,
    _migrate_workspaces,
    coerce_variables,
    resolve_agent_bindings,
    resolve_variables,
)


def _config(
    *,
    global_vars: dict[str, str] | None = None,
    workspace_vars: dict[str, str] | None = None,
    crew_vars: dict[str, str] | None = None,
    workspace_name: str = "ops",
    crew_workspace: str | None = None,
) -> KiroCrewConfig:
    cfg = KiroCrewConfig()
    cfg.variables = dict(global_vars or {})
    cfg.workspaces = {
        "default": WorkspaceConfig(dir="workspace"),
        workspace_name: WorkspaceConfig(
            dir=f"w-{workspace_name}", variables=dict(workspace_vars or {})
        ),
    }
    cfg.default_workspace = "default"
    cfg.agents = {
        "crew1": KiroCrewAgentConfig(
            kiro_agent="kirocrew",
            workspace=workspace_name if crew_workspace is None else crew_workspace,
            variables=dict(crew_vars or {}),
        )
    }
    cfg.default_agent = "crew1"
    return cfg


class TestLayerPrecedence:
    def test_global_only(self):
        r = resolve_variables(_config(global_vars={"a": "g"}))
        assert r.values == {"a": "g"}
        assert r.winning_scope["a"] == SCOPE_GLOBAL

    def test_workspace_overrides_global(self):
        r = resolve_variables(_config(global_vars={"a": "g"}, workspace_vars={"a": "w"}))
        assert r.values["a"] == "w"
        assert r.winning_scope["a"] == SCOPE_WORKSPACE
        assert r.shadowed["a"] == [SCOPE_GLOBAL]

    def test_crew_overrides_workspace(self):
        r = resolve_variables(
            _config(global_vars={"a": "g"}, workspace_vars={"a": "w"}, crew_vars={"a": "c"})
        )
        assert r.values["a"] == "c"
        assert r.winning_scope["a"] == SCOPE_CREW
        assert r.shadowed["a"] == [SCOPE_GLOBAL, SCOPE_WORKSPACE]

    def test_session_overrides_crew(self):
        cfg = _config(global_vars={"a": "g"}, crew_vars={"a": "c"})
        r = resolve_variables(cfg, session_overrides={"a": "s"})
        assert r.values["a"] == "s"
        assert r.winning_scope["a"] == SCOPE_SESSION

    def test_disjoint_keys_all_survive(self):
        r = resolve_variables(
            _config(global_vars={"g": "1"}, workspace_vars={"w": "2"}, crew_vars={"c": "3"})
        )
        assert r.values == {"g": "1", "w": "2", "c": "3"}
        assert r.shadowed == {}

    def test_empty_string_at_narrow_scope_beats_non_empty_global(self):
        # Presence, not truthiness: a blank at a narrow scope is deliberate.
        r = resolve_variables(_config(global_vars={"a": "g"}, crew_vars={"a": ""}))
        assert r.values["a"] == ""
        assert r.winning_scope["a"] == SCOPE_CREW

    def test_no_variables_anywhere_resolves_empty(self):
        r = resolve_variables(_config())
        assert r.values == {}


class TestScopeSelection:
    def test_reports_resolved_crew_and_workspace(self):
        r = resolve_variables(_config(workspace_name="ops"))
        assert r.agent_name == "crew1"
        assert r.workspace_name == "ops"

    def test_unknown_agent_name_takes_the_default_crew(self):
        cfg = _config(crew_vars={"a": "c"})
        r = resolve_variables(cfg, agent_name="does-not-exist")
        assert r.agent_name == "crew1"
        assert r.values["a"] == "c"

    def test_explicit_agent_name_selects_that_crew(self):
        cfg = _config(crew_vars={"a": "one"})
        cfg.agents["crew2"] = KiroCrewAgentConfig(workspace="ops", variables={"a": "two"})
        r = resolve_variables(cfg, agent_name="crew2")
        assert r.agent_name == "crew2"
        assert r.values["a"] == "two"

    def test_crew_naming_a_missing_workspace_falls_back(self):
        cfg = _config(workspace_vars={"a": "w"}, crew_workspace="gone")
        cfg.workspaces["default"] = WorkspaceConfig(dir="workspace", variables={"a": "fallback"})
        r = resolve_variables(cfg)
        assert r.workspace_name == "default"
        assert r.values["a"] == "fallback"

    def test_no_agents_configured_yields_global_only(self):
        cfg = KiroCrewConfig()
        cfg.variables = {"a": "g"}
        cfg.agents = {}
        cfg.workspaces = {}
        r = resolve_variables(cfg)
        assert r.values == {"a": "g"}
        assert r.agent_name == ""

    def test_workspace_agrees_with_resolve_agent_bindings(self):
        """Guard against the two resolvers drifting apart on scope selection."""
        cases = [
            _config(workspace_name="ops"),
            _config(workspace_name="ops", crew_workspace="gone"),
        ]
        for cfg in cases:
            for name in (None, "crew1", "unknown-agent"):
                bindings = resolve_agent_bindings(cfg, agent_name=name)
                resolution = resolve_variables(cfg, agent_name=name)
                expected_dir = cfg.workspaces[resolution.workspace_name].dir
                assert str(bindings.workspace_dir) == expected_dir


class TestSessionLayerValidation:
    def test_session_override_is_validated_like_any_scope(self):
        cfg = _config(global_vars={"a": "g"})
        r = resolve_variables(cfg, session_overrides={"a": "ok", "bad-name": "x"})
        assert r.values["a"] == "ok"
        assert "bad-name" not in r.values

    def test_session_override_cannot_take_a_reserved_name(self):
        r = resolve_variables(_config(), session_overrides={"MAX_SUBAGENTS": "9"})
        assert "MAX_SUBAGENTS" not in r.values


class TestCoerceVariables:
    def test_drops_only_the_offending_pair(self, caplog):
        with caplog.at_level(logging.WARNING):
            out = coerce_variables({"good": "v", "1bad": "x", "also_good": "w"}, "variables")
        assert out == {"good": "v", "also_good": "w"}
        assert "1bad" in caplog.text

    def test_warning_names_the_scope(self, caplog):
        with caplog.at_level(logging.WARNING):
            coerce_variables({"a-b": "x"}, "agents.oncall")
        assert "agents.oncall" in caplog.text

    def test_non_object_is_ignored(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert coerce_variables(["not", "an", "object"], "variables") == {}
        assert "expected an object" in caplog.text

    def test_missing_section_is_silent(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert coerce_variables(None, "variables") == {}
        assert caplog.text == ""

    def test_coerces_scalars(self):
        assert coerce_variables({"a": 3, "b": True}, "variables") == {"a": "3", "b": "true"}


class TestWorkspaceMigrationCarriesVariables:
    """A key the migration does not read is dropped on the next save, because
    to_dict serializes the dataclass rather than the raw config entry."""

    def test_structured_entry_keeps_variables(self):
        out = _migrate_workspaces({"ops": {"dir": "w-ops", "variables": {"a": "1"}}})
        assert out["ops"].variables == {"a": "1"}

    def test_survives_a_to_dict_then_migrate_round_trip(self):
        cfg = _config(workspace_vars={"a": "1"}, workspace_name="ops")
        serialized = cfg.to_dict()["workspaces"]
        assert serialized["ops"]["variables"] == {"a": "1"}
        again = _migrate_workspaces(serialized)
        assert again["ops"].variables == {"a": "1"}
        assert again["ops"].dir == "w-ops"

    def test_flat_string_entry_still_migrates(self):
        out = _migrate_workspaces({"legacy": "some-dir"})
        assert out["legacy"].dir == "some-dir"
        assert out["legacy"].variables == {}

    def test_invalid_pair_in_a_workspace_is_dropped_not_fatal(self):
        out = _migrate_workspaces({"ops": {"dir": "d", "variables": {"ok": "1", "": "2"}}})
        assert out["ops"].variables == {"ok": "1"}


class TestCrewVariablesRoundTrip:
    def test_crew_variables_serialize(self):
        cfg = _config(crew_vars={"a": "1"})
        assert cfg.to_dict()["agents"]["crew1"]["variables"] == {"a": "1"}

    def test_global_variables_serialize(self):
        cfg = _config(global_vars={"a": "1"})
        assert cfg.to_dict()["variables"] == {"a": "1"}


class TestAWholeConfigSaveIsVariablesNeutral:
    """``save()`` serializes the MERGED config, so a whole-config write from any
    unrelated settings panel had two wrong options and both shipped as findings:

    * subtract the overlay-owned leaves -> a base variable duplicating an overlay
      value is DELETED, and a workspace entry whose every leaf matched went with it;
    * leave them -> the MERGED value is written, so the overlay's value is COPIED
      into config.json and a shadowed base value is OVERWRITTEN.

    Neither is recoverable from the merged view: the merge already discarded the
    shadowed base value. So the write is made neutral — config.json's own maps are
    restored verbatim, and the only writer of variables is the locked patch endpoint.
    """

    def test_a_shadowed_base_value_is_neither_deleted_nor_overwritten(self):
        # config.json says "base"; the overlay shadows it with "over", so the merged
        # view carries "over" and the base value is not present in it at all.
        base = {"variables": {"A": "base"}}
        merged = {"variables": {"A": "over"}, "agent": {"model": "auto"}}

        out = loader_mod._preserve_base_variables(merged, base)

        assert out["variables"] == {
            "A": "base"
        }, "the overlay's value was written into the base document"

    def test_a_duplicated_value_is_kept_rather_than_subtracted(self):
        base = {"variables": {"A": "same"}}
        merged = {"variables": {}}  # as _subtract_overlay would have left it

        out = loader_mod._preserve_base_variables(merged, base)

        assert out["variables"] == {"A": "same"}, "an unrelated save deleted a variable"

    def test_a_map_absent_from_the_base_stays_absent(self):
        """Neutral in both directions: it must not CREATE a variables map either,
        or an overlay-only definition would be materialized into the base file."""
        base: dict = {}
        merged = {"variables": {"FROM_OVERLAY": "x"}}

        out = loader_mod._preserve_base_variables(merged, base)

        assert "variables" not in out

    def test_workspace_and_crew_scopes_are_covered(self):
        base = {
            "workspaces": {"ops": {"dir": "d", "variables": {"A": "base-ws"}}},
            "agents": {"oncall": {"variables": {"A": "base-crew"}}},
        }
        merged = {
            "workspaces": {"ops": {"dir": "d", "variables": {"A": "over-ws"}}},
            "agents": {"oncall": {"variables": {"A": "over-crew"}}},
        }

        out = loader_mod._preserve_base_variables(merged, base)

        assert out["workspaces"]["ops"]["variables"] == {"A": "base-ws"}
        assert out["agents"]["oncall"]["variables"] == {"A": "base-crew"}
        # Non-variables fields are untouched by this helper.
        assert out["workspaces"]["ops"]["dir"] == "d"

    def test_a_workspace_absent_from_the_base_loses_only_its_variables(self):
        """An overlay-declared workspace still serializes, but without variables the
        base never held — the same reason the PUT handler omits its ``dir``."""
        base = {"workspaces": {}}
        merged = {"workspaces": {"overlaid": {"dir": "d", "variables": {"A": "x"}}}}

        out = loader_mod._preserve_base_variables(merged, base)

        assert "variables" not in out["workspaces"]["overlaid"]
        assert out["workspaces"]["overlaid"]["dir"] == "d"

    def test_the_restore_and_the_write_are_one_locked_transaction(self):
        """Reading the base separately and writing afterwards left a window: a
        variables PUT committing in between was acknowledged to its caller and then
        discarded by this write's stale snapshot.

        Asserted on the WIRING rather than by racing threads: the restore has to run
        inside the mutate callback that ``update_config_locked`` invokes with the
        document it just read under the lock, not against a separately-read copy.
        """
        source = inspect.getsource(loader_mod.KiroCrewConfig.save)
        assert (
            "update_config_locked(config_path(), mutate=_mutate" in source
        ), "save() no longer writes through the locked helper"
        assert (
            "_preserve_base_variables(d, current)" in source
        ), "the restore must read the document the LOCK handed it, not its own read"
        # The separate unlocked read must be gone.
        assert "json.loads(config_path().read_text" not in source
        assert "write_config_atomically(config_path()" not in source

    def test_the_restore_uses_the_locked_document_not_a_prior_read(self):
        """Behavioural half: the callback's argument is what gets preserved, so a
        value that appeared after save() serialized still survives."""
        captured: dict = {}

        def _fake_locked(_path, *, mutate, **_kw):
            # The document as the lock found it — holding a variable save() never saw.
            captured["result"] = mutate({"variables": {"LATE": "arrived"}})
            return captured["result"]

        cfg = KiroCrewConfig()
        cfg.variables = {}
        with (
            patch.object(loader_mod, "update_config_locked", _fake_locked),
            patch.object(loader_mod, "_invalidate_config_cache", lambda: None),
        ):
            cfg.save()

        assert captured["result"]["variables"] == {
            "LATE": "arrived"
        }, "a variable that landed after serialization was discarded"

        """The neutrality is scoped to variables; overlay-owned non-variable leaves
        are still stripped so they do not leak into the base file."""
        merged = {"agent": {"model": "auto"}, "variables": {"A": "x"}}
        overlay = {"agent": {"model": "auto"}}

        out = loader_mod._subtract_overlay(merged, overlay)

        assert "agent" not in out
