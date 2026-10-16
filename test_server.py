"""Unit tests for the RomM MCP server.

No live RomM instance required — HTTP is stubbed at the _request boundary, so
these tests verify tool logic, request shaping, scope hygiene, and formatting.

Run:
    uv run pytest test_server.py -v
"""

import json
import os

import pytest

# Ensure import doesn't depend on real credentials.
os.environ.setdefault("ROMM_USERNAME", "tester")
os.environ.setdefault("ROMM_PASSWORD", "secret")

import server  # noqa: E402


class _Recorder(list):
    """A list of recorded requests carrying one FIFO response queue per seam.

    The recorder itself holds the JSON requests (method/path/params/json/data),
    exactly as before. `bytes` and `uploads` hold the binary and multipart
    requests; their matching canned responses live in `bytes_responses` and
    `uploads_responses`.
    """
    responses: list
    bytes: list
    bytes_responses: list
    uploads: list
    uploads_responses: list


@pytest.fixture
def calls(monkeypatch):
    """Capture every outbound request and feed canned responses per seam.

    Append to `calls.responses`, `calls.bytes_responses` or
    `calls.uploads_responses` to pop return values FIFO; the defaults are {} for
    JSON and multipart and (b"", {}) for bytes. A queued BaseException instance
    is raised instead of returned, so error paths (409/503, timeouts) can be
    driven from the same queue.
    """
    recorded = _Recorder()
    recorded.responses = []
    recorded.bytes = []
    recorded.bytes_responses = []
    recorded.uploads = []
    recorded.uploads_responses = []

    def _next(queue):
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def fake_request(method, path, *, params=None, json=None, data=None,
                           long_timeout=False, auth_required=True):
        recorded.append({
            "method": method, "path": path, "params": params,
            "json": json, "data": data,
        })
        return _next(recorded.responses) if recorded.responses else {}

    async def fake_request_bytes(method, path, *, params=None,
                                 long_timeout=False, auth_required=True):
        recorded.bytes.append({
            "method": method, "path": path, "params": params,
            "long_timeout": long_timeout,
        })
        return _next(recorded.bytes_responses) if recorded.bytes_responses else (b"", {})

    async def fake_request_multipart(method, path, *, params=None, files=None,
                                     long_timeout=False, auth_required=True):
        recorded.uploads.append({
            "method": method, "path": path, "params": params,
            "files": files, "long_timeout": long_timeout,
        })
        return _next(recorded.uploads_responses) if recorded.uploads_responses else {}

    monkeypatch.setattr(server, "_request", fake_request)
    monkeypatch.setattr(server, "_request_bytes", fake_request_bytes)
    monkeypatch.setattr(server, "_request_multipart", fake_request_multipart)
    return recorded


# ── Scope hygiene ─────────────────────────────────────────────────────────


def test_scopes_request_only_needed_writes():
    scopes = set(server._DEFAULT_SCOPES.split())
    # The write scopes the tools actually use, plus task running.
    assert "roms.user.write" in scopes
    assert "collections.write" in scopes
    assert "assets.write" in scopes
    assert "tasks.run" in scopes


def test_scopes_exclude_dangerous_writes():
    scopes = set(server._DEFAULT_SCOPES.split())
    for forbidden in ("roms.write", "platforms.write", "firmware.write",
                      "users.write", "me.write", "devices.write"):
        assert forbidden not in scopes, f"{forbidden} should not be requested"


# ── Formatting helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (None, "0 B"),
    (512, "512 B"),
    (2048, "2 KB"),
    (5 * 1024**2, "5 MB"),
    (3 * 1024**3, "3.0 GB"),
])
def test_fmt_size(n, expected):
    assert server._fmt_size(n) == expected


# ── romm_set_status ─────────────────────────────────────────────────────────


async def test_set_status_rejects_bad_status(calls):
    out = await server.romm_set_status(5, status="bogus")
    assert "Invalid status" in out
    assert not calls  # never hit the API


async def test_set_status_mutually_exclusive_played(calls):
    out = await server.romm_set_status(5, mark_played=True, clear_played=True)
    assert "mutually exclusive" in out
    assert not calls


async def test_set_status_requires_a_field(calls):
    out = await server.romm_set_status(5)
    assert "Nothing to update" in out
    assert not calls


async def test_set_status_builds_body_and_params(calls):
    calls.responses.append({"status": "finished", "backlogged": True,
                            "last_played": "2026-06-23T00:00:00Z"})
    out = await server.romm_set_status(
        7, status="finished", backlogged=True, mark_played=True
    )
    req = calls[0]
    assert req["method"] == "PUT"
    assert req["path"] == "roms/7/props"
    assert req["json"] == {"status": "finished", "backlogged": True}
    assert req["params"] == {"update_last_played": True}
    assert "finished" in out


async def test_set_status_rating_bounds(calls):
    out = await server.romm_set_status(7, rating=99)
    assert "0 and 10" in out
    assert not calls


# ── romm_favorite ─────────────────────────────────────────────────────────


