"""Unit tests for the RomM MCP server.

No live RomM instance required — HTTP is stubbed at the _request boundary, so
these tests verify tool logic, request shaping, scope hygiene, and formatting.

Run:
    uv run pytest test_server.py -v
"""

import json
import os
from urllib.parse import parse_qs

import httpx
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
    # The write scopes the tools actually use, plus task running: user data,
    # collections, save states/files/memory cards, the server-side
    # gamelist/pegasus exports (platforms.write) and your own notifications
    # (me.write).
    assert "roms.user.write" in scopes
    assert "collections.write" in scopes
    assert "assets.write" in scopes
    assert "platforms.write" in scopes
    assert "me.write" in scopes
    assert "tasks.run" in scopes


def test_scopes_exclude_dangerous_writes():
    scopes = set(server._DEFAULT_SCOPES.split())
    for forbidden in ("roms.write", "firmware.write", "users.write",
                      "devices.write"):
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


# ── romm_virtual_collection_detail ───────────────────────────────────────────

# SEC-007: virtual_id is interpolated as one URL path segment. Hostile ids that
# could smuggle an extra path segment, query or fragment are rejected with this
# exact wording and no request on any HTTP seam.
INVALID_VIRTUAL_ID_MSG = "virtual_id contains invalid characters."


@pytest.mark.parametrize("virtual_id", [
    "1/../../users/me",
    "x?foo=bar",
    ".",
    "..",
    "a#frag",
    "a%2e",
    "a\\b",
    "a b",
    "a\tb",
    "a\nb",
    "a\x00b",
    "../../users/me",
])
async def test_virtual_collection_detail_rejects_hostile_ids(calls, virtual_id):
    out = await server.romm_virtual_collection_detail(virtual_id)
    assert out == INVALID_VIRTUAL_ID_MSG
    assert not calls
    assert not calls.bytes
    assert not calls.uploads


@pytest.mark.parametrize("virtual_id", [
    "genre:Action",
    "abc123",
    "genre_1",
    "collection.2024",
    "...",
])
async def test_virtual_collection_detail_valid_id_is_single_path_segment(
        calls, virtual_id):
    # Legitimate ids (":" is allowed by design, as are dots and underscores)
    # still resolve to exactly one path segment with no smuggled query params.
    calls.responses.append({"id": virtual_id, "name": "Action", "type": "genre",
                            "rom_count": 0})
    calls.responses.append({"items": []})
    out = await server.romm_virtual_collection_detail(virtual_id)
    assert len(calls) == 2
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == f"collections/virtual/{virtual_id}"
    assert calls[0]["params"] is None
    assert calls[1]["path"] == "roms"
    assert calls[1]["params"]["virtual_collection_id"] == virtual_id
    assert "Action (genre)" in out


async def test_virtual_collection_detail_blank_id_uses_pre_existing_path(calls):
    # Pre-existing behaviour, unchanged by SEC-007 and deliberately not changed:
    # a blank id is interpolated as-is, issuing GET collections/virtual/. Pinned
    # so any future drift in this edge is visible.
    out = await server.romm_virtual_collection_detail("")
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "collections/virtual/"
    assert "not found" in out


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


# ── Shared: repeatable query-param encoding ─────────────────────────────────


def test_multi_value_params_encode_as_repeated_keys():
    # Contract for the repeatable platform_ids query param (export + random-ROM
    # tools): a dict whose value is a list must flatten to repeated keys at the
    # real httpx encoder. No network: the Request is built but never sent.
    request = httpx.Request(
        "POST", "https://romm.example/api/export/gamelist-xml",
        params={"platform_ids": [3, 7], "local_export": True},
    )
    # httpx 0.28.1 encodes boolean params lowercase ("true"). request.url.query
    # is bytes, so decode it before parse_qs to compare against str values.
    assert parse_qs(request.url.query.decode()) == {
        "platform_ids": ["3", "7"], "local_export": ["true"],
    }


# ── romm_export_gamelist / romm_export_pegasus ──────────────────────────────

EMPTY_PLATFORM_IDS_MSG = "platform_ids must contain at least one platform ID."
NON_POSITIVE_PLATFORM_IDS_MSG = "platform_ids must be positive integers."


