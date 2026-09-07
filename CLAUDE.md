# CLAUDE.md — Colin's Cool Couch Clicker

Display name is "Colin's Cool Crazy Couch Computer Clicker" (short: C⁶ Clicker); repo, folder, and localStorage prefix stay `clicker`. Local web remote for Apple TV. Lives at `~/Developer/clicker` (never under `~/Documents`, which is iCloud-synced).

## Shape
- `server.py` — aiohttp + pyatv. One `State` object holds storage, current connection, in-progress pairing. Demo device classes at the bottom fake everything for `--demo`.
- `index.html` — single-file UI, vanilla JS, no build, no external assets. Theme and layout are `data-*` attributes on `<html>` set pre-paint. Every color is a CSS variable in the two `:root[data-theme]` blocks.
- `start.command` — bootstraps `.venv` and runs the server. Never add a root `package.json`.
- `install.sh` + `mac/` — the friend-facing path. `curl | bash` assembles `~/Applications/Clicker.app` on the user's Mac (built locally, so no Gatekeeper prompt), `LSUIElement` so no Dock icon, launcher writes to `~/Library/Logs/Clicker.log`. Launcher and `server.py` both detect an already-running instance on the port and just open the browser. `/api/quit` (Settings, Quit) is the only way to stop the background app. Tested under the CLT Python 3.9.6; pyatv 0.18 requires >=3.9.
- `build-app.sh` — PyInstaller self-contained `dist/Clicker.app` + zip for GitHub Releases (`gh release create vX.Y dist/Clicker-mac-arm64.zip`). Ad-hoc signed only (Colin has an Apple Development cert, not Developer ID), so downloaded copies need the one-time Privacy & Security "Open Anyway". `server.py` resolves `HERE` through `sys._MEIPASS` when frozen. Build is arm64-only from this Mac.
- Ink themes were removed in 1.4.0 (Colin's call); `owens` keeps the melted-bone idea. Themes with behavior live in the `FX` map in `index.html` (sunset clock, monolith idle, reactive play-state, perapp colors, ransom letter-wrapping); `setTheme` starts/stops them and `renderStatus`/`renderFavs` re-apply. Old note for reference: ink themes set `data-ink` on `<html>`: monochrome palette, 3px radii, Didot/Copperplate/mono system fonts (no web fonts), card counters + registration corners, dial-ring d-pad via masked `repeating-conic-gradient`, grain + ornament as CSS data-URI SVGs on `body::before/::after`, brand icons desaturated with `filter:grayscale`.
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

- **Info lookups** (`InfoService` in `server.py`): opt-in via `prefs.info_enabled`; Wikipedia through ONE `api.php` call (`generator=search` + extracts + pageimages with `pilicense=any`; the REST `page/summary` endpoint 429s this client, do not go back to it); facts via the official `anthropic` SDK (`claude-opus-5`, effort medium through `extra_body` so old SDKs on the CLT Python 3.9 path still work). Key lives in `prefs.json` (chmod 600), never returned in full. No key + no env credentials → friendly "add a key" error. Results LRU-cached per playing context.

- **Updater** (`Updater` in `server.py`): `VERSION` constant is the source of truth (`build-app.sh`/`release.sh` read it with sed; keep the line shape `VERSION = "x.y.z"`). Checks `api.github.com/repos/lightsgoblack/clicker/releases/latest` 8s after launch and daily; `/api/update/apply` downloads `Clicker-mac-<arch>.zip`, `ditto`s it over the running bundle (old moved aside, rolled back on failure), then `open -a <bundle> --args --no-open` after 1.5s and SIGTERMs itself. install.sh-built apps update by copying files from the release tarball instead. Dev checkouts (`.git` present) refuse and say `git pull`. Test hooks: `CLICKER_VERSION_OVERRIDE`, `CLICKER_RELEASES_URL` (see the scratch test in the 2026-09-07 session: fake `latest.json` + zip on `python3 -m http.server`). The GitHub API 404s while the repo is private, so nobody can update until it is public.
- **Release process:** bump `VERSION`, commit, `./release.sh "notes"`. Never overwrite an existing release's zip again (v1.0 was overwritten several times before versioning existed).

- **Hosted facts** (`facts-service/`, Vercel project `clicker-facts`, Node function `api/facts.js`, `@anthropic-ai/sdk` 0.x): default source for facts; `prefs.facts_source` = hosted|own. Clicker sends `X-Clicker-Client` (required by the function as a scanner filter, not a secret). Meter is in-memory unless Upstash env vars exist; the REAL cap is the $2 monthly limit on Colin's dedicated Anthropic workspace. `ANTHROPIC_API_KEY` on Vercel must be added by Colin (`vercel env add`), never by a session. Deploy: `cd facts-service && vercel --prod --yes`.

## Verify
- `.venv/bin/python server.py --demo --no-open` then drive the UI in a browser; `/api/demo/log` shows every command the fake device received.
- Real-device behavior (does Netflix honor the link, does the TV respond to volume) cannot be verified headlessly. Say so.
- Favorite icons: `ICONS` map in `index.html` (hand-drawn SVG on brand gradients, keyed by name). Catalog entries reference a key; custom favorites can pick one or type an emoji/initials. Old favorites upgrade themselves by matching bundle ID.