async def test_favorite_adds_to_existing_collection(calls):
    calls.responses.append([{"id": 3, "name": "Favourites", "is_favorite": True}])
    out = await server.romm_favorite(42, favorite=True)
    # First call lists collections, second adds the rom.
    assert calls[0]["path"] == "collections"
    assert calls[1]["method"] == "POST"
    assert calls[1]["path"] == "collections/3/roms"
    assert calls[1]["json"] == {"rom_ids": [42]}
    assert "Added ROM 42" in out


async def test_favorite_creates_collection_when_missing(calls):
    calls.responses.append([])  # no collections
    calls.responses.append({"id": 9, "name": "Favourites", "is_favorite": True})  # create
    calls.responses.append({})  # add rom
    out = await server.romm_favorite(42)
    assert calls[1]["method"] == "POST"
    assert calls[1]["path"] == "collections"
    assert calls[1]["params"] == {"is_favorite": True}
    assert calls[2]["path"] == "collections/9/roms"
    assert "Added ROM 42" in out


async def test_unfavorite_uses_delete(calls):
    calls.responses.append([{"id": 3, "is_favorite": True}])
    out = await server.romm_favorite(42, favorite=False)
    assert calls[1]["method"] == "DELETE"
    assert calls[1]["path"] == "collections/3/roms"
    assert calls[1]["json"] == {"rom_ids": [42]}
    assert "Removed ROM 42" in out


async def test_unfavorite_with_no_collection(calls):
    calls.responses.append([])
    out = await server.romm_favorite(42, favorite=False)
    assert "nothing to remove" in out.lower()


# ── notes ──────────────────────────────────────────────────────────────────


async def test_add_note_requires_title(calls):
    out = await server.romm_add_note(5, title="   ")
    assert "title is required" in out
    assert not calls


async def test_add_note_posts_body(calls):
    calls.responses.append({"id": 11})
    out = await server.romm_add_note(5, title="Boss tips", content="hi", tags=["wip"])
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "roms/5/notes"
    assert calls[0]["json"] == {"title": "Boss tips", "content": "hi",
                                "is_public": False, "tags": ["wip"]}
    assert "note id: 11" in out


async def test_update_note_only_sends_changed(calls):
    calls.responses.append({})
    await server.romm_update_note(5, 11, content="new body")
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "roms/5/notes/11"
    assert calls[0]["json"] == {"content": "new body"}


async def test_update_note_noop(calls):
    out = await server.romm_update_note(5, 11)
    assert "Nothing to update" in out
    assert not calls


async def test_delete_note(calls):
    calls.responses.append({})
    out = await server.romm_delete_note(5, 11)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "roms/5/notes/11"
    assert "Deleted note 11" in out


# ── transport selection ──────────────────────────────────────────────────────


def test_transport_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv("ROMM_MCP_TRANSPORT", raising=False)
    assert server._transport_config() == ("stdio", {})