async def test_export_gamelist_shaping_default(calls):
    out = await server.romm_export_gamelist([3, 7])
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "export/gamelist-xml"
    assert req["params"] == {"platform_ids": [3, 7]}
    assert req["json"] is None
    assert "gamelist.xml" in out
    assert "on the RomM server" in out
    assert "Nothing was downloaded" in out


async def test_export_gamelist_local_export_flag(calls):
    await server.romm_export_gamelist([3, 7], local_export=True)
    assert calls[0]["params"] == {"platform_ids": [3, 7], "local_export": True}
    # The default call never includes the flag at all (not even as False).
    await server.romm_export_gamelist([3, 7])
    assert "local_export" not in calls[1]["params"]


async def test_export_gamelist_empty_platform_ids_no_request(calls):
    out = await server.romm_export_gamelist([])
    assert out == EMPTY_PLATFORM_IDS_MSG
    assert not calls and not calls.bytes and not calls.uploads


async def test_export_gamelist_rejects_non_positive_ids_no_request(calls):
    for bad in ([0], [-2]):
        out = await server.romm_export_gamelist(bad)
        assert out == NON_POSITIVE_PLATFORM_IDS_MSG
    assert not calls and not calls.bytes and not calls.uploads


async def test_export_gamelist_api_error_propagates(calls):
    calls.responses.append(RuntimeError("API error 400: bad"))
    with pytest.raises(RuntimeError):
        await server.romm_export_gamelist([3])


def test_export_docstrings_state_server_side_write():
    # Normalize whitespace: docstrings wrap across lines.
    gamelist = " ".join(server.romm_export_gamelist.__doc__.split())
    pegasus = " ".join(server.romm_export_pegasus.__doc__.split())
    assert "gamelist.xml" in gamelist
    assert "ON the RomM server" in gamelist
    assert "Nothing is downloaded to the client" in gamelist
    assert "metadata.pegasus.txt" in pegasus
    assert "ON the RomM server" in pegasus
    assert "Nothing is downloaded to the client" in pegasus


async def test_export_pegasus_shaping_default(calls):
    out = await server.romm_export_pegasus([3, 7])
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "export/pegasus"
    assert req["params"] == {"platform_ids": [3, 7]}
    assert req["json"] is None
    assert "metadata.pegasus.txt" in out


async def test_export_pegasus_local_export_flag(calls):
    await server.romm_export_pegasus([9], local_export=True)
    assert calls[0]["params"] == {"platform_ids": [9], "local_export": True}


async def test_export_pegasus_empty_platform_ids_no_request(calls):
    out = await server.romm_export_pegasus([])
    assert out == EMPTY_PLATFORM_IDS_MSG
    assert not calls and not calls.bytes and not calls.uploads


# ── romm_memory_cards ───────────────────────────────────────────────────────

CARD_ID_MSG = "card_id must be positive."


async def test_memory_cards_shaping_and_fields(calls):
    calls.responses.append([
        {"id": 7, "name": "Slot A", "emulator": "pcsx2",
         "user": {"username": "j"}, "is_public": False, "file_size_bytes": 2048,
         "created_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-02-01T00:00:00Z"},
        {"id": 9},
    ])
    out = await server.romm_memory_cards()
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "memory-cards"
    assert calls[0]["params"] == {}
    assert "Memory cards (2):" in out
    assert "  - Slot A [pcsx2]" in out
    assert "    Owner: j" in out
    assert "    Public: no" in out
    assert "    Size: 2 KB" in out
    assert "    Created: 2026-01-01T00:00:00Z" in out
    assert "    Updated: 2026-02-01T00:00:00Z" in out
    assert "    ID: 7" in out
    # A card missing every optional field renders the name fallback + ID only.
    assert out.splitlines()[-1] == "    ID: 9"
    assert "  - Unnamed card" in out


async def test_memory_cards_emulator_filter(calls):
    calls.responses.append([])
    await server.romm_memory_cards(emulator="pcsx2")
    assert calls[0]["params"] == {"emulator": "pcsx2"}


async def test_memory_cards_empty_message(calls):
    calls.responses.extend([[], []])
    out = await server.romm_memory_cards()
    assert out == "No memory cards found."
    out = await server.romm_memory_cards(emulator="pcsx2")
    assert out == "No memory cards found for emulator pcsx2."


async def test_memory_cards_accepts_items_dict(calls):
    calls.responses.append({"items": [{"id": 1, "name": "A"}]})
    out = await server.romm_memory_cards()
    assert "  - A" in out
    assert "    ID: 1" in out


