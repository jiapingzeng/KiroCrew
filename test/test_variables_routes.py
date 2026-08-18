"""Tests for the /api/variables dashboard routes.

Hermetic: every case redirects ``config_path`` at a ``tmp_path`` file and hands the
handler a config object directly, so nothing reads or writes the real data home
and the loader's fingerprint cache never participates.
"""

from __future__ import annotations

import inspect
import json
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from kiro_crew.config.loader import KiroCrewAgentConfig, KiroCrewConfig, WorkspaceConfig
from kiro_crew.dashboard.handlers import variables as vh

_NOT_POSIX = os.name == "nt"

# Every test here awaits a handler directly.
pytestmark = pytest.mark.asyncio


def _request(method: str, body: Any = ...):
    """A mocked request. ``body=None`` models a malformed payload, which is what
    the handler's ``except Exception -> 400`` branch is written for."""
    req = make_mocked_request(method, "/api/variables")
    if body is None:
        req.json = AsyncMock(side_effect=ValueError("not json"))  # type: ignore[method-assign]
    elif body is not ...:
        req.json = AsyncMock(return_value=body)  # type: ignore[method-assign]
    return req


def _config() -> KiroCrewConfig:
    cfg = KiroCrewConfig()
    cfg.variables = {"baseUrl": "https://global.test", "orgName": "Acme"}
    cfg.workspaces = {
        "default": WorkspaceConfig(dir="workspace"),
        "ops": WorkspaceConfig(dir="workspace-ops", variables={"baseUrl": "https://ops.test"}),
    }
    cfg.default_workspace = "default"
    cfg.agents = {
        "crew1": KiroCrewAgentConfig(
            kiro_agent="kirocrew", workspace="ops", variables={"queue": "oncall"}
        )
    }
    cfg.default_agent = "crew1"
    return cfg


@pytest.fixture()
def wired(monkeypatch, tmp_path: Path):
    """Redirect the handler at a temp config file and a fixed config object.

    The base file starts minimal on purpose. The GET view now reports what
    ``config.json`` ITSELF holds rather than the merged map, so a test that cares
    about reported pairs seeds the file explicitly — the config object alone no
    longer implies an editable pair, and that distinction is the point of the read
    side.
    """
    cfg = _config()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspaces": {"ops": {"dir": "workspace-ops"}}}), encoding="utf-8")
    monkeypatch.setattr(vh, "config_path", lambda: path)
    monkeypatch.setattr(vh.KiroCrewConfig, "load", classmethod(lambda cls: cfg))
    return cfg, path


async def test_get_reports_every_scope(wired):
    cfg, path = wired
    # ``global`` and ``workspaces`` report what config.json itself holds, since that
    # is what a PUT here can change; seed the file rather than relying on the
    # config object, which also carries overlay-supplied values.
    path.write_text(
        json.dumps(
            {
                "variables": {"baseUrl": "https://global.test", "orgName": "Acme"},
                "workspaces": {
                    "ops": {"dir": "workspace-ops", "variables": {"baseUrl": "https://ops.test"}}
                },
            }
        ),
        encoding="utf-8",
    )
    resp = await vh.api_variables(_request("GET"))
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["global"] == {"baseUrl": "https://global.test", "orgName": "Acme"}
    assert payload["workspaces"]["ops"] == {"baseUrl": "https://ops.test"}
    assert payload["crews"]["crew1"] == {"queue": "oncall"}


async def test_get_reports_resolution_and_provenance(wired):
    resp = await vh.api_variables(_request("GET"))
    payload = json.loads(resp.text)
    # crew1 binds workspace ops, so the workspace value wins over global.
    assert payload["effective"]["baseUrl"] == "https://ops.test"
    assert payload["winning_scope"]["baseUrl"] == "workspace"
    assert payload["shadowed"]["baseUrl"] == ["global"]
    assert payload["effective"]["queue"] == "oncall"
    assert payload["winning_scope"]["queue"] == "crew"
    assert payload["active_workspace"] == "ops"
    assert payload["active_agent"] == "crew1"


