# CLAUDE.md — Clicker

Local web remote for Apple TV. Lives at `~/Developer/clicker` (never under `~/Documents`, which is iCloud-synced).

## Shape
- `server.py` — aiohttp + pyatv. One `State` object holds storage, current connection, in-progress pairing. Demo device classes at the bottom fake everything for `--demo`.
- `index.html` — single-file UI, vanilla JS, no build, no external assets. Theme and layout are `data-*` attributes on `<html>` set pre-paint. Every color is a CSS variable in the two `:root[data-theme]` blocks.
- `start.command` — bootstraps `.venv` and runs the server. Never add a root `package.json`.
- Credentials: `~/Library/Application Support/Clicker/credentials.json` via pyatv `FileStorage`. `prefs.json` beside it holds the last device. Neither is in the repo.
- localStorage keys are prefixed `clicker_` (`theme`, `layout`, `favs`).

## Protocol facts that bit or will bite
- tvOS 15+ does not advertise MRP separately; remote control rides over AirPlay, so **"Pair remote" = AirPlay pairing**. **"Pair apps" = Companion pairing** (app list, launch, keyboard, power, `home`). Both need a PIN typed from the TV screen.
- `scan()` must be passed `storage=` or `service.credentials` is always empty and every device looks unpaired.
- `launch_app()` takes either a bundle ID or a URL; deep-link handling is entirely the target app's business.
- No mute in the protocol. Volume commands work through HDMI-CEC / the TV, so they depend on the TV honoring them.
- The catalog bundle IDs in `index.html` are community-known values, not verified against every tvOS release; the "On this TV" tab (`/api/apps`) is authoritative.

## Verify
- `.venv/bin/python server.py --demo --no-open` then drive the UI in a browser; `/api/demo/log` shows every command the fake device received.
- Real-device behavior (does Netflix honor the link, does the TV respond to volume) cannot be verified headlessly. Say so.
- Favorite icons: `ICONS` map in `index.html` (hand-drawn SVG on brand gradients, keyed by name). Catalog entries reference a key; custom favorites can pick one or type an emoji/initials. Old favorites upgrade themselves by matching bundle ID.