# ── romm_memory_card_detail ─────────────────────────────────────────────────


async def test_memory_card_detail_shaping_and_fields(calls):
    calls.responses.append({
        "id": 7, "name": "Slot A", "file_name": "slotA.ps2",
        "emulator": "pcsx2", "user": {"username": "j"}, "is_public": True,
        "file_size_bytes": 2048, "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z", "version_count": 3,
    })
    out = await server.romm_memory_card_detail(7)
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "memory-cards/7"
    assert "Memory card 7:" in out
    assert "  Name: Slot A" in out
    assert "  File: slotA.ps2" in out
    assert "  Emulator: pcsx2" in out
    assert "  Owner: j" in out
    assert "  Public: yes" in out
    assert "  Size: 2 KB" in out
    assert "  Created: 2026-01-01T00:00:00Z" in out
    assert "  Updated: 2026-02-01T00:00:00Z" in out
    assert "  Versions: 3" in out
    assert out.splitlines()[-1] == "  ID: 7"


async def test_memory_card_detail_omits_absent_fields(calls):
    calls.responses.append({"id": 8})
    out = await server.romm_memory_card_detail(8)
    assert out.splitlines() == ["Memory card 8:", "  ID: 8"]


async def test_memory_card_detail_not_found(calls):
    calls.responses.extend([{}, []])
    assert await server.romm_memory_card_detail(7) == "Memory card 7 not found."
    assert await server.romm_memory_card_detail(7) == "Memory card 7 not found."


async def test_memory_card_detail_rejects_bad_id(calls):
    for bad in (0, -1):
        assert await server.romm_memory_card_detail(bad) == CARD_ID_MSG
    assert not calls and not calls.bytes and not calls.uploads


# ── romm_memory_card_versions ───────────────────────────────────────────────


async def test_memory_card_versions_shaping_and_rows(calls):
    calls.responses.append([
        {"id": 11, "version": 3, "created_at": "2026-01-01T00:00:00Z",
         "file_name": "v3.zip", "file_size_bytes": 4096},
    ])
    out = await server.romm_memory_card_versions(7)
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "memory-cards/7/versions"
    assert "Memory card 7 versions (1):" in out
    assert "  1. Version 3" in out
    assert "    Created: 2026-01-01T00:00:00Z" in out
    assert "    File: v3.zip" in out
    assert "    Size: 4 KB" in out
    assert "    ID: 11" in out


async def test_memory_card_versions_empty(calls):
    calls.responses.append([])
    out = await server.romm_memory_card_versions(7)
    assert out == "No versions recorded for memory card 7."


async def test_memory_card_versions_rejects_bad_id(calls):
    for bad in (0, -1):
        assert await server.romm_memory_card_versions(bad) == CARD_ID_MSG
    assert not calls and not calls.bytes and not calls.uploads


# ── romm_memory_card_download ───────────────────────────────────────────────

VERSION_ID_MSG = "version_id must be 0 (latest) or a positive ID."


async def test_memory_card_download_latest_shaping(calls, tmp_path):
    calls.bytes_responses.append((b"BIN", {}))
    target = tmp_path / "out.bin"
    out = await server.romm_memory_card_download(7, str(target))
    req = calls.bytes[0]
    assert req["method"] == "GET"
    assert req["path"] == "memory-cards/7/content"
    assert req["params"] is None
    assert req["long_timeout"] is True
    assert target.read_bytes() == b"BIN"
    assert f"Saved memory card 7 to {target} (3 B)." in out


async def test_memory_card_download_version_routing(calls, tmp_path):
    calls.bytes_responses.append((b"V3", {}))
    out = await server.romm_memory_card_download(7, str(tmp_path), version_id=3)
    assert calls.bytes[0]["path"] == "memory-cards/versions/3/content"
    assert (tmp_path / "memory_card_7_v3.bin").read_bytes() == b"V3"
    assert "version 3" in out


async def test_memory_card_download_default_name_fallback(calls, tmp_path):
    calls.bytes_responses.append((b"X", {}))
    out = await server.romm_memory_card_download(7, str(tmp_path))
    assert (tmp_path / "memory_card_7.bin").read_bytes() == b"X"
    assert str(tmp_path / "memory_card_7.bin") in out