def test_transport_http_reads_host_port(monkeypatch):
    monkeypatch.setenv("ROMM_MCP_TRANSPORT", "HTTP")
    monkeypatch.setenv("ROMM_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("ROMM_MCP_PORT", "9000")
    assert server._transport_config() == (
        "http", {"host": "0.0.0.0", "port": 9000})


# ── play sessions, smart/virtual collections, identity (v1.2) ────────────────


async def test_log_play_session_builds_payload(calls):
    calls.responses.append({})
    out = await server.romm_log_play_session(42, 30)
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "play-sessions"
    sessions = req["json"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["rom_id"] == 42
    assert sessions[0]["duration_ms"] == 30 * 60_000
    assert sessions[0]["start_time"] < sessions[0]["end_time"]
    assert "Logged a 30-minute" in out


async def test_log_play_session_bounds(calls):
    out = await server.romm_log_play_session(42, 0)
    assert "between 1 and 1440" in out
    assert not calls


async def test_delete_play_session(calls):
    calls.responses.append({})
    out = await server.romm_delete_play_session(7)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "play-sessions/7"
    assert "Deleted play session 7" in out


async def test_activity_rom_scoped_path(calls):
    calls.responses.append([{"username": "j", "rom_name": "Chrono Trigger",
                             "platform_name": "SNES",
                             "started_at": "2026-07-18T00:00:00Z"}])
    out = await server.romm_activity(rom_id=5)
    assert calls[0]["path"] == "activity/rom/5"
    assert "j played Chrono Trigger [SNES]" in out


async def test_virtual_collections_params(calls):
    calls.responses.append([{"id": "abc123", "name": "Mario", "rom_count": 9}])
    out = await server.romm_virtual_collections("franchise")
    assert calls[0]["params"]["type"] == "franchise"
    assert "Mario (9 ROMs)" in out
    assert "abc123" in out


async def test_create_smart_collection_encodes_criteria(calls):
    calls.responses.append({"id": 3, "name": "SNES RPGs", "rom_count": 12,
                            "filter_summary": "platform SNES"})
    out = await server.romm_create_smart_collection(
        "SNES RPGs", filter_criteria={"platform_ids": [5], "genres": ["RPG"]})
    req = calls[0]
    assert req["path"] == "collections/smart"
    assert json.loads(req["data"]["filter_criteria"]) == {
        "platform_ids": [5], "genres": ["RPG"]}
    assert req["params"] == {"is_public": False}
    assert "id: 3" in out


async def test_create_smart_collection_rejects_unknown_keys(calls):
    out = await server.romm_create_smart_collection(
        "X", filter_criteria={"platfrom_ids": [5]})
    assert "Unknown filter_criteria key" in out
    assert "platfrom_ids" in out
    assert not calls


async def test_update_smart_collection_noop(calls):
    out = await server.romm_update_smart_collection(3)
    assert "Nothing to update" in out
    assert not calls


async def test_whoami_formats_identity_and_grants(calls):
    calls.responses.append({"id": 1, "username": "romm", "role": "admin",
                            "enabled": True})
    calls.responses.append({"is_admin": True, "grants": ["roms.read"],
                            "hidden": []})
    out = await server.romm_whoami()
    assert "User: romm" in out
    assert "Admin: yes" in out


async def test_whoami_handles_object_grants(calls):
    # Live RomM 5.0 returns grants as a list of objects — must not sort dicts.
    calls.responses.append({"id": 1, "username": "romm"})
    calls.responses.append({"is_admin": False, "grants": [
        {"action": "rom.view", "scope": {"kind": "global", "id": None}},
        {"action": "collection.edit", "scope": {"kind": "collection", "id": 4}}],
        "hidden": []})
    out = await server.romm_whoami()
    assert "collection.edit (collection 4), rom.view" in out
    assert "Grants (2)" in out


async def test_metadata_search_params(calls):
    calls.responses.append([{"name": "Super Mario 64", "slug": "sm64",
                             "igdb_id": 1074, "moby_id": None}])
    out = await server.romm_metadata_search(42)
    assert calls[0]["params"] == {"rom_id": 42, "search_by": "name"}
    assert "Super Mario 64" in out
    assert "igdb" in out


# ── tasks & scan ─────────────────────────────────────────────────────────────


async def test_tasks_lists_registry_and_status(calls):
    calls.responses.append({"scheduled": [
        {"name": "scan_library", "enabled": True, "manual_run": False,
         "cron_string": "0 3 * * *"}]})
    calls.responses.append([])
    out = await server.romm_tasks()
    assert "scan_library" in out
    assert "cron 0 3 * * *" in out
    assert "manual run not allowed" in out
    assert "No tasks currently running" in out


async def test_scan_library_quick_body(calls):
    calls.responses.append({
        "task_key": "scan_library", "task_name": "Scan library",
        "task_id": "abc-123", "status": "queued",
        "created_at": "2026-01-01T00:00:00Z",
        "enqueued_at": "2026-01-01T00:00:01Z",
    })
    out = await server.romm_scan_library()
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "tasks/scan"
    assert req["json"] == {"type": "quick"}
    assert "Library scan started (quick)" in out
    assert "  task_name: Scan library" in out
    assert 'Follow progress with romm_task_detail(task_id="abc-123").' in out


async def test_scan_library_full_body(calls):
    calls.responses.append({"task_id": "t-1"})
    out = await server.romm_scan_library(full=True)
    assert calls[0]["json"] == {"type": "full"}
    assert "Library scan started (full)" in out


async def test_scan_library_409_reports_scan_in_flight(calls):
    calls.responses.append(RuntimeError("API error 409: scan already running"))
    out = await server.romm_scan_library()
    assert "already in progress" in out


async def test_scan_library_503_reports_no_worker(calls):
    calls.responses.append(RuntimeError("API error 503: no worker available"))
    out = await server.romm_scan_library()
    assert "task worker" in out


async def test_scan_library_other_errors_propagate(calls):
    calls.responses.append(RuntimeError("API error 500: boom"))
    with pytest.raises(RuntimeError):
        await server.romm_scan_library()


# ── romm_task_detail ─────────────────────────────────────────────────────────

# Contractual message (SEC-001): romm_task_detail rejects any id containing
# characters outside [A-Za-z0-9._-]+ with this exact wording and never issues a
# request. Pinned verbatim so the rejecting contract cannot drift.
INVALID_TASK_ID_MSG = (
    "task_id contains invalid characters. Only letters, digits, dots, "
    "underscores and hyphens are allowed."
)

# SEC-006: a bare "." or ".." passes the charset allowlist yet httpx collapses
# the dot segment, so romm_task_detail rejects it with this exact wording
# (distinct from the charset message) and never issues a request.
INVALID_TASK_ID_DOT_MSG = 'task_id must not be "." or "..".'


async def test_task_detail_renders_full_fields(calls):
    calls.responses.append({
        "task_key": "scan_library", "task_name": "Scan library",
        "task_id": "abc-123", "status": "complete",
        "created_at": "2026-01-01T00:00:00Z",
        "enqueued_at": "2026-01-01T00:00:01Z",
        "started_at": "2026-01-01T00:00:02Z",
        "ended_at": "2026-01-01T00:01:00Z",
        "result": {"scanned": 12},
    })
    out = await server.romm_task_detail("abc-123")
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "tasks/abc-123"
    assert "Task abc-123:" in out
    assert "  task_key: scan_library" in out
    assert "  status: complete" in out
    assert "  started_at: 2026-01-01T00:00:02Z" in out
    assert "  ended_at: 2026-01-01T00:01:00Z" in out
    assert '  result: {"scanned": 12}' in out


async def test_task_detail_omits_missing_fields(calls):
    calls.responses.append({"task_id": "abc", "status": "running"})
    out = await server.romm_task_detail("abc")
    assert "  status: running" in out
    assert "started_at" not in out
    assert "ended_at" not in out
    assert "result" not in out


async def test_task_detail_lists_extra_keys_sorted(calls):
    calls.responses.append({"task_id": "t", "zeta": 1, "alpha": 2})
    out = await server.romm_task_detail("t")
    assert out.index("  alpha: 2") < out.index("  zeta: 1")


async def test_task_detail_truncates_long_result(calls):
    calls.responses.append({"task_id": "t", "result": "x" * 600})
    out = await server.romm_task_detail("t")
    assert f"  result: {'x' * 500}..." in out
    assert "x" * 501 not in out


async def test_task_detail_blank_id_makes_no_request(calls):
    out = await server.romm_task_detail("   ")
    assert "task_id is required" in out
    assert not calls


@pytest.mark.parametrize("task_id", [
    "1/../../users/me",
    "x?foo=bar",
    "a#frag",
    "a%2e%2e",
    "a\\b",
    "a b",
])
async def test_task_detail_rejects_hostile_ids(calls, task_id):
    # SEC-001: an id that could smuggle another path segment or query string is
    # rejected with the contractual message and no request on any HTTP seam.
    out = await server.romm_task_detail(task_id)
    assert out == INVALID_TASK_ID_MSG
    assert not calls
    assert not calls.bytes
    assert not calls.uploads


async def test_task_detail_invalid_id_message_is_pinned(calls):
    # The rejection wording is part of the contract, not an implementation
    # detail (SEC-001): pin the exact string a caller can rely on.
    out = await server.romm_task_detail("x?foo=bar")
    assert out == (
        "task_id contains invalid characters. Only letters, digits, dots, "
        "underscores and hyphens are allowed."
    )
    assert not calls


@pytest.mark.parametrize("task_id,expected_path", [
    ("abc-123", "tasks/abc-123"),
    ("550e8400-e29b-41d4-a716-446655440000",
     "tasks/550e8400-e29b-41d4-a716-446655440000"),
    ("a.b_c-1", "tasks/a.b_c-1"),
    (42, "tasks/42"),
])
async def test_task_detail_valid_id_is_single_path_segment(calls, task_id,
                                                           expected_path):
    # Valid ids (str or int) still work and resolve to exactly one path segment
    # with no extra segments and no query parameters (SEC-001).
    calls.responses.append({"task_id": str(task_id), "status": "complete"})
    out = await server.romm_task_detail(task_id)
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == expected_path
    assert calls[0]["path"].count("/") == 1
    assert calls[0]["params"] is None
    assert "  status: complete" in out


@pytest.mark.parametrize("task_id", [".", "..", " . "])
async def test_task_detail_rejects_dot_segments(calls, task_id):
    # SEC-006: ids composed only of dots pass the charset allowlist yet httpx
    # collapses the dot segment ("." -> tasks/, ".." -> the parent), so they are
    # rejected with the dot-segment message and no request on any HTTP seam.
    out = await server.romm_task_detail(task_id)
    assert out == INVALID_TASK_ID_DOT_MSG
    assert not calls
    assert not calls.bytes
    assert not calls.uploads


async def test_task_detail_dot_segment_message_is_pinned(calls):
    # The dot-segment rejection wording is part of the SEC-006 contract.
    out = await server.romm_task_detail("..")
    assert out == 'task_id must not be "." or "..".'
    assert not calls
    assert not calls.bytes
    assert not calls.uploads


@pytest.mark.parametrize("task_id,expected_path", [
    ("...", "tasks/..."),
    ("abc-123", "tasks/abc-123"),
])
async def test_task_detail_three_dots_and_normal_id_still_request(calls, task_id,
                                                                  expected_path):
    # SEC-006 pins the boundary: only bare "." and ".." are rejected. Three dots
    # ("...") and a normal id still issue exactly one GET on tasks/{id}.
    calls.responses.append({"task_id": task_id, "status": "complete"})
    out = await server.romm_task_detail(task_id)
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == expected_path
    assert calls[0]["params"] is None
    assert len(calls.bytes) == 0
    assert len(calls.uploads) == 0
    assert "  status: complete" in out


async def test_task_detail_not_found(calls):
    calls.responses.append({})
    out = await server.romm_task_detail("abc")
    assert "No task detail available for abc" in out




# ── romm_get_item favorite detection ────────────────────────────────────────


async def test_get_item_favorite_via_collection_membership_on_50(calls):
    # 5.0: rom_user has no is_favorite, and user_collections embeds
    # is_favorite as null — favorite-ness comes from id-matching the
    # favorites collection.
    calls.responses.append({
        "id": 14376, "name": "Super Metroid", "rom_user": {"status": None},
        "user_collections": [{"id": 10, "name": "Favorites", "is_favorite": None}],
    })
    calls.responses.append([{"id": 10, "name": "Favorites", "is_favorite": True}])
    out = await server.romm_get_item(14376)
    assert calls[1]["path"] == "collections"
    assert "Favorite: yes" in out


async def test_get_item_not_favorite_when_not_member(calls):
    calls.responses.append({
        "id": 5, "name": "Beyond Oasis", "rom_user": {},
        "user_collections": [{"id": 9, "name": "Action RPG", "is_favorite": None}],
    })
    calls.responses.append([{"id": 10, "name": "Favorites", "is_favorite": True}])
    out = await server.romm_get_item(5)
    assert "Favorite: yes" not in out


async def test_get_item_favorite_legacy_4x_field_skips_lookup(calls):
    calls.responses.append({
        "id": 5, "name": "Chrono Trigger",
        "rom_user": {"is_favorite": True},
    })
    out = await server.romm_get_item(5)
    assert len(calls) == 1  # no collections lookup needed
    assert "Favorite: yes" in out


# ── collections ──────────────────────────────────────────────────────────────


async def test_collections_uses_rom_count_on_50(calls):
    calls.responses.append([{"id": 1, "name": "RPGs", "rom_count": 12}])
    out = await server.romm_collections()
    assert "RPGs (12 ROMs)" in out


async def test_collections_falls_back_to_embedded_roms_on_4x(calls):
    calls.responses.append([{"id": 1, "name": "RPGs", "roms": [{}, {}]}])
    out = await server.romm_collections()
    assert "RPGs (2 ROMs)" in out


async def test_collection_detail_fetches_members_on_50(calls):
    # 5.0: detail has rom_count/rom_ids but no embedded roms list.
    calls.responses.append({"id": 4, "name": "RPGs", "rom_count": 2, "rom_ids": [1, 2]})
    calls.responses.append({"items": [{"name": "Chrono Trigger", "platform_slug": "snes"},
                                      {"name": "Earthbound"}], "total": 2})
    out = await server.romm_collection_detail(4)
    assert calls[1]["path"] == "roms"
    assert calls[1]["params"]["collection_id"] == 4
    assert "ROMs: 2" in out
    assert "Chrono Trigger" in out


async def test_collection_detail_uses_embedded_roms_on_4x(calls):
    calls.responses.append({"id": 4, "name": "RPGs",
                            "roms": [{"name": "Chrono Trigger"}]})
    out = await server.romm_collection_detail(4)
    assert len(calls) == 1  # no second fetch needed
    assert "Chrono Trigger" in out


async def test_create_collection_uses_form_data(calls):
    calls.responses.append({"id": 4, "name": "RPGs"})
    out = await server.romm_create_collection("RPGs", description="best")
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "collections"
    assert calls[0]["data"] == {"name": "RPGs", "description": "best"}
    assert calls[0]["json"] is None
    assert "id: 4" in out


async def test_add_to_collection(calls):
    calls.responses.append({"rom_ids": [1, 2, 3]})
    out = await server.romm_add_to_collection(4, [2, 3])
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "collections/4/roms"
    assert calls[0]["json"] == {"rom_ids": [2, 3]}
    assert "now has 3 ROMs" in out


async def test_remove_from_collection(calls):
    calls.responses.append({"rom_ids": [1]})
    out = await server.romm_remove_from_collection(4, [2, 3])
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "collections/4/roms"
    assert "now has 1 ROMs" in out


async def test_add_to_collection_empty(calls):
    out = await server.romm_add_to_collection(4, [])
    assert "at least one" in out.lower()
    assert not calls


async def test_delete_collection(calls):
    calls.responses.append({})
    out = await server.romm_delete_collection(4)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "collections/4"
    assert "Deleted collection 4" in out


# ── romm_states ──────────────────────────────────────────────────────────────


async def test_states_no_filters_params_empty(calls):
    calls.responses.append([])
    out = await server.romm_states()
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "states"
    assert calls[0]["params"] == {}
    assert "No save states found." in out


async def test_states_filters_and_limit_truncation(calls):
    calls.responses.append([
        {"id": 9, "file_name": "slot1.state", "file_size_bytes": 2048,
         "rom_id": 5, "platform_slug": "snes", "emulator": "retroarch",
         "updated_at": "2026-01-01T00:00:00Z"},
        {"id": 10, "file_name": "slot2.state", "file_size_bytes": 1024,
         "rom_id": 5},
    ])
    out = await server.romm_states(rom_id=5, platform_id=2, limit=1)
    assert calls[0]["params"] == {"rom_id": 5, "platform_id": 2}
    assert "Save states (2):" in out
    assert "  - slot1.state (ROM 5) [snes] [retroarch] \u2014 2 KB" in out
    assert "    ID: 9" in out
    assert "(1 more states not shown)" in out
    assert "slot2.state" not in out


async def test_states_empty_message_qualifier(calls):
    calls.responses.append([])
    out = await server.romm_states(rom_id=7)
    assert "No save states found for ROM 7." in out


async def test_states_accepts_items_dict(calls):
    calls.responses.append({"items": [{"id": 1, "file_name": "a.state"}]})
    out = await server.romm_states()
    assert "  - a.state" in out
    assert "    ID: 1" in out


# ── romm_state_detail ────────────────────────────────────────────────────────


async def test_state_detail_renders_fields(calls):
    calls.responses.append({
        "id": 9, "file_name": "slot1.state", "rom_name": "Super Metroid",
        "rom_id": 14376, "platform_display_name": "SNES",
        "emulator": "retroarch", "file_size_bytes": 2048,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
        "is_favorite": True, "is_public": False,
        "labels": ["speedrun", "wip"],
    })
    out = await server.romm_state_detail(9)
    assert calls[0]["path"] == "states/9"
    assert "Save state 9:" in out
    assert "  File: slot1.state" in out
    assert "  ROM: Super Metroid (ROM 14376)" in out
    assert "  Platform: SNES" in out
    assert "  Emulator: retroarch" in out
    assert "  Size: 2 KB" in out
    assert "  Favorite: yes" in out
    assert "  Public: no" in out
    assert "  Labels: speedrun, wip" in out
    assert "  ID: 9" in out


async def test_state_detail_not_found(calls):
    calls.responses.append({})
    out = await server.romm_state_detail(9)
    assert "Save state 9 not found." in out


# ── romm_state_download ──────────────────────────────────────────────────────


async def test_state_download_to_explicit_file(calls, tmp_path):
    calls.bytes_responses.append(
        (b"BIN", {"content-disposition": 'attachment; filename="slot1.state"'}))
    target = tmp_path / "out.state"
    out = await server.romm_state_download(9, str(target))
    req = calls.bytes[0]
    assert req["method"] == "GET"
    assert req["path"] == "states/9/content"
    assert req["long_timeout"] is True
    assert target.read_bytes() == b"BIN"
    assert f"Saved save state 9 to {target} (3 B)." in out


async def test_state_download_directory_uses_header_name(calls, tmp_path):
    calls.bytes_responses.append(
        (b"DATA", {"content-disposition": 'attachment; filename="slot2.state"'}))
    out = await server.romm_state_download(9, str(tmp_path))
    dest = tmp_path / "slot2.state"
    assert dest.read_bytes() == b"DATA"
    assert str(dest) in out


async def test_state_download_falls_back_to_default_name(calls, tmp_path):
    calls.bytes_responses.append((b"X", {}))
    out = await server.romm_state_download(9, str(tmp_path))
    assert (tmp_path / "state_9.bin").read_bytes() == b"X"
    assert str(tmp_path / "state_9.bin") in out


async def test_state_download_control_char_header_uses_default_name(calls, tmp_path):
    # SEC-002: a NUL-smuggling Content-Disposition name is rejected by the
    # parser, so the download falls back to state_{id}.bin and does not raise.
    calls.bytes_responses.append(
        (b"X", {"content-disposition": "attachment; filename*=UTF-8''a%00b.bin"}))
    out = await server.romm_state_download(9, str(tmp_path))
    assert (tmp_path / "state_9.bin").read_bytes() == b"X"
    assert [p.name for p in tmp_path.iterdir()] == ["state_9.bin"]
    assert str(tmp_path / "state_9.bin") in out


async def test_state_download_refuses_existing_file(calls, tmp_path):
    target = tmp_path / "keep.state"
    target.write_bytes(b"ORIGINAL")
    calls.bytes_responses.append((b"NEWDATA", {}))
    out = await server.romm_state_download(9, str(target))
    assert "File already exists" in out
    assert "overwrite=True" in out
    assert target.read_bytes() == b"ORIGINAL"


async def test_state_download_overwrite_replaces(calls, tmp_path):
    target = tmp_path / "keep.state"
    target.write_bytes(b"OLD")
    calls.bytes_responses.append((b"NEW", {}))
    out = await server.romm_state_download(9, str(target), overwrite=True)
    assert target.read_bytes() == b"NEW"
    assert f"Saved save state 9 to {target} (3 B)." in out


# ── _filename_from_content_disposition ───────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ('attachment; filename="save.state"', "save.state"),
    ("attachment; filename=save.state", "save.state"),
    ("attachment; filename*=UTF-8''my%20save.state", "my save.state"),
    ("attachment; filename=\"plain.state\"; filename*=UTF-8''star.state",
     "star.state"),
    ('attachment; filename="evil/../../x.state"', "x.state"),
    ('attachment; filename="C:\\Users\\evil.state"', "evil.state"),
    ('attachment; filename=".."', None),
    ('attachment; filename=""', None),
    ("attachment", None),
    ("attachment; filename*=UTF-8''a%00b.bin", None),
    ("attachment; filename*=UTF-8''a%01b.bin", None),
    ("attachment; filename*=UTF-8''a%7fb.bin", None),
    (None, None),
])
def test_filename_from_content_disposition(value, expected):
    assert server._filename_from_content_disposition(value) == expected


