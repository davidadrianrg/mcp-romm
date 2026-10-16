# Changelog

## 1.4.0 — 2026-10-16

Server-side metadata exports, memory cards, recommendations and notifications —
16 new tools (69 total: 39 read + 30 write).

### Added

- **Server-side metadata exports:** `romm_export_gamelist` and
  `romm_export_pegasus` trigger RomM to write `gamelist.xml` (resp.
  `metadata.pegasus.txt`) inside the platform directories on the RomM server
  itself; nothing is downloaded to the client.
- **Memory cards (RomM 5.x):** `romm_memory_cards` (list, optional emulator
  filter), `romm_memory_card_detail`, `romm_memory_card_versions`,
  `romm_memory_card_download` (latest or a specific version), and the write
  tools `romm_memory_card_version_upload` (the broker-layout zip only — loose
  `.ps2`/`.raw` images are rejected), `romm_memory_card_update`
  (name/visibility), and `romm_memory_card_delete`.
- **Recommendations (RomM 5.x):** `romm_recommendations` (with optional
  refresh), `romm_random_rom` (optional single-platform filter), and
  `romm_similar_roms` — each showing the game, its score, and the reasons.
- **Notifications (RomM 5.0+):** `romm_notifications` (newest first),
  `romm_notify`, `romm_notifications_read` (all, or specific IDs), and
  `romm_notification_dismiss` (one, or all with id 0).
- `platforms.write` (the two server-side exports) and `me.write` (your own
  notifications) added to the requested OAuth scopes; the scope set is
  otherwise unchanged.

### Changed

- `_validate_path_id` now caps path ids at 128 characters (SEC-008 residual
  hardening); the existing charset and dot-segment rules and their messages are
  unchanged.
- Display strings for `romm_task_detail` and `romm_virtual_collection_detail`
  now echo the normalized id rather than the raw argument.
- `smoke_test.py` gains three read-only live checks: memory cards,
  recommendations, and notifications.
- Version bumped to 1.4.0.

## 1.3.0 — 2026-10-16

Save-state and save-file management plus RomM 5.x scan triggering — 13 new tools
(53 total: 31 read + 22 write).

### Added

- **Save states (RomM 5.x):** `romm_states` (list by ROM or platform),
  `romm_state_detail`, `romm_state_download`, and the write tools
  `romm_state_upload`, `romm_state_delete`, `romm_state_favorite`,
  `romm_state_update`.
- **Advanced save files (RomM 5.x):** `romm_save_download`, `romm_save_upload`,
  `romm_save_delete`, `romm_save_favorite`, `romm_save_update`.
- **Tasks:** `romm_task_detail` — a background task's status, timing, and
  result.
- `romm_saves` now also filters by `slot` and `device_id`.
- `assets.write` added to the requested OAuth scopes, for save-state and save
  uploads, deletes, and property updates only.
- Binary download support (`romm_state_download`, `romm_save_download`) and
  multipart upload support (`romm_state_upload`, `romm_save_upload`), with
  Content-Disposition filename handling, overwrite protection, and the long
  request timeout.
- CI now also runs on pushes to `master`.

### Changed

- `romm_scan_library` now triggers scans through `POST /api/tasks/scan`
  (RomM 5.x; quick by default, or full) and reports the task execution
  response, with clear messages when a scan is already in progress (409) or no
  task worker is available (503).
- Hardened id validation for `romm_task_detail` and
  `romm_virtual_collection_detail` against path/query injection.
- Version bumped to 1.3.0.

## 1.2.1

### Added

- Optional HTTP transport for remote MCP clients: set `ROMM_MCP_TRANSPORT=http`
  (with `ROMM_MCP_HOST`/`ROMM_MCP_PORT`, default `127.0.0.1:8765`) to serve the
  MCP over Streamable HTTP instead of stdio — e.g. one shared instance on a
  homelab reachable from every machine. No auth of its own: bind to trusted
  networks only. Dockerfile documents the pattern and exposes 8765.

## 1.2.0

Full RomM 5.0 feature coverage — 12 new tools (40 total), all within the
existing OAuth scope set (no new permissions requested) and the same security
boundary: your own user data and collections only.

### Added

- **Play tracking (RomM 5.0+):** `romm_activity` (who played what, when),
  `romm_play_sessions` (your session history with durations),
  `romm_log_play_session` (record a session), `romm_delete_play_session`.
- **Virtual collections (RomM 5.0+):** `romm_virtual_collections` (automatic
  groupings by genre/franchise/collection/company/mode) and
  `romm_virtual_collection_detail`.
- **Smart collections (RomM 5.0+):** `romm_smart_collection_detail` (rules +
  matching ROMs), `romm_create_smart_collection`,
  `romm_update_smart_collection`, `romm_delete_smart_collection`. Filter
  criteria are validated against the key set RomM's handler actually reads —
  RomM itself silently ignores unknown keys, which would otherwise turn a typo
  into a filter that doesn't do what it says.
