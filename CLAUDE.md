# CLAUDE.md — Colin's Cool Couch Clicker

Display name is "Colin's Cool Crazy Couch Computer Clicker" (short: C⁶ Clicker); repo, folder, and localStorage prefix stay `clicker`. Local web remote for Apple TV. Lives at `~/Developer/clicker` (never under `~/Documents`, which is iCloud-synced).

## Shape
- `server.py` — aiohttp + pyatv. One `State` object holds storage, current connection, in-progress pairing. Demo device classes at the bottom fake everything for `--demo`.
- `index.html` — single-file UI, vanilla JS, no build, no external assets. Theme and layout are `data-*` attributes on `<html>` set pre-paint. Every color is a CSS variable in the two `:root[data-theme]` blocks.
- `start.command` — bootstraps `.venv` and runs the server. Never add a root `package.json`.
- `install.sh` + `mac/` — the friend-facing path. `curl | bash` assembles `~/Applications/Clicker.app` on the user's Mac (built locally, so no Gatekeeper prompt), `LSUIElement` so no Dock icon, launcher writes to `~/Library/Logs/Clicker.log`. Launcher and `server.py` both detect an already-running instance on the port and just open the browser. `/api/quit` (Settings, Quit) is the only way to stop the background app. Tested under the CLT Python 3.9.6; pyatv 0.18 requires >=3.9.
- Ink themes (`blackwork`, `flash`) set `data-ink` on `<html>`: monochrome palette, 3px radii, Didot/Copperplate/mono system fonts (no web fonts), card counters + registration corners, dial-ring d-pad via masked `repeating-conic-gradient`, grain + ornament as CSS data-URI SVGs on `body::before/::after`, brand icons desaturated with `filter:grayscale`.
- Credentials: `~/Library/Application Support/Clicker/credentials.json` via pyatv `FileStorage`. `prefs.json` beside it holds the last device. Neither is in the repo.
- localStorage keys are prefixed `clicker_` (`theme`, `layout`, `favs`).
- Themes: `THEMES` array in JS + one `:root[data-theme=...]` block each. Psychedelic ones set `data-psy` on `<html>`, which makes `body` transparent so the animated `body::before` ground shows (an opaque body paints over a negative z-index pseudo-element; that cost a debugging round). Rainbow hue is a JS ticker on `--hue`, not a CSS animation.
- Colin paired "Great Room" for real on 2026-09-06 and it connected; the pairing flow is verified on a device.

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