def test_write_download_wraps_valueerror_as_runtimeerror(tmp_path):
    # SEC-002: a residual ValueError reaching the write (e.g. an embedded NUL
    # in the destination) must surface as RuntimeError per the error contract,
    # never as a raw ValueError escaping through FastMCP.
    with pytest.raises(RuntimeError, match="Failed to write"):
        server._write_download(b"X", {}, target_path=str(tmp_path),
                               default_name="bad\x00name.bin")


# ── romm_save_download ───────────────────────────────────────────────────────


async def test_save_download_shaping_and_fallback(calls, tmp_path):
    calls.bytes_responses.append((b"SAVE", {}))
    out = await server.romm_save_download(3, str(tmp_path))
    req = calls.bytes[0]
    assert req["method"] == "GET"
    assert req["path"] == "saves/3/content"
    assert req["params"] is None
    assert req["long_timeout"] is True
    assert (tmp_path / "save_3.bin").read_bytes() == b"SAVE"
    assert str(tmp_path / "save_3.bin") in out


# ── romm_state_upload ────────────────────────────────────────────────────────


async def test_state_upload_shaping_single_part(calls, tmp_path):
    state_file = tmp_path / "save.state"
    state_file.write_bytes(b"STATEBYTES")
    calls.uploads_responses.append({"id": 42})
    out = await server.romm_state_upload(5, str(state_file))
    req = calls.uploads[0]
    assert req["method"] == "POST"
    assert req["path"] == "states"
    assert req["params"] == {"rom_id": 5}
    assert req["long_timeout"] is True
    assert set(req["files"]) == {"stateFile"}
    assert req["files"]["stateFile"] == (
        "save.state", b"STATEBYTES", "application/octet-stream")
    assert "Uploaded save state save.state for ROM 5 (state id: 42)." in out


