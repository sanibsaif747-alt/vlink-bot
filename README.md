# vlink-bot — Telegram shortener-bypass bot

Bypasses link shorteners (linksgo.in, earnlinks.in, vplink.in) and returns the
final file link (MediaFire etc.) directly in Telegram.

- Pure Python stdlib (urllib) + curl binary for API solves
- Auto-discovers the earnlinks/linksgo `/links/go` API form and solves it via
  POST with session cookies (no browser needed)
- Playwright/Chromium browser fallback for JS-gated chains (`chain_walker.py`)
- 24/7 watchdog supervisor with auto-restart (`vlink_supervisor.py`)
- Log rotation, allowlisted chat IDs, loop detection, UA rotation

## Files

| File | Purpose |
|---|---|
| `vlink_bypass.py` | Telegram bot + shortener API solver (entry point) |
| `vlink_supervisor.py` | Watchdog: keeps bot alive, restarts on crash |
| `vlink.sh` | start/stop/restart/status helper (uses `setsid`) |
| `chain_walker.py` | Playwright browser fallback for JS-gated chains |

## Env vars

| Var | Required | Purpose |
|---|---|---|
| `VPLINK_BOT_TOKEN` | yes | Telegram bot token from @BotFather |
| `VPLINK_ALLOWED_CHAT_IDS` | no | Comma-separated chat IDs allowed to use the bot |
| `VPLINK_DEBUG` | no | `1` for debug output |

## Run (any 24/7 host)

```bash
export VPLINK_BOT_TOKEN=123:ABC
export VPLINK_ALLOWED_CHAT_IDS=123456789
python3 vlink_bypass.py
```

Or with the supervisor + helper script (Android/PRoot tested):

```bash
./vlink.sh start
./vlink.sh status
```

CLI test without Telegram:

```bash
python3 vlink_bypass.py --resolve "https://linksgo.in/XXXX"
```

## How the solve works

1. For linksgo.in / earnlinks.in links: fetch the form page with the correct
   partner-domain `Referer` (partner learned from the no-referer 302).
2. Extract `_csrfToken`, `ad_form_data`, `_Token[fields]`, `_Token[unlocked]`.
3. POST to `/links/go` with session cookies via `curl` (Chrome UA) — server
   fingerprints curl-style requests correctly.
4. The API may chain (linksgo → earnlinks → MediaFire): solved recursively.
5. Session/code caches in-process for instant re-solves of the same link.

Note: links have a server-side click budget per code — a spent code returns
`Bad Request`; fresh links work immediately.