async def test_memory_card_download_refuses_existing_file(calls, tmp_path):
    target = tmp_path / "keep.bin"
    target.write_bytes(b"ORIGINAL")
    calls.bytes_responses.append((b"NEWDATA", {}))
    out = await server.romm_memory_card_download(7, str(target))
    assert "File already exists" in out
    assert "overwrite=True" in out
    assert target.read_bytes() == b"ORIGINAL"


async def test_memory_card_download_overwrite_replaces(calls, tmp_path):
    target = tmp_path / "keep.bin"
    target.write_bytes(b"OLD")
    calls.bytes_responses.append((b"NEW", {}))
    out = await server.romm_memory_card_download(7, str(target), overwrite=True)
    assert target.read_bytes() == b"NEW"
    assert f"Saved memory card 7 to {target} (3 B)." in out


async def test_memory_card_download_rejects_bad_ids(calls, tmp_path):
    target = str(tmp_path / "none.bin")
    assert await server.romm_memory_card_download(0, target) == CARD_ID_MSG
    assert await server.romm_memory_card_download(7, target, version_id=-1) == (
        VERSION_ID_MSG)
    assert not calls.bytes and not calls and not calls.uploads


# ── romm_memory_card_version_upload ─────────────────────────────────────────

BROKER_ZIP_MSG = ("Memory card versions accept only the broker-layout zip; "
                  "loose .ps2/.raw images are rejected.")


async def test_memory_card_version_upload_shaping(calls, tmp_path):
    card_file = tmp_path / "card.zip"
    card_file.write_bytes(b"ZIP")
    calls.uploads_responses.append({"id": 11})
    out = await server.romm_memory_card_version_upload(7, str(card_file))
    req = calls.uploads[0]
    assert req["method"] == "POST"
    assert req["path"] == "memory-cards/7/versions"
    assert req["params"] is None
    assert req["long_timeout"] is True
    assert req["files"] == {
        "cardFile": ("card.zip", b"ZIP", "application/octet-stream")}
    assert "(version id: 11)" in out


async def test_memory_card_version_upload_missing_file_no_request(calls, tmp_path):
    missing = str(tmp_path / "nope.zip")
    out = await server.romm_memory_card_version_upload(7, missing)
    assert out == f"File not found: {missing}."
    assert not calls.uploads and not calls and not calls.bytes


async def test_memory_card_version_upload_rejects_loose_ps2(calls, tmp_path):
    # Case-insensitive: ".PS2" is rejected too.
    loose = tmp_path / "memory.PS2"
    loose.write_bytes(b"raw")
    out = await server.romm_memory_card_version_upload(7, str(loose))
    assert out == BROKER_ZIP_MSG
    assert not calls.uploads and not calls and not calls.bytes


async def test_memory_card_version_upload_rejects_loose_raw(calls, tmp_path):
    loose = tmp_path / "memory.raw"
    loose.write_bytes(b"raw")
    out = await server.romm_memory_card_version_upload(7, str(loose))
    assert out == BROKER_ZIP_MSG
    assert not calls.uploads and not calls and not calls.bytes


async def test_memory_card_version_upload_rejects_bad_card_id(calls, tmp_path):
    card_file = tmp_path / "card.zip"
    card_file.write_bytes(b"ZIP")
    out = await server.romm_memory_card_version_upload(0, str(card_file))
    assert out == CARD_ID_MSG
    assert not calls.uploads


# ── romm_memory_card_update / romm_memory_card_delete ───────────────────────

NO_UPDATE_MSG = "Nothing to update — provide a field to change."


async def test_memory_card_update_name_only_one_put(calls):
    calls.responses.append({})
    out = await server.romm_memory_card_update(7, name="Slot A")
    assert len(calls) == 1
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "memory-cards/7"
    assert calls[0]["json"] == {"name": "Slot A"}
    assert "updated name" in out


async def test_memory_card_update_visibility_only_one_put(calls):
    calls.responses.append({})
    out = await server.romm_memory_card_update(7, is_public=False)
    assert len(calls) == 1
    assert calls[0]["path"] == "memory-cards/7/visibility"
    assert calls[0]["json"] == {"is_public": False}
    assert "updated is_public" in out


async def test_memory_card_update_both_fixed_order(calls):
    calls.responses.extend([{}, {}])
    out = await server.romm_memory_card_update(7, name="Slot A", is_public=True)
    assert [c["path"] for c in calls] == [
        "memory-cards/7", "memory-cards/7/visibility"]
    assert [c["json"] for c in calls] == [
        {"name": "Slot A"}, {"is_public": True}]
    assert "updated name, is_public" in out