async def test_put_global_persists(wired):
    _, path = wired
    resp = await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1", "b": ""}}))
    assert resp.status == 200
    assert json.loads(resp.text)["ok"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"a": "1", "b": ""}


async def test_a_set_leaves_unnamed_keys_alone(wired):
    """The property the whole-scope form could not offer, and the reason it went.

    Under the replace contract, a second write that did not re-list ``b`` deleted it
    — which is how one tab's save discarded another tab's edit, and how a base value
    shadowed by config.local.json disappeared without anyone touching it. A patch
    touches only what it names.
    """
    _, path = wired
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1", "b": "2"}}))
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "9"}}))
    assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"a": "9", "b": "2"}


async def test_a_workspace_set_leaves_unnamed_keys_alone(wired):
    """The workspace branch has its own copy of the patch logic, so it needs its
    own proof. A mutation that made only this branch replace the whole scope passed
    the suite until this test existed — the global-scope test above says nothing
    about it.
    """
    _, path = wired
    await vh.api_variables(
        _request("PUT", {"scope": "workspace", "workspace": "ops", "set": {"a": "1", "b": "2"}})
    )
    await vh.api_variables(
        _request("PUT", {"scope": "workspace", "workspace": "ops", "set": {"a": "9"}})
    )
    entry = json.loads(path.read_text(encoding="utf-8"))["workspaces"]["ops"]
    assert entry["variables"] == {"a": "9", "b": "2"}
    # The directory the operator set must survive a variables patch untouched.
    assert entry["dir"] == "workspace-ops"


async def test_a_workspace_delete_removes_only_the_named_key(wired):
    _, path = wired
    await vh.api_variables(
        _request("PUT", {"scope": "workspace", "workspace": "ops", "set": {"a": "1", "b": "2"}})
    )
    await vh.api_variables(
        _request("PUT", {"scope": "workspace", "workspace": "ops", "delete": ["a"]})
    )
    entry = json.loads(path.read_text(encoding="utf-8"))["workspaces"]["ops"]
    assert entry["variables"] == {"b": "2"}


async def test_delete_is_an_explicit_verb(wired):
    """Removal is named rather than implied by absence, which is what keeps the
    empty string unambiguous: it stays a legal value that overrides a broader
    scope, instead of colliding with 'unset'."""
    _, path = wired
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1", "b": ""}}))
    await vh.api_variables(_request("PUT", {"scope": "global", "delete": ["b"]}))
    assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"a": "1"}


async def test_an_empty_string_survives_a_later_patch(wired):
    """An empty value is not absence: a patch that does not name it must keep it."""
    _, path = wired
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"blank": ""}}))
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"other": "x"}}))
    stored = json.loads(path.read_text(encoding="utf-8"))["variables"]
    assert stored == {"blank": "", "other": "x"}


async def test_set_and_delete_apply_together(wired):
    _, path = wired
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1", "b": "2"}}))
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"c": "3"}, "delete": ["a"]}))
    assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"b": "2", "c": "3"}


async def test_setting_and_deleting_one_key_is_refused(wired):
    """Ambiguous by construction, so it is refused rather than resolved by ordering
    — whichever the server applied second would be silently arbitrary."""
    resp = await vh.api_variables(
        _request("PUT", {"scope": "global", "set": {"a": "1"}, "delete": ["a"]})
    )
    assert resp.status == 400
    assert json.loads(resp.text)["code"] == "variables_conflicting_change"


async def test_a_body_naming_neither_set_nor_delete_is_refused(wired):
    resp = await vh.api_variables(_request("PUT", {"scope": "global"}))
    assert resp.status == 400
    assert json.loads(resp.text)["code"] == "variables_invalid_values"