async def test_state_upload_emulator_and_screenshot(calls, tmp_path):
    state_file = tmp_path / "save.state"
    state_file.write_bytes(b"S")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"PNG")
    calls.uploads_responses.append({"id": 7})
    out = await server.romm_state_upload(
        5, str(state_file), emulator="retroarch", screenshot_path=str(shot))
    req = calls.uploads[0]
    assert req["params"] == {"rom_id": 5, "emulator": "retroarch"}
    assert set(req["files"]) == {"stateFile", "screenshotFile"}
    assert req["files"]["screenshotFile"] == (
        "shot.png", b"PNG", "application/octet-stream")
    assert "(state id: 7)" in out


async def test_state_upload_missing_file_makes_no_request(calls, tmp_path):
    out = await server.romm_state_upload(5, str(tmp_path / "nope.state"))
    assert "File not found" in out
    assert not calls.uploads
    assert not calls


async def test_state_upload_missing_screenshot_makes_no_request(calls, tmp_path):
    state_file = tmp_path / "save.state"
    state_file.write_bytes(b"S")
    out = await server.romm_state_upload(
        5, str(state_file), screenshot_path=str(tmp_path / "nope.png"))
    assert "Screenshot file not found" in out
    assert not calls.uploads


async def test_state_upload_rejects_bad_rom_id(calls, tmp_path):
    state_file = tmp_path / "save.state"
    state_file.write_bytes(b"S")
    out = await server.romm_state_upload(0, str(state_file))
    assert "rom_id must be positive" in out
    assert not calls.uploads