async def test_memory_card_update_noop_no_request(calls):
    out = await server.romm_memory_card_update(7)
    assert out == NO_UPDATE_MSG
    assert not calls and not calls.bytes and not calls.uploads


async def test_memory_card_update_blank_name_treated_as_not_given(calls):
    out = await server.romm_memory_card_update(7, name="   ")
    assert out == NO_UPDATE_MSG
    assert not calls and not calls.bytes and not calls.uploads


async def test_memory_card_delete_posts_cards_body(calls):
    calls.responses.append({})
    out = await server.romm_memory_card_delete(7)
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "memory-cards/delete"
    assert calls[0]["json"] == {"cards": [7]}
    assert out == "Deleted memory card 7."


def test_memory_card_delete_docstring_is_destructive():
    doc = server.romm_memory_card_delete.__doc__
    assert doc.startswith("Permanently")
    assert "cannot be undone" in doc


async def test_memory_card_delete_rejects_bad_id(calls):
    out = await server.romm_memory_card_delete(0)
    assert out == CARD_ID_MSG
    assert not calls


# ── romm_recommendations / romm_random_rom / romm_similar_roms ──────────────


async def test_recommendations_shaping_default(calls):
    calls.responses.append([])
    await server.romm_recommendations()
    req = calls[0]
    assert req["method"] == "GET"
    assert req["path"] == "recommendations"
    assert req["params"] == {"limit": 20}
    assert "refresh" not in req["params"]


async def test_recommendations_refresh_flag(calls):
    calls.responses.append([])
    await server.romm_recommendations(refresh=True)
    assert calls[0]["params"] == {"limit": 20, "refresh": True}


async def test_recommendations_limit_clamped(calls):
    # Clamp, not reject (consistent with romm_states/romm_play_sessions).
    calls.responses.extend([[], []])
    await server.romm_recommendations(limit=0)
    await server.romm_recommendations(limit=999)
    assert calls[0]["params"] == {"limit": 1}
    assert calls[1]["params"] == {"limit": 50}


async def test_recommendations_rows_show_game_score_reasons(calls):
    calls.responses.append([
        {"rom": {"id": 5, "name": "Super Metroid", "platform_slug": "snes"},
         "score": 0.91, "reasons": ["a", "b"]},
    ])
    out = await server.romm_recommendations()
    assert "Recommendations (1):" in out
    assert "Super Metroid" in out
    assert "Score: 0.91" in out
    assert "Reasons: a; b" in out


async def test_recommendations_flat_item_shape(calls):
    calls.responses.append(
        [{"id": 5, "name": "Flat ROM", "platform_slug": "snes"}])
    out = await server.romm_recommendations()
    assert "Flat ROM" in out
    assert "   ID: 5" in out


async def test_recommendations_empty(calls):
    calls.responses.append([])
    out = await server.romm_recommendations()
    assert out == "No recommendations available."


async def test_random_rom_default_no_params(calls):
    calls.responses.append({"id": 5, "name": "R"})
    await server.romm_random_rom()
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "roms/random"
    assert calls[0]["params"] == {}


async def test_random_rom_platform_filter(calls):
    calls.responses.append({"id": 5, "name": "R"})
    await server.romm_random_rom(platform_id=5)
    assert calls[0]["params"] == {"platform_ids": [5]}


async def test_random_rom_null_response(calls):
    calls.responses.extend([None, {}])
    assert await server.romm_random_rom() == "No random ROM found (no candidates)."
    assert await server.romm_random_rom() == "No random ROM found (no candidates)."


async def test_random_rom_rejects_negative_platform_id(calls):
    out = await server.romm_random_rom(platform_id=-1)
    assert out == "platform_id must be 0 (all) or a positive ID."
    assert not calls and not calls.bytes and not calls.uploads


async def test_random_rom_renders_rom_line(calls):
    calls.responses.append(
        {"id": 5, "name": "Chrono Trigger", "platform_slug": "snes"})
    out = await server.romm_random_rom()
    assert "Random pick:" in out
    assert "Chrono Trigger" in out
    assert "   ID: 5" in out


async def test_similar_roms_shaping(calls):
    calls.responses.append([])
    await server.romm_similar_roms(42)
    req = calls[0]
    assert req["method"] == "GET"
    assert req["path"] == "roms/42/similar"
    assert req["params"] == {"limit": 12}


