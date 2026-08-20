"""Compare-and-set on PATCH /api/chat/slots/{slot}/folder.

An unconditional write is right for a user choosing a destination right now. It
is WRONG for one that replays a decision taken seconds ago: the sidebar's
drag-move undo bar offers to put a session back for 8s, and in that window
another client can move it on. Its broadcast has not necessarily arrived, so the
undoing client still believes the session sits where it dropped it — and an
unconditional PATCH would silently overwrite the newer placement.

`expected_folder_id` closes that: the write only lands if the server still
agrees, and a refusal reports the authoritative folder so the loser of the race
can show where the session actually is. Omitting the field keeps the old
unconditional behaviour, which is what every live "Move to folder…" uses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.dashboard.chat_folders import api_chat_slot_folder
from kiro_crew.dashboard.state import DashboardState, _ChatSlot

WORK = "fldr00000001"
LATER = "fldr00000002"


def _make_app(state: DashboardState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_patch("/api/chat/slots/{slot}/folder", api_chat_slot_folder)
    return app


def _state(slot: _ChatSlot) -> DashboardState:
    state = MagicMock(spec=DashboardState)
    state._slots = {slot.key: slot}
    state._folders = [
        {"id": WORK, "name": "Work", "parent_id": ""},
        {"id": LATER, "name": "Later", "parent_id": ""},
    ]
    state.push_slots_update = MagicMock()
    state.mutate_folders = AsyncMock(return_value="")
    return state


def _slot(key: str, folder_id: str) -> _ChatSlot:
    slot = _ChatSlot(key)
    slot.folder_id = folder_id
    return slot


async def _patch(state: DashboardState, body: dict) -> tuple[int, dict]:
    with (
        patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"),
        patch("kiro_crew.dashboard.chat_folders._unhide_folder", AsyncMock(return_value=True)),
    ):
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/slots/chat-1-100/folder", json=body)
            return resp.status, await resp.json()


class TestExpectedFolderId:
    @pytest.mark.asyncio
    async def test_the_write_lands_when_the_expectation_still_holds(self) -> None:
        slot = _slot("chat-1-100", WORK)
        status, body = await _patch(_state(slot), {"folder_id": "", "expected_folder_id": WORK})
        assert status == 200
        assert body["folder_id"] == ""
        assert slot.folder_id == ""

    @pytest.mark.asyncio
    async def test_a_stale_expectation_is_refused_and_changes_nothing(self) -> None:
        # Another client already moved this session Work -> Later. The undoing
        # client still expects Work, so its write must NOT land.
        slot = _slot("chat-1-100", LATER)
        status, body = await _patch(_state(slot), {"folder_id": "", "expected_folder_id": WORK})
        assert status == 409
        assert body["code"] == "folder_conflict"
        # …and it reports where the session actually is, so the loser can show it.
        assert body["folder_id"] == LATER
        assert slot.folder_id == LATER

    @pytest.mark.asyncio
    async def test_unfiled_is_expressed_as_the_empty_string_not_omission(self) -> None:
        # Undoing a drag OUT of the root expects "no folder". That has to be a
        # real expectation, not indistinguishable from sending no field at all.
        slot = _slot("chat-1-100", WORK)
        status, body = await _patch(_state(slot), {"folder_id": WORK, "expected_folder_id": ""})
        assert status == 409
        assert body["folder_id"] == WORK
        assert slot.folder_id == WORK

    @pytest.mark.asyncio
    async def test_a_stale_expectation_outranks_a_missing_destination(self) -> None:
        # Undo's ORIGIN folder was deleted while the session also moved on
        # elsewhere. Validating the destination first would answer 400 "folder
        # not found" — a refusal that says nothing about WHERE the session is, so
        # the client would fall back to its stale idea of the placement. The
        # staleness answer has to win, and carry the truth with it.
        slot = _slot("chat-1-100", LATER)
        status, body = await _patch(
            _state(slot),
            {"folder_id": "fldrdeleted1", "expected_folder_id": WORK},
        )
        assert status == 409
        assert body["code"] == "folder_conflict"
        assert body["folder_id"] == LATER
        assert slot.folder_id == LATER

    @pytest.mark.asyncio
    async def test_a_conditional_write_to_a_deleted_folder_lands_unfiled(self) -> None:
        # The expectation HOLDS, but the folder being restored has since been
        # deleted. A conditional write is restoring a placement, not choosing
        # one: unfiled is where that session belongs now, and the client cannot
        # know the folder is gone (its folder list can be a broadcast behind), so
        # a 400 would leave Undo doing nothing at all.
        slot = _slot("chat-1-100", WORK)
        status, body = await _patch(
            _state(slot),
            {"folder_id": "fldrdeleted1", "expected_folder_id": WORK},
        )
        assert status == 200
        assert body["folder_id"] == ""
        assert slot.folder_id == ""

    @pytest.mark.asyncio
    async def test_an_unconditional_write_to_a_deleted_folder_is_still_refused(self) -> None:
        # A live "Move to folder…" is a CHOICE. Silently redirecting it to unfiled
        # would file the session somewhere the user did not pick.
        slot = _slot("chat-1-100", WORK)
        status, _ = await _patch(_state(slot), {"folder_id": "fldrdeleted1"})
        assert status == 400
        assert slot.folder_id == WORK

    @pytest.mark.asyncio
    async def test_omitting_the_field_stays_unconditional(self) -> None:
        # Every live "Move to folder…" takes this path: the user is choosing now,
        # so whatever the session's current folder is, the write lands.
        slot = _slot("chat-1-100", LATER)
        status, _ = await _patch(_state(slot), {"folder_id": WORK})
        assert status == 200
        assert slot.folder_id == WORK