- **Identity:** `romm_whoami` — authenticated user, role, and (on 5.0) the
  effective permission grants from the new permissions engine.
- **Metadata:** `romm_metadata_search` — search IGDB/MobyGames/etc. for
  matches for a ROM (read-only; does not change the match).
- `romm_smart_collections` listing now shows ROM counts and rule summaries.

## 1.1.0

RomM 5.0 compatibility release, audited against a live 5.0.0 instance's
OpenAPI spec. All previously used endpoints, scopes, and the OAuth password
grant survive 5.0 unchanged; this release fixes the response-schema drift.

### Fixed (RomM 5.0 schema changes)

- `romm_collections` showed "0 ROMs" for every collection — 5.0 replaced the
  embedded `roms` list with `rom_count`/`rom_ids`. Now reads `rom_count`,
  falling back to the 4.x embedded list.
- `romm_collection_detail` listed no members on 5.0 — the collection detail no
  longer embeds ROMs. Members are now fetched via `GET /api/roms?collection_id=`
  (falls back to the 4.x embedded list).
- `romm_get_item` favorite flag — 5.0 dropped `rom_user.is_favorite`, and the
  `is_favorite` field embedded in `user_collections` is always null (verified
  live on 5.0.0). The flag is now derived by resolving the favorites collection
  and id-matching it in `user_collections` (falls back to the 4.x field).
- `romm_get_item` inline note — 5.0 dropped `rom_user.note_raw_markdown`; when
  `has_notes` is set, the output now points to `romm_rom_notes`.
- `romm_saves` and `romm_devices` displayed blank fields on 5.0 — saves now
  show emulator/slot/rom_id (5.0) alongside the 4.x rom_name/platform fields;
  devices show client/platform/sync state with the 4.x `type` fallback.

### Added

- `romm_search_by_hash` accepts `ra_hash` (RetroAchievements hash, RomM 5.0+).
- `romm_library_items` and `romm_search` report the library-wide `total` match
  count, not just the page size.
- `romm_tasks` now also lists the task registry (RomM 5.0's `GET /api/tasks`):
  each task's schedule, enabled state, and whether it can be run manually.
- `romm_scan_library` explains RomM 5.0's refusal of manually triggered scans
  (`scan_library` is `manual_run: false` there) instead of surfacing a raw 400.
- `smoke_test.py` is now a full live e2e suite: all 28 tools against a real
  instance, with self-cleaning write fixtures and a `ROMM_SMOKE_SKIP` knob for
  endpoints that are broken server-side. Running it against RomM 5.0.0 found
  three server-side bugs, documented in the README's Known issues section.

### Changed

- Documented prerequisites corrected: RomM 5.0+ (most read tools work on
  4.4+ — the `favorite` filter param only exists since 4.4.0), and an ordinary
  enabled user account (the old "admin role" claim was wrong). Documented RomM
  5.0's role collapse (`viewer`/`editor`/`admin` → `user`/`admin` + permissions
  engine) and its 403-troubleshooting implication.

## 1.0.0

First stable release. The server now both reads and writes: in addition to the
19 read tools, it can modify your own RomM user data and collections. Write
support has been smoke-tested end-to-end against a live RomM instance.

### Added

- **Write tools (9):**
  - `romm_set_status` — set play status (incomplete/finished/completed_100/
    retired/never_playing), backlog, now-playing, rating, completion, and the
    last-played timestamp.
  - `romm_favorite` — add/remove a ROM from your favorites (RomM models
    favorites as a collection; the tool finds or creates it).
  - `romm_add_note`, `romm_update_note`, `romm_delete_note` — manage your ROM notes.
  - `romm_create_collection`, `romm_add_to_collection`,
    `romm_remove_from_collection`, `romm_delete_collection` — manage collections.
- Test suite (`test_server.py`) and a CI workflow running ruff + pytest on 3.10 and 3.12.
- `[build-system]` and explicit `py-modules` in `pyproject.toml`.

### Changed

- **Least-privilege OAuth scopes.** The token now requests only the read scopes
  the tools use plus `roms.user.write`, `collections.write`, and `tasks.run`.
  Previously it requested a full write/edit scope set (`roms.write`,
  `platforms.write`, `users.write`, etc.) that no tool used.
- Security documentation rewritten to describe the actual write surface instead
  of claiming the server is fully read-only.

### Fixed

- `romm_rom_notes` read the wrong response fields (`raw_markdown`/`body`) and so
  showed no note text; it now reads `title`/`content` and shows author and
  visibility.

## 0.1.0

Initial release — 19 read-only tools for browsing platforms, searching ROMs,
viewing metadata, collections, saves, and tasks.