async def test_similar_roms_limit_clamped(calls):
    calls.responses.append([])
    await server.romm_similar_roms(42, limit=999)
    assert calls[0]["params"] == {"limit": 50}


async def test_similar_roms_rows_show_game_score_reasons(calls):
    calls.responses.append([
        {"rom": {"id": 9, "name": "Earthbound", "platform_slug": "snes"},
         "score": 0.77, "reasons": ["same genre"]},
    ])
    out = await server.romm_similar_roms(42)
    assert "Similar ROMs for ROM 42 (1):" in out
    assert "Earthbound" in out
    assert "Score: 0.77" in out
    assert "Reasons: same genre" in out


async def test_similar_roms_empty(calls):
    calls.responses.append([])
    out = await server.romm_similar_roms(42)
    assert out == "No similar ROMs found for ROM 42."


async def test_similar_roms_rejects_bad_rom_id(calls):
    out = await server.romm_similar_roms(0)
    assert out == "rom_id must be positive."
    assert not calls


# ── romm_notifications ──────────────────────────────────────────────────────


async def test_notifications_shaping(calls):
    calls.responses.append([])
    await server.romm_notifications()
    req = calls[0]
    assert req["method"] == "GET"
    assert req["path"] == "notifications"
    assert req["params"] is None


async def test_notifications_newest_first(calls):
    calls.responses.append([
        {"id": 1, "title": "OLDEST", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 2, "title": "NEWEST", "created_at": "2026-03-01T00:00:00Z"},
        {"id": 3, "title": "MIDDLE", "created_at": "2026-02-01T00:00:00Z"},
        {"id": 4, "title": "UNDATEDA"},
        {"id": 5, "title": "UNDATEDB"},
    ])
    out = await server.romm_notifications()
    assert out.index("NEWEST") < out.index("MIDDLE") < out.index("OLDEST")
    # Undated entries keep their API order and follow the dated ones.
    assert out.index("OLDEST") < out.index("UNDATEDA") < out.index("UNDATEDB")


async def test_notifications_render_fields_and_unread_marker(calls):
    calls.responses.append([
        {"id": 1, "title": "Warning", "level": "warning", "body": "x" * 250,
         "link": "https://example.com", "created_at": "2026-01-01T00:00:00Z",
         "is_read": False},
        {"id": 2, "title": "Read one", "level": "info", "is_read": True},
    ])
    out = await server.romm_notifications()
    assert "  - [WARNING] Warning (unread)" in out
    assert "  - [INFO] Read one" in out
    assert "[INFO] Read one (unread)" not in out
    assert "    " + "x" * 200 + "..." in out
    assert "x" * 201 not in out
    assert "    Link: https://example.com" in out
    assert "    2026-01-01T00:00:00Z" in out
    assert "    ID: 1" in out
    assert "    ID: 2" in out


async def test_notifications_empty(calls):
    calls.responses.append([])
    out = await server.romm_notifications()
    assert out == "No notifications."


# ── romm_notify / romm_notifications_read / romm_notification_dismiss ──────


async def test_notify_shaping_default(calls):
    calls.responses.append({"id": 5})
    out = await server.romm_notify("T")
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "notifications"
    assert req["json"] == {"title": "T", "body": "", "level": "info"}
    assert "kind" not in req["json"]
    assert "recipients" not in req["json"]
    assert "(id: 5)" in out


async def test_notify_link_when_given(calls):
    calls.responses.append({"id": 6})
    await server.romm_notify("T", link="https://example.com")
    assert calls[0]["json"] == {
        "title": "T", "body": "", "level": "info",
        "link": "https://example.com"}
    calls.responses.append({"id": 7})
    await server.romm_notify("T")
    assert "link" not in calls[1]["json"]


async def test_notify_empty_title_no_request(calls):
    for title in ("", "   "):
        assert await server.romm_notify(title) == "title is required."
    assert not calls and not calls.bytes and not calls.uploads


async def test_notify_invalid_level_no_request(calls):
    # Case-sensitive: 'INFO' is rejected too. Level is echoed in single quotes,
    # mirroring romm_set_status.
    out = await server.romm_notify("T", level="bogus")
    assert out == "Invalid level 'bogus'. Valid: info, success, warning, error."
    out = await server.romm_notify("T", level="INFO")
    assert out == "Invalid level 'INFO'. Valid: info, success, warning, error."
    assert not calls and not calls.bytes and not calls.uploads