# ── romm_state_delete / romm_state_favorite / romm_state_update ──────────────


async def test_state_delete_posts_ids_body(calls):
    calls.responses.append({})
    out = await server.romm_state_delete(9)
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "states/delete"
    assert calls[0]["json"] == {"states": [9]}
    assert "Deleted save state 9." in out


def test_state_delete_docstring_is_destructive():
    doc = server.romm_state_delete.__doc__
    assert doc.startswith("Permanently delete")
    assert "cannot be undone" in doc


async def test_state_favorite_true(calls):
    calls.responses.append({})
    out = await server.romm_state_favorite(9)
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "states/9/favorite"
    assert calls[0]["json"] == {"is_favorite": True}
    assert "Save state 9: added to favorites." in out


async def test_state_favorite_false(calls):
    calls.responses.append({})
    out = await server.romm_state_favorite(9, favorite=False)
    assert calls[0]["json"] == {"is_favorite": False}
    assert "Save state 9: removed from favorites." in out


async def test_state_update_single_field_sends_one_put(calls):
    calls.responses.append({})
    out = await server.romm_state_update(9, labels=["speedrun"])
    assert len(calls) == 1
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "states/9/labels"
    assert calls[0]["json"] == {"labels": ["speedrun"]}
    assert "Save state 9: updated labels." in out