async def test_deleting_a_key_that_is_not_there_is_not_an_error(wired):
    """Idempotent: two tabs can both delete the same row without the second seeing
    a failure for work that is already done."""
    _, path = wired
    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1"}}))
    resp = await vh.api_variables(_request("PUT", {"scope": "global", "delete": ["ghost"]}))
    assert resp.status == 200
    assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"a": "1"}


async def test_put_workspace_persists_under_that_workspace(wired):
    _, path = wired
    resp = await vh.api_variables(
        _request("PUT", {"scope": "workspace", "workspace": "ops", "set": {"queue": "tier2"}})
    )
    assert resp.status == 200
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["workspaces"]["ops"]["variables"] == {"queue": "tier2"}
    assert data["workspaces"]["ops"]["dir"] == "workspace-ops"


async def test_put_widens_a_legacy_flat_workspace_entry(monkeypatch, tmp_path: Path):
    """The flat form maps a name straight to its directory; assigning a key onto
    that string would raise, and replacing it would lose the directory."""
    cfg = _config()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspaces": {"ops": "legacy-dir"}}), encoding="utf-8")
    monkeypatch.setattr(vh, "config_path", lambda: path)
    monkeypatch.setattr(vh.KiroCrewConfig, "load", classmethod(lambda cls: cfg))

    resp = await vh.api_variables(
        _request("PUT", {"scope": "workspace", "workspace": "ops", "set": {"a": "1"}})
    )
    assert resp.status == 200
    entry = json.loads(path.read_text(encoding="utf-8"))["workspaces"]["ops"]
    assert entry == {"dir": "legacy-dir", "variables": {"a": "1"}}