async def test_notify_message_includes_id(calls):
    calls.responses.append({"id": 5})
    out = await server.romm_notify("Hello")
    assert out == 'Notification "Hello" created (id: 5).'


async def test_notifications_read_mark_some(calls):
    out = await server.romm_notifications_read([3, 7])
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "notifications/read"
    assert req["json"] == {"ids": [3, 7]}
    assert out == "Marked 2 notification(s) as read."


async def test_notifications_read_mark_all(calls):
    out = await server.romm_notifications_read()
    # The ids key is present with null on the wire (mark ALL as read).
    assert calls[0]["json"] == {"ids": None}
    assert "ids" in calls[0]["json"]
    assert out == "Marked all notifications as read."


async def test_notifications_read_empty_list_no_request(calls):
    out = await server.romm_notifications_read([])
    assert out == ("Provide at least one notification ID, or omit ids to mark "
                   "all as read.")
    assert not calls and not calls.bytes and not calls.uploads


async def test_notifications_read_rejects_non_positive_ids(calls):
    for bad in ([0], [-1]):
        assert await server.romm_notifications_read(bad) == (
            "notification IDs must be positive.")
    assert not calls and not calls.bytes and not calls.uploads


async def test_notification_dismiss_all(calls):
    calls.responses.append({})
    out = await server.romm_notification_dismiss(dismiss_all=True)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "notifications"
    assert calls[0]["json"] is None
    assert out == "Dismissed all notifications."


async def test_notification_dismiss_bare_call_refuses(calls):
    out = await server.romm_notification_dismiss()
    assert out.startswith("Nothing dismissed")
    assert "dismiss_all=True" in out
    assert not calls and not calls.bytes and not calls.uploads


async def test_notification_dismiss_one(calls):
    calls.responses.append({})
    out = await server.romm_notification_dismiss(7)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "notifications/7"
    assert out == "Dismissed notification 7."


def test_notification_dismiss_docstring_is_destructive():
    doc = server.romm_notification_dismiss.__doc__
    assert doc.startswith("Permanently")
    assert "cannot be undone" in doc
    assert "ALL" in doc


async def test_notification_dismiss_rejects_negative_id(calls):
    out = await server.romm_notification_dismiss(-1)
    assert out == "notification_id must be a positive ID or 0 with dismiss_all=True."
    assert not calls and not calls.bytes and not calls.uploads


# ── H1: _validate_path_id max_len ───────────────────────────────────────────


async def test_task_detail_max_len_rejects_129(calls):
    out = await server.romm_task_detail("a" * 129)
    assert out == "task_id must be at most 128 characters."
    assert not calls and not calls.bytes and not calls.uploads


async def test_task_detail_max_len_accepts_128(calls):
    calls.responses.append({"task_id": "a" * 128, "status": "complete"})
    out = await server.romm_task_detail("a" * 128)
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "tasks/" + "a" * 128
    assert "  status: complete" in out


async def test_virtual_collection_detail_max_len_rejects_129(calls):
    out = await server.romm_virtual_collection_detail("a" * 129)
    assert out == "virtual_id must be at most 128 characters."
    assert not calls and not calls.bytes and not calls.uploads


async def test_validate_path_id_message_precedence(calls):
    # Charset/dot checks run before the length check, so a long hostile id keeps
    # its charset message rather than being reported as over-length.
    hostile = "a" * 129 + "?"
    out = await server.romm_task_detail(hostile)
    assert out == INVALID_TASK_ID_MSG
    assert not calls and not calls.bytes and not calls.uploads


# ── H2: normalized id echoed in display strings ─────────────────────────────


async def test_task_detail_echoes_normalized_id(calls):
    calls.responses.append({})
    out = await server.romm_task_detail(" 42 ")
    assert "No task detail available for 42." in out
    assert " 42 " not in out
    calls.responses.append({"task_id": "42", "status": "complete"})
    out = await server.romm_task_detail(" 42 ")
    assert "Task 42:" in out
    assert len(calls) == 2


async def test_virtual_collection_detail_echoes_normalized_id(calls):
    calls.responses.append({})
    out = await server.romm_virtual_collection_detail(" genre:Action ")
    assert "Virtual collection genre:Action not found." in out
