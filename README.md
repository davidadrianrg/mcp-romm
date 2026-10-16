# RomM MCP Server

An [MCP](https://modelcontextprotocol.io) server for [RomM](https://github.com/rommapp/romm) — the self-hosted retro game library manager. 69 tools: 39 read-only for browsing platforms, searching ROMs, viewing metadata, collections (regular, smart, and virtual), saves, save states, memory cards, recommendations, play activity, notifications, and tasks — plus 30 write tools for play status, play sessions, favorites, notes, collection management, save states/save files/memory cards (upload, delete, favorite, and metadata), server-side metadata exports, and notifications, through any MCP-compatible AI assistant.

## Tools

### Read

| Tool | Description |
|------|-------------|
| `romm_status` | Check server configuration and reachability |
| `romm_stats` | Library-wide statistics (platforms, ROMs, saves, total size) |
| `romm_platforms` | List platforms with ROM counts and sizes |
| `romm_library_items` | Browse ROMs with filtering and pagination |
| `romm_recent` | Recently added or updated ROMs |
| `romm_get_item` | Full ROM detail — metadata, saves, user status |
| `romm_search` | Search ROMs by name |
| `romm_search_by_hash` | Identify a ROM by file hash (CRC, MD5, SHA1, or RetroAchievements) |
| `romm_filters` | Available filter values (genres, regions, languages, tags) |
| `romm_recommendations` | Personalized ROM recommendations from your play history (RomM 5.x) |
| `romm_random_rom` | Pick a random ROM, optionally restricted to one platform (RomM 5.x) |
| `romm_similar_roms` | ROMs similar to a given ROM, with scores and reasons (RomM 5.x) |
| `romm_collections` | List user-curated collections |
| `romm_collection_detail` | List ROMs in a specific collection |
| `romm_smart_collections` | List auto-generated smart collections |
| `romm_saves` | List save files by ROM, platform, slot, or device |
| `romm_states` | List save states by ROM or platform (RomM 5.x) |
| `romm_state_detail` | Full detail for a single save state (RomM 5.x) |
| `romm_state_download` | Download a save state to a local path (RomM 5.x) |
| `romm_save_download` | Download a save file to a local path (RomM 5.x) |
| `romm_memory_cards` | List memory cards, optionally filtered by emulator (RomM 5.x) |
| `romm_memory_card_detail` | Full detail for a single memory card (RomM 5.x) |
| `romm_memory_card_versions` | List saved versions of a memory card (RomM 5.x) |
| `romm_memory_card_download` | Download a memory card — latest or a specific version — to a local path (RomM 5.x) |
| `romm_user_profile` | Browse by status (now playing, backlog, completed, etc.) |
| `romm_rom_notes` | View notes on a ROM |
| `romm_firmware` | List BIOS/firmware files per platform |
| `romm_devices` | List registered devices |
| `romm_tasks` | List registered tasks (schedule, manual-run availability) and running status |
| `romm_scan_library` | Trigger a background library rescan (quick by default, or full) |
| `romm_task_detail` | Show a background task's status, timing, and result |
| `romm_activity` | Recent play activity feed — who played what, when (5.0+) |
| `romm_play_sessions` | List recorded play sessions with durations (5.0+) |
| `romm_virtual_collections` | Automatic groupings by genre/franchise/company/etc. (5.0+) |
| `romm_virtual_collection_detail` | List ROMs in a virtual collection (5.0+) |
| `romm_smart_collection_detail` | A smart collection's rules and matching ROMs (5.0+) |
| `romm_whoami` | Authenticated account, role, and effective permissions |
| `romm_metadata_search` | Search metadata providers (IGDB etc.) for ROM matches (5.0+) |
| `romm_notifications` | List your notifications, newest first (5.0+) |

### Write

These modify **your own** user data, collections, notifications, and save states, save files and memory cards (upload, delete, favorite, and metadata). The two `romm_export_*` tools write metadata files **inside the platform directories on the RomM server** — nothing is downloaded to the client. No tool alters ROM files, firmware, or other users.

| Tool | Description |
|------|-------------|
| `romm_set_status` | Set play status, backlog, now-playing, rating, completion, last-played |
| `romm_favorite` | Add or remove a ROM from your favorites |
| `romm_add_note` | Add a note to a ROM |
| `romm_update_note` | Edit an existing note |
| `romm_delete_note` | Delete a note (permanent) |
| `romm_create_collection` | Create a new collection |
| `romm_add_to_collection` | Add ROMs to a collection |
| `romm_remove_from_collection` | Remove ROMs from a collection |
| `romm_delete_collection` | Delete a collection — the grouping only, not the ROMs (permanent) |
| `romm_log_play_session` | Record a play session on a ROM (5.0+) |
| `romm_delete_play_session` | Delete one of your play sessions (permanent, 5.0+) |
| `romm_create_smart_collection` | Create a smart collection — a saved filter that auto-matches ROMs (5.0+) |
| `romm_update_smart_collection` | Edit a smart collection's name/description/rules (5.0+) |
| `romm_delete_smart_collection` | Delete a smart collection — the saved filter only (permanent, 5.0+) |
| `romm_state_upload` | Upload a save state (RomM 5.x) |
| `romm_state_delete` | Delete a save state (permanent) |
| `romm_state_favorite` | Add or remove a save state from favorites |
| `romm_state_update` | Edit a save state's labels, visibility, or file name |
| `romm_save_upload` | Upload a save file (RomM 5.x) |
| `romm_save_delete` | Delete a save file (permanent) |
| `romm_save_favorite` | Add or remove a save file from favorites |
| `romm_save_update` | Edit a save file's labels, visibility, or file name |
| `romm_export_gamelist` | Write `gamelist.xml` inside the platform directories on the RomM server (nothing downloaded to the client) |
| `romm_export_pegasus` | Write `metadata.pegasus.txt` inside the platform directories on the RomM server (nothing downloaded to the client) |
| `romm_memory_card_version_upload` | Upload a new version of your own memory card — broker-layout zip only, loose `.ps2`/`.raw` rejected (RomM 5.x) |
| `romm_memory_card_update` | Edit your own memory card's name or visibility (RomM 5.x) |
| `romm_memory_card_delete` | Delete your own memory card (permanent, RomM 5.x) |
| `romm_notify` | Create a notification for yourself (RomM 5.0+) |
| `romm_notifications_read` | Mark your own notifications as read — all, or specific IDs (RomM 5.0+) |
| `romm_notification_dismiss` | Dismiss your own notifications — one by ID, or all with dismiss_all=True (permanent, RomM 5.0+) |

## Setup

### Prerequisites

- Python 3.10+
- A running [RomM](https://github.com/rommapp/romm) instance (v5.0+; most read tools also work on v4.4+)
- An **enabled** RomM user account — admin is not required (see [Auth](#auth))

### Install

```bash
pip install fastmcp httpx
```

Or clone and install:

```bash
git clone https://github.com/lodordev/mcp-romm.git
cd mcp-romm
pip install .
```

### Configure

Set environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ROMM_URL` | No | `http://localhost:3000` | Your RomM instance URL |
| `ROMM_CLIENT_TOKEN` | No | | RomM client token (`rmm_...`, RomM 5.x), sent as the bearer credential directly, skipping the password grant. Set this **or** both `ROMM_USERNAME` and `ROMM_PASSWORD` |
| `ROMM_USERNAME` | **Yes\*** | | RomM username — required unless `ROMM_CLIENT_TOKEN` is set |
| `ROMM_PASSWORD` | **Yes\*** | | RomM password — required unless `ROMM_CLIENT_TOKEN` is set |
| `ROMM_REQUEST_TIMEOUT` | No | `30` | Default request timeout (seconds) |
| `ROMM_REQUEST_TIMEOUT_LONG` | No | `60` | Timeout for slow endpoints |
| `ROMM_TLS_VERIFY` | No | `true` | Verify TLS certificates |
| `ROMM_MCP_TRANSPORT` | No | `stdio` | `stdio` (client spawns the process) or `http` (serve remote MCP clients) |
| `ROMM_MCP_HOST` | No | `127.0.0.1` | Bind address for `http` transport |
| `ROMM_MCP_PORT` | No | `8765` | Port for `http` transport |

### Add to Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "romm": {
      "command": "python",
      "args": ["/path/to/mcp-romm/server.py"],
      "env": {
        "ROMM_URL": "http://your-romm-instance:3000",
        "ROMM_USERNAME": "your-username",
        "ROMM_PASSWORD": "your-password"
      }
    }
  }
}
```

### Add to Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "romm": {
      "command": "python",
      "args": ["/path/to/mcp-romm/server.py"],
      "env": {
        "ROMM_URL": "http://your-romm-instance:3000",
        "ROMM_USERNAME": "your-username",
        "ROMM_PASSWORD": "your-password"
      }
    }
  }
}
```

### Remote (HTTP) transport

By default the server speaks stdio and is spawned by the client. To host it as
a shared network service instead — e.g. one instance on your homelab that all
your machines use — set:

```bash
ROMM_MCP_TRANSPORT=http ROMM_MCP_HOST=0.0.0.0 ROMM_MCP_PORT=8765 python server.py
```

Then point clients at it, e.g. Claude Code:

```bash
claude mcp add --transport http romm http://your-host:8765/mcp
```

The HTTP transport has no authentication of its own — bind it to a trusted
network only (LAN, VPN, or an overlay network like Tailscale), never the open
internet. Note that claude.ai web/mobile connectors connect from Anthropic's
cloud and cannot reach private-network URLs.

## Examples

Once configured, you can ask your AI assistant things like:

- "What platforms do I have in RomM?"
- "Search for Zelda games"
- "Show me my backlog"
- "How many ROMs do I have total?"
- "What was recently added?"
- "Show me the saves for Super Metroid"
- "What's in my favorites?"
- "List my firmware files for PlayStation"
- "What tasks are running?"
- "What devices are registered?"

And, with the write tools:

- "Mark Chrono Trigger as finished"
- "Favorite Super Metroid"
- "Add it to my backlog and rate it 9"
- "Make a collection called 'SNES RPGs' and add ROMs 10, 11, and 12"
- "Add a note to this ROM: 'glitch at the second boss, save often'"

## Security

- **Least privilege.** The OAuth2 token requests only the read scopes the tools use plus the write scopes those tools actually need: `roms.user.write` (play status, favorites, notes), `collections.write` (regular and smart collections), `assets.write` (save-state, save-file and memory-card uploads, deletes, and property updates), `platforms.write` (the two server-side metadata exports), `me.write` (your own notifications), and `tasks.run` (the library rescan). It deliberately does **not** request `roms.write`, `firmware.write`, `users.write`, or `devices.write` — no tool uses them.
- **Bounded write surface.** Write tools change only your own user data (play status, favorites, notes), your own collections, your own save states, save files and memory cards (upload, delete, favorite, metadata), and your own notifications. The two export tools (`romm_export_gamelist`, `romm_export_pegasus`) write `gamelist.xml` / `metadata.pegasus.txt` **inside the platform directories on the RomM server** and download nothing to the client. No tool edits ROM files, firmware, or other users.
- **Destructive ops are labeled.** `romm_delete_note`, `romm_delete_collection`, `romm_delete_play_session`, `romm_delete_smart_collection`, `romm_state_delete`, `romm_save_delete`, `romm_memory_card_delete`, and `romm_notification_dismiss` permanently remove data and say so in their descriptions. (`romm_delete_collection` removes the grouping, not the ROMs; `romm_notification_dismiss` removes all of your notifications only with an explicit dismiss_all=True.)
- **Credentials stay in memory.** Credentials and tokens are never written to disk. (Files move only where you ask: the download tools write to the local `target_path` you give them, and the upload tools read the local files you point them at.)
- **TLS by default.** Certificate verification is enabled by default (`ROMM_TLS_VERIFY=true`).
- **Auto-retry.** If a token expires mid-session, the server re-authenticates transparently.

## Auth

The server authenticates with RomM in one of two modes:

- **Client token (RomM 5.x, recommended):** set `ROMM_CLIENT_TOKEN` to a `rmm_...` token created in RomM (user menu → Client tokens). It is sent as the bearer credential directly — nothing else is stored and no token endpoint is called. The token carries its own scope set; a rejected token fails with a clear regeneration hint.
- **OAuth2 password grant:** with `ROMM_USERNAME`/`ROMM_PASSWORD`, tokens are scoped to the minimum permissions the tools need and automatically refreshed when they expire. If a request gets a 401, the server re-authenticates and retries once.

**Note:** The read and write tools operate on your own library and user data, so an ordinary **enabled** RomM user account is sufficient — admin is not required. (`romm_scan_library` does require an account permitted to run tasks.)

**RomM 5.0 role change:** RomM 5.0 collapsed the old `viewer`/`editor`/`admin` roles into `user`/`admin` and moved fine-grained authorization to a permissions system (legacy roles are coerced to `user` on upgrade). If a tool unexpectedly gets a 403 on a 5.0 instance, check the account's effective permissions (`GET /api/permissions/me`) in the RomM admin UI.

## Known issues

Server-side issues found by running this server's live e2e suite
(`smoke_test.py`) against a RomM 5.0.0 instance. The first two were observed on
5.0.0 and are **pending verification against 5.4** — they may or may not still
occur there:

- **`romm_filters` times out (observed on 5.0.0; pending verification on 5.4).**
  `GET /api/roms/filters` in RomM 5.0.0 executes a query with a cartesian
  product (RomM's log flags it at `roms_handler.py:2159`); the request hangs
  until the client timeout, and the abandoned query keeps running server-side
  at high CPU. Avoid calling `romm_filters` on 5.0.0 until this is fixed
  upstream — every call strands another runaway database query.
- **Note listing 500s once any note exists (observed on 5.0.0; pending
  verification on 5.4).** RomM 5.0.0's `GET /api/roms/{id}/notes` fails
  serialization (`UserNoteSchema` validation in `endpoints/roms/notes.py`)
  whenever the ROM has at least one note. Creating and deleting notes work;
  `romm_rom_notes` (and the read-back after `romm_add_note`) errors on such
  ROMs until fixed upstream.
- **Library scan triggering — resolved in 1.3.0.** RomM 5.0.0 rejected
  `POST /api/tasks/run/scan_library` (`scan_library` is `manual_run: false`).
  `romm_scan_library` now triggers scans through `POST /api/tasks/scan` (a
  quick scan by default, or a full scan with `full=true`) and reports the
  queued task; follow it with `romm_task_detail`. The old
  `/api/tasks/run/scan_library` path is gone.

## License

MIT. See [CHANGELOG.md](CHANGELOG.md) for release history.

## Fork

This repository is a fork of
[lodordev/mcp-romm](https://github.com/lodordev/mcp-romm). This fork's default
branch is `master`; upstream's is `main`. Sync with upstream with:

```bash
git fetch upstream main && git merge upstream/main
```

Add the upstream remote once first if needed:
`git remote add upstream https://github.com/lodordev/mcp-romm.git`.