async def test_put_preserves_unrelated_config_keys(monkeypatch, tmp_path: Path):
    cfg = _config()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"agent": {"model": "auto"}, "workspaces": {"ops": {"dir": "d"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vh, "config_path", lambda: path)
    monkeypatch.setattr(vh.KiroCrewConfig, "load", classmethod(lambda cls: cfg))

    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1"}}))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["agent"] == {"model": "auto"}
    assert data["workspaces"]["ops"] == {"dir": "d"}


@pytest.mark.skipif(_NOT_POSIX, reason="POSIX file modes")
async def test_put_preserves_existing_file_permissions(monkeypatch, tmp_path: Path):
    """Widening an operator's tightened config.json on an unrelated save would be
    a silent downgrade."""
    cfg = _config()
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(vh, "config_path", lambda: path)
    monkeypatch.setattr(vh.KiroCrewConfig, "load", classmethod(lambda cls: cfg))

    await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1"}}))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


class TestRejections:
    """Every non-2xx body carries a machine-readable ``code`` — the dashboard
    renders server prose verbatim, so the identifier is what a client switches on."""

    async def test_malformed_json(self, wired):
        resp = await vh.api_variables(_request("PUT", None))
        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_invalid_json"

    async def test_non_object_body(self, wired):
        resp = await vh.api_variables(_request("PUT", ["not", "an", "object"]))
        assert json.loads(resp.text)["code"] == "variables_invalid_body"

    async def test_unknown_scope(self, wired):
        resp = await vh.api_variables(_request("PUT", {"scope": "crew", "set": {}}))
        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_invalid_scope"

    async def test_missing_values(self, wired):
        resp = await vh.api_variables(_request("PUT", {"scope": "global"}))
        assert json.loads(resp.text)["code"] == "variables_invalid_values"

    async def test_unknown_workspace(self, wired):
        resp = await vh.api_variables(
            _request("PUT", {"scope": "workspace", "workspace": "nope", "set": {}})
        )
        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_unknown_workspace"

    async def test_invalid_name_names_the_key(self, wired):
        resp = await vh.api_variables(_request("PUT", {"scope": "global", "set": {"1bad": "x"}}))
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert payload["code"] == "variables_invalid_pair"
        assert payload["key"] == "1bad"

    async def test_reserved_name_is_refused(self, wired):
        resp = await vh.api_variables(
            _request("PUT", {"scope": "global", "set": {"MAX_SUBAGENTS": "9"}})
        )
        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_invalid_pair"

    async def test_control_character_is_refused(self, wired):
        resp = await vh.api_variables(
            _request("PUT", {"scope": "global", "set": {"a": "one\ntwo"}})
        )
        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_invalid_pair"

    async def test_a_rejected_write_persists_nothing(self, wired):
        _, path = wired
        before = path.read_text(encoding="utf-8")
        await vh.api_variables(_request("PUT", {"scope": "global", "set": {"1bad": "x"}}))
        assert path.read_text(encoding="utf-8") == before


async def test_payload_satisfies_the_frontend_interface(wired):
    """The panel reads a typed `VariablesView`; a field renamed on one side and
    not the other type-checks fine and fails only in a browser.

    The parser is brace-aware and strips the optional marker. Both matter: the
    first version stopped at the first ``}``, which a nested object type closes
    early, and it kept the ``?`` from an optional field, so ``overlay_owned?`` was
    compared against a payload key spelled ``overlay_owned`` and reported missing.
    Optional fields are still required to be PRESENT here — the handler is their
    only producer, so absence would mean the field was renamed or dropped.
    """
    client = (
        Path(__file__).resolve().parents[1] / "website" / "src" / "api" / "client.ts"
    ).read_text(encoding="utf-8")
    start = client.index("export interface VariablesView")
    body_start = client.index("{", start)
    depth = 0
    declared: set[str] = set()
    for line in client[body_start:].splitlines():
        stripped = line.strip()
        if stripped.startswith(("/*", "*", "//")):
            continue
        # Collect only depth-1 members: a nested object's own fields belong to that
        # inner type, not to VariablesView.
        if depth == 1 and ":" in stripped:
            name = stripped.split(":")[0].strip().rstrip("?")
            if name:
                declared.add(name)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            break
    assert declared, "could not parse VariablesView — the interface moved or was renamed"

    resp = await vh.api_variables(_request("GET"))
    payload = json.loads(resp.text)
    missing = declared - set(payload)
    assert not missing, f"handler payload is missing fields the panel reads: {sorted(missing)}"


class TestTheWriteIsLockedAndOffLoop:
    """A whole-file replacement must not race another config writer, and must not
    block the gateway's event loop."""

    async def test_the_mutation_goes_through_the_locked_helper(self, wired):
        _, path = wired
        seen: dict[str, object] = {}

        def _fake(target, *, mutate, **kwargs):
            seen["path"] = target
            data = {"agent": {"model": "auto"}}
            seen["result"] = mutate(data)
            return data

        with patch.object(vh, "update_config_locked", _fake) as _:
            resp = await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1"}}))
        assert resp.status == 200
        assert seen["path"] == path
        # The callback applied the scope write to the dict the lock handed it,
        # rather than to a copy read before the lock was taken.
        assert seen["result"]["variables"] == {"a": "1"}
        assert seen["result"]["agent"] == {"model": "auto"}

    async def test_the_locked_helper_runs_off_the_event_loop(self, wired):
        """It reads, locks and fsyncs; on the loop that freezes every task."""
        source = inspect.getsource(vh)
        # Through run_config_write, which holds BOTH config locks and runs the
        # blocking write in a worker thread. The sidecar flock alone only serializes
        # against other update_config_locked callers — the dashboard's legacy
        # handlers take the loop-side asyncio lock instead, so holding one of the two
        # let a variables PUT and a settings PUT interleave and revert each other.
        assert "run_config_write(update_config_locked" in source
        assert (
            "asyncio.to_thread(update_config_locked" not in source
        ), "the bare to_thread form holds only the flock"
        # The unlocked WRITE forms must be gone. Match the CALL form, not the bare
        # name: prose explaining why the write goes through the locked helper names
        # these functions, and a guard that cannot tell a mention from a call fails
        # on a comment while a real call stays invisible.
        assert "write_config_atomically(" not in source
        # Reads of the overlay DO exist (both refusals need it), so the rule is that
        # each is dispatched to a thread, not that none exists. Scoped to the async
        # handler: it is the only place running on the loop, and a module-wide scan
        # flags a nested call that is already off-loop.
        handler = inspect.getsource(vh.api_variables)
        assert "asyncio.to_thread(_read_overlay)" in handler
        assert "asyncio.to_thread(_view_inputs)" in handler
        for on_loop in ("= _view_inputs(", "= _read_overlay("):
            assert on_loop not in handler, f"{on_loop} runs on the event loop"

    async def test_a_corrupt_config_fails_closed_without_writing(self, wired):
        def _raise(*_args, **_kwargs):
            raise vh.ConfigReadError("bad json")

        with patch.object(vh, "update_config_locked", _raise):
            resp = await vh.api_variables(_request("PUT", {"scope": "global", "set": {"a": "1"}}))
        assert resp.status == 500
        assert json.loads(resp.text)["code"] == "config_corrupt"

    async def test_a_malformed_workspace_entry_is_refused_not_a_500(self, wired):
        """A hand-edited entry that is neither a mapping nor a directory string.

        The loader is permissive enough to expose such a workspace in the merged
        view, so a PUT reaches the mutate callback, where ``entry.get("variables")``
        raised AttributeError and answered 500. This guard existed before and was
        deleted along with the resurrection discriminator that shared its ``elif``
        chain — the branch went wholesale when only the discriminator should have.
        """
        _, path = wired
        for bad in (42, ["a"], True):
            path.write_text(json.dumps({"workspaces": {"ops": bad}}), encoding="utf-8")
            resp = await vh.api_variables(
                _request(
                    "PUT",
                    {"scope": "workspace", "workspace": "ops", "set": {"q": "1"}},
                )
            )
            assert resp.status == 400, f"a {type(bad).__name__} entry produced {resp.status}"
            assert json.loads(resp.text)["code"] == "variables_malformed_workspace"
            # The operator's malformed value is left exactly as written rather than
            # being replaced by a repair this endpoint has no mandate to make.
            assert json.loads(path.read_text(encoding="utf-8"))["workspaces"]["ops"] == bad

    async def test_the_legacy_directory_string_is_still_accepted(self, wired):
        """The refusal must not catch the legacy flat form, which is valid config."""
        _, path = wired
        path.write_text(json.dumps({"workspaces": {"ops": "workspace-ops"}}), encoding="utf-8")
        resp = await vh.api_variables(
            _request(
                "PUT",
                {"scope": "workspace", "workspace": "ops", "set": {"q": "1"}},
            )
        )
        assert resp.status == 200
        entry = json.loads(path.read_text(encoding="utf-8"))["workspaces"]["ops"]
        assert entry["dir"] == "workspace-ops"
        assert entry["variables"] == {"q": "1"}

    async def test_an_absent_base_entry_never_materializes(self, wired, monkeypatch, tmp_path):
        """No entry is created for a workspace config.json does not hold — even when
        the overlay declares it.

        This replaces a test that asserted materialization (without a ``dir``).
        Creating the entry is what left a resurrection window: whatever pre-lock
        signal decided "this is a first write, not a deletion" was stale by the time
        the lock was held, three different signals over three rounds. With no
        materialization there is no window to close.
        """
        _, path = wired
        path.write_text(json.dumps({}), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text(
            json.dumps({"workspaces": {"ops": {"dir": "workspace-ops"}}}), encoding="utf-8"
        )
        monkeypatch.setattr(vh, "config_local_path", lambda: local)
        seen: dict[str, object] = {}

        def _fake(_target, *, mutate, **_kwargs):
            data: dict = {}
            seen["result"] = mutate(data)
            return data

        with patch.object(vh, "update_config_locked", _fake):
            resp = await vh.api_variables(
                _request(
                    "PUT",
                    {"scope": "workspace", "workspace": "ops", "set": {"q": "1"}},
                )
            )
        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_unknown_workspace"
        # The callback raised, so no write was produced at all.
        assert "result" not in seen

    async def test_an_absent_entry_with_no_overlay_declaration_is_refused(
        self, wired, monkeypatch, tmp_path
    ):
        """The resurrection window, closed.

        The previous discriminator read the base document's prior presence just
        before the lock, so a deletion landing between the merged pre-check and that
        read looked like a first write and the entry was recreated. Keyed on the
        overlay there is no window: a base-declared workspace always HAS an entry, so
        an absent entry plus no overlay declaration can only mean deleted.
        """
        _, path = wired
        path.write_text(json.dumps({}), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        def _fake(_target, *, mutate, **_kwargs):
            return mutate({})

        with patch.object(vh, "update_config_locked", _fake):
            resp = await vh.api_variables(
                _request(
                    "PUT",
                    {"scope": "workspace", "workspace": "ops", "set": {"q": "1"}},
                )
            )

        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_unknown_workspace"

    async def test_an_existing_workspace_keeps_its_directory(self, wired):
        """The flip side: a workspace config.json DOES list must keep the directory
        the operator set — a variables patch must not disturb it."""
        _, path = wired
        resp = await vh.api_variables(
            _request(
                "PUT",
                {"scope": "workspace", "workspace": "ops", "set": {"q": "1"}},
            )
        )
        assert resp.status == 200
        entry = json.loads(path.read_text(encoding="utf-8"))["workspaces"]["ops"]
        assert entry["dir"] == "workspace-ops"
        assert entry["variables"] == {"q": "1"}


class TestAConcurrentWorkspaceDeletion:
    """The ``workspace not in cfg.workspaces`` pre-check reads UNLOCKED, so its
    answer can go stale before the locked write runs.

    Asserting on an already-absent workspace would only re-test the pre-check. The
    workspace has to vanish in the window BETWEEN the pre-check and the mutate
    callback — the interleaving where the old code rebuilt the entry from a
    ``fallback_dir`` captured before the lock, resurrecting a workspace the
    operator had just deleted.
    """

    async def test_a_vanished_workspace_is_refused_not_resurrected(self, wired):
        _, path = wired
        path.write_text(
            json.dumps({"workspaces": {"ops": {"dir": "workspace-ops"}}}),
            encoding="utf-8",
        )
        real_update = vh.update_config_locked

        def _delete_then_update(target, *, mutate, **kwargs):
            # A concurrent writer removing the workspace after the pre-check passed.
            data = json.loads(Path(target).read_text(encoding="utf-8"))
            del data["workspaces"]["ops"]
            Path(target).write_text(json.dumps(data), encoding="utf-8")
            return real_update(target, mutate=mutate, **kwargs)

        with patch.object(vh, "update_config_locked", _delete_then_update):
            resp = await vh.api_variables(
                _request(
                    "PUT",
                    {"scope": "workspace", "workspace": "ops", "set": {"a": "1"}},
                )
            )

        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_unknown_workspace"
        after = json.loads(path.read_text(encoding="utf-8"))
        assert (
            "ops" not in after["workspaces"]
        ), "the concurrently-deleted workspace was resurrected by the write"

    async def test_the_refusal_leaves_other_scopes_untouched(self, wired):
        """Refusing must abort the whole write, not land a partial one."""
        _, path = wired
        path.write_text(
            json.dumps(
                {"workspaces": {"ops": {"dir": "workspace-ops"}}, "agent": {"model": "auto"}}
            ),
            encoding="utf-8",
        )
        real_update = vh.update_config_locked

        def _delete_then_update(target, *, mutate, **kwargs):
            data = json.loads(Path(target).read_text(encoding="utf-8"))
            del data["workspaces"]["ops"]
            Path(target).write_text(json.dumps(data), encoding="utf-8")
            return real_update(target, mutate=mutate, **kwargs)

        with patch.object(vh, "update_config_locked", _delete_then_update):
            resp = await vh.api_variables(
                _request(
                    "PUT",
                    {"scope": "workspace", "workspace": "ops", "set": {"a": "1"}},
                )
            )

        assert resp.status == 400
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["agent"] == {"model": "auto"}
        assert "variables" not in after


class TestTheLocalOverlay:
    """``KiroCrewConfig.load()`` deep-merges ``config.local.json`` over
    ``config.json``, but this endpoint writes ``config.json`` alone.

    Nothing in the original suite constructed an overlay at all, which is why a
    deterministic bug lived here: judged by the merged view an overlay-declared
    workspace exists, judged by the base mapping it does not, so the concurrent-
    deletion refusal fired on a workspace the same endpoint had just listed and it
    could never be saved. The refusal is now keyed on the BASE document's own prior
    state, and overlay-owned values are subtracted the way
    ``KiroCrewConfig.save()`` subtracts them.
    """

    def _overlay(self, tmp_path: Path, payload: dict) -> Path:
        local = tmp_path / "config.local.json"
        local.write_text(json.dumps(payload), encoding="utf-8")
        return local

    async def test_a_workspace_declared_only_in_the_overlay_is_refused(
        self, wired, monkeypatch, tmp_path
    ):
        """The deliberate COST of refusing every absent base entry.

        This test previously asserted the opposite — that such a workspace could be
        saved — and materializing its entry is what created the resurrection window
        three review rounds failed to close with a pre-lock discriminator. Since any
        pre-lock signal is stale by construction, the refusal is unconditional, and
        the price is that a workspace declared ONLY in config.local.json cannot be
        given variables here.

        The GET view still LISTS it (it is in the merged config), so the refusal has
        to be legible rather than silent. An operator who wants dashboard-managed
        variables for such a workspace declares it in config.json too.
        """
        cfg, path = wired
        path.write_text(
            json.dumps({"workspaces": {"ops": {"dir": "workspace-ops"}}}), encoding="utf-8"
        )
        local = self._overlay(tmp_path, {"workspaces": {"overlaid": {"dir": "ws-overlaid"}}})
        monkeypatch.setattr(vh, "config_local_path", lambda: local)
        cfg.workspaces["overlaid"] = WorkspaceConfig(dir="ws-overlaid")

        resp = await vh.api_variables(
            _request(
                "PUT",
                {"scope": "workspace", "workspace": "overlaid", "set": {"a": "1"}},
            )
        )

        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_unknown_workspace"
        # Nothing was materialized, which is the property that closes the window.
        after = json.loads(path.read_text(encoding="utf-8"))
        assert "overlaid" not in after["workspaces"]

    async def test_setting_an_overlay_owned_key_is_refused(self, wired, monkeypatch, tmp_path):
        """Refused rather than written. The base write would be INERT — the overlay
        wins on load — so accepting it would report a success the user cannot see,
        which is worse than saying the value is not ours to change.

        This replaces an earlier behaviour that silently subtracted such a pair on
        the way in: a save that looked like it worked and changed nothing.
        """
        _, path = wired
        path.write_text(json.dumps({}), encoding="utf-8")
        local = self._overlay(tmp_path, {"variables": {"FROM_OVERLAY": "old"}})
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        resp = await vh.api_variables(
            _request("PUT", {"scope": "global", "set": {"FROM_OVERLAY": "new"}})
        )

        assert resp.status == 400
        body = json.loads(resp.text)
        assert body["code"] == "variables_overlay_owned"
        assert "FROM_OVERLAY" in body["error"]
        assert "variables" not in json.loads(path.read_text(encoding="utf-8"))

    async def test_deleting_an_overlay_owned_key_is_refused(self, wired, monkeypatch, tmp_path):
        """The destructive half: a delete would drop the key from a base file that
        may not even hold it while the overlay keeps re-supplying it, so the row
        reappears on the next read after a reported success."""
        _, path = wired
        path.write_text(json.dumps({"variables": {"FROM_OVERLAY": "base"}}), encoding="utf-8")
        local = self._overlay(tmp_path, {"variables": {"FROM_OVERLAY": "wins"}})
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        resp = await vh.api_variables(
            _request("PUT", {"scope": "global", "delete": ["FROM_OVERLAY"]})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["code"] == "variables_overlay_owned"
        assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"FROM_OVERLAY": "base"}

    async def test_a_base_value_hidden_behind_an_overlay_key_is_not_lost(
        self, wired, monkeypatch, tmp_path
    ):
        """The third-round finding, asserted directly.

        ``config.json`` holds SHADOWED=base while the overlay supplies SHADOWED=wins,
        so the merged read never shows the base value. Saving an unrelated variable
        used to echo the whole merged map back and drop the base value entirely. A
        patch never reads it, so it survives.
        """
        _, path = wired
        path.write_text(
            json.dumps({"variables": {"SHADOWED": "base", "OTHER": "keep"}}), encoding="utf-8"
        )
        local = self._overlay(tmp_path, {"variables": {"SHADOWED": "wins"}})
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        resp = await vh.api_variables(
            _request("PUT", {"scope": "global", "set": {"UNRELATED": "x"}})
        )

        assert resp.status == 200
        stored = json.loads(path.read_text(encoding="utf-8"))["variables"]
        assert stored == {
            "SHADOWED": "base",
            "OTHER": "keep",
            "UNRELATED": "x",
        }, "saving one variable disturbed a base value shadowed by the overlay"

    async def test_the_view_reports_base_values_not_merged_ones(self, wired, monkeypatch, tmp_path):
        """What the panel may edit is what config.json holds. Reporting the merged
        value made the panel offer a pair it did not own."""
        _, path = wired
        path.write_text(json.dumps({"variables": {"SHADOWED": "base"}}), encoding="utf-8")
        local = self._overlay(tmp_path, {"variables": {"SHADOWED": "wins"}})
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        resp = await vh.api_variables(_request("GET"))

        payload = json.loads(resp.text)
        assert payload["global"]["SHADOWED"] == "base"
        assert payload["overlay_owned"]["global"] == ["SHADOWED"]

    async def test_the_view_reports_which_keys_the_overlay_owns(self, wired, monkeypatch, tmp_path):
        """Reported so the panel can say a pair is not deletable here, instead of
        showing a delete that succeeds and then reappears on the next read."""
        _, _path = wired
        local = self._overlay(
            tmp_path,
            {
                "variables": {"FROM_OVERLAY": "x"},
                "workspaces": {"ops": {"variables": {"WS_OVERLAY": "y"}}},
            },
        )
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        resp = await vh.api_variables(_request("GET"))

        payload = json.loads(resp.text)
        assert payload["overlay_owned"]["global"] == ["FROM_OVERLAY"]
        assert payload["overlay_owned"]["workspaces"]["ops"] == ["WS_OVERLAY"]

    async def test_a_malformed_overlay_is_treated_as_absent(self, wired, monkeypatch, tmp_path):
        """save() swallows exactly these two errors rather than refusing to persist
        the base config, so this path matches it."""
        _, path = wired
        path.write_text(json.dumps({}), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(vh, "config_local_path", lambda: local)

        resp = await vh.api_variables(_request("PUT", {"scope": "global", "set": {"MINE": "yes"}}))

        assert resp.status == 200
        assert json.loads(path.read_text(encoding="utf-8"))["variables"] == {"MINE": "yes"}