async def test_state_update_all_fields_in_fixed_order(calls):
    calls.responses.extend([{}, {}, {}])
    out = await server.romm_state_update(
        9, labels=["a"], is_public=True, file_name="x.state")
    assert [c["path"] for c in calls] == [
        "states/9/labels", "states/9/visibility", "states/9/file-name"]
    assert [c["json"] for c in calls] == [
        {"labels": ["a"]}, {"is_public": True}, {"file_name": "x.state"}]
    assert "Save state 9: updated labels, is_public, file_name." in out


async def test_state_update_noop_makes_no_request(calls):
    out = await server.romm_state_update(9)
    assert "Nothing to update" in out
    assert not calls


async def test_state_update_blank_file_name_makes_no_request(calls):
    out = await server.romm_state_update(9, file_name="   ")
    assert "file_name must not be empty" in out
    assert not calls


# ── romm_save_upload ─────────────────────────────────────────────────────────


async def test_save_upload_shaping_slot_and_overwrite(calls, tmp_path):
    save_file = tmp_path / "game.sav"
    save_file.write_bytes(b"SAVEDATA")
    calls.uploads_responses.append({"id": 3})
    out = await server.romm_save_upload(
        5, str(save_file), emulator="snes9x", slot="2", overwrite=True)
    req = calls.uploads[0]
    assert req["method"] == "POST"
    assert req["path"] == "saves"
    assert req["params"] == {"rom_id": 5, "emulator": "snes9x",
                             "slot": "2", "overwrite": True}
    assert "autocleanup" not in req["params"]
    assert req["long_timeout"] is True
    assert set(req["files"]) == {"saveFile"}
    assert req["files"]["saveFile"] == (
        "game.sav", b"SAVEDATA", "application/octet-stream")
    assert "Uploaded save file game.sav for ROM 5 (save id: 3)." in out


async def test_save_upload_omits_optional_params_by_default(calls, tmp_path):
    save_file = tmp_path / "game.sav"
    save_file.write_bytes(b"S")
    calls.uploads_responses.append({"id": 4})
    out = await server.romm_save_upload(5, str(save_file))
    req = calls.uploads[0]
    assert req["params"] == {"rom_id": 5}
    assert "slot" not in req["params"]
    assert "overwrite" not in req["params"]
    assert "autocleanup" not in req["params"]
    assert set(req["files"]) == {"saveFile"}
    assert "(save id: 4)" in out


async def test_save_upload_uses_screenshotfile_field(calls, tmp_path):
    save_file = tmp_path / "game.sav"
    save_file.write_bytes(b"S")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"PNG")
    calls.uploads_responses.append({"id": 5})
    out = await server.romm_save_upload(
        5, str(save_file), screenshot_path=str(shot))
    req = calls.uploads[0]
    assert set(req["files"]) == {"saveFile", "screenshotFile"}
    assert req["files"]["screenshotFile"][0] == "shot.png"
    assert "Uploaded save file game.sav for ROM 5" in out


async def test_save_upload_missing_file_makes_no_request(calls, tmp_path):
    out = await server.romm_save_upload(5, str(tmp_path / "nope.sav"))
    assert "File not found" in out
    assert not calls.uploads


async def test_save_upload_missing_screenshot_makes_no_request(calls, tmp_path):
    save_file = tmp_path / "game.sav"
    save_file.write_bytes(b"S")
    out = await server.romm_save_upload(
        5, str(save_file), screenshot_path=str(tmp_path / "nope.png"))
    assert "Screenshot file not found" in out
    assert not calls.uploads


# ── romm_save_delete / romm_save_favorite / romm_save_update ─────────────────


async def test_save_delete_posts_ids_body(calls):
    calls.responses.append({})
    out = await server.romm_save_delete(3)
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "saves/delete"
    assert calls[0]["json"] == {"saves": [3]}
    assert "Deleted save file 3." in out


def test_save_delete_docstring_is_destructive():
    doc = server.romm_save_delete.__doc__
    assert doc.startswith("Permanently delete")
    assert "cannot be undone" in doc


async def test_save_favorite_true(calls):
    calls.responses.append({})
    out = await server.romm_save_favorite(3)
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "saves/3/favorite"
    assert calls[0]["json"] == {"is_favorite": True}
    assert "Save file 3: added to favorites." in out


async def test_save_favorite_false(calls):
    calls.responses.append({})
    out = await server.romm_save_favorite(3, favorite=False)
    assert calls[0]["json"] == {"is_favorite": False}
    assert "Save file 3: removed from favorites." in out


async def test_save_update_single_field_sends_one_put(calls):
    calls.responses.append({})
    out = await server.romm_save_update(3, is_public=False)
    assert len(calls) == 1
    assert calls[0]["path"] == "saves/3/visibility"
    assert calls[0]["json"] == {"is_public": False}
    assert "Save file 3: updated is_public." in out


async def test_save_update_all_fields_in_fixed_order(calls):
    calls.responses.extend([{}, {}, {}])
    out = await server.romm_save_update(
        3, labels=["a"], is_public=True, file_name="y.sav")
    assert [c["path"] for c in calls] == [
        "saves/3/labels", "saves/3/visibility", "saves/3/file-name"]
    assert [c["json"] for c in calls] == [
        {"labels": ["a"]}, {"is_public": True}, {"file_name": "y.sav"}]
    assert "Save file 3: updated labels, is_public, file_name." in out


async def test_save_update_noop_makes_no_request(calls):
    out = await server.romm_save_update(3)
    assert "Nothing to update" in out
    assert not calls


async def test_save_update_blank_file_name_makes_no_request(calls):
    out = await server.romm_save_update(3, file_name="  ")
    assert "file_name must not be empty" in out
    assert not calls


# ── romm_saves slot/device extension ─────────────────────────────────────────


async def test_saves_adds_slot_and_device_filters(calls):
    calls.responses.append([])
    await server.romm_saves(slot="2", device_id="abc")
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "saves"
    assert calls[0]["params"] == {"slot": "2", "device_id": "abc"}


async def test_saves_omits_empty_slot_and_device(calls):
    calls.responses.append([])
    out = await server.romm_saves()
    assert calls[0]["params"] == {}
    assert "No saves found." in out
