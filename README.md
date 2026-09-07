# Colin's Cool Couch Clicker

Colossally convenient couch control, courtesy of Colin. A free, local web remote for Apple TV that runs on your Mac. Open it in a browser tab (or on your phone over Wi-Fi), pair once with the PIN on the TV, and you have navigation, playback, volume, power, typing, and a customizable grid of favorites that launch apps or jump straight to a show.

Nothing leaves your network except a once-a-day check for updates (a single request to GitHub, can be turned off) and the optional "About what's playing" lookups (see below). There are no accounts, no subscriptions, and no telemetry. Pairing credentials are stored in `~/Library/Application Support/Clicker/`.

## Start it

Double-click `start.command` in Finder, or:

```bash
./start.command
```

The first run creates a Python virtual environment and installs [pyatv](https://pyatv.dev) (about a minute). After that it opens `http://localhost:8765/` in your browser.

To try the interface with no Apple TV around:

```bash
./start.command --demo
```

## Pairing (one time per Apple TV)

1. Open the device picker (the pill in the header) and wait for the scan.
2. **Pair remote** (AirPlay): navigation, playback, volume. A 4-digit PIN appears on the TV. Type it in.
3. **Pair apps** (Companion): launching apps, listing installed apps, typing into text fields, sleep and wake. Another PIN.
4. Connect. Clicker remembers the last device and reconnects on the next launch.

If a PIN never appears on the TV, check the Apple TV's **Settings, AirPlay and HomeKit, Allow Access** (set it to Everyone or Anyone on the Same Network while pairing).

## Favorites

Tiles come in five flavors:

| Type | What it does | Value |
|---|---|---|
| App | Launches an app | Bundle ID, e.g. `com.netflix.Netflix` (the "On this TV" tab lists what is installed) |
| Link | Opens a deep link inside the app | `https://www.netflix.com/title/80057281`, `https://www.youtube.com/watch?v=…`, `https://tv.apple.com/us/show/…` |
| Text | Types into the focused field | Any string |
| Button | One remote press | `screensaver`, `control_center`, `suspend`, `top_menu`, … |
| Macro | A short sequence | One step per line: `home`, `wait 700`, `launch com.plexapp.plex`, `down x3`, `type hello` |

Edit mode (pencil icon) lets you rename, remove, and drag to reorder. Settings has export and import as JSON.

Whether a deep link actually opens the right show is up to that app. Netflix, YouTube, and Apple TV+ links generally work. Others vary.

## Themes

Nineteen of them. Dark (default) and Light. Five for when the lights are off: Acid Trip, Lava Lamp, Vaporwave, Blacklight, Rainbow Road. CHAOS. Terminal, VHS, Blueprint, Braun, Ransom Note, Late Show, Monolith, and Rick Owens Light. Three that react to the world: Sunset follows the real time of day, Reactive follows play/pause, Per-app skin dresses up as whatever app is open. The theme button in the header opens the picker, or press `K`. The psychedelic ones animate; they respect the system "reduce motion" setting.

## Install on any Mac (the easy way)

**Apple Silicon Mac (2020 or later):** download the latest `Clicker-mac-arm64.zip` from the [Releases page](https://github.com/lightsgoblack/clicker/releases/latest), double-click the zip, and drag `Clicker` into your Applications folder. Python is included; nothing else to install.

If macOS says it cannot verify the app, click **Done**, open **System Settings, Privacy & Security**, scroll down, and click **Open Anyway** next to Clicker. One-time step. Releases built with a Developer ID certificate are notarized and skip this entirely (see `build-app.sh`).

**Intel Mac, or if you would rather not click through that warning:** paste this one line into Terminal (`Cmd+Space`, type Terminal, Return):

```bash
curl -fsSL https://raw.githubusercontent.com/lightsgoblack/clicker/main/install.sh | bash
```

That assembles a `Clicker` app in your Applications folder on your own Mac (so there is no warning to click through), using the Python that macOS provides. If the Mac has never had Apple's Command Line Tools, a dialog offers to install them. Click Install and wait; the installer continues on its own.

Either way, Clicker then runs quietly in the background and opens the remote in your browser. Open it from Launchpad, Spotlight, or the Dock any time. Quit it from the gear menu in the remote.

### Put it in the Dock

Drag `Clicker` from Applications onto the Dock. Clicking it starts the server if needed and opens the remote. For a proper windowed app with no browser chrome, open the remote in Safari and choose **File, Add to Dock**; that creates a "Clicker" web app you can launch from the Dock too.

## Share it with friends

This cannot live on Vercel or any web host: the server has to sit on the same Wi-Fi as the Apple TV, so each person runs it on their own Mac. Send them the Releases link (Apple Silicon) or the one-line install (any Mac) above. They need a Mac on the same Wi-Fi as their Apple TV, and the PIN the TV shows during the two pairing steps. That is the whole setup. Phones then work by opening the Mac's address in a browser while the Mac is awake.

## Native protocol (Swift)

`swift/` holds `atvswift`, a small native Swift implementation of the Apple TV Companion protocol (pairing, verification, session encryption, commands) with no Python and no pyatv. It shares pyatv's credential format, so a TV paired by Clicker works with it directly. It is the seed of a future native app; today it is a command-line tool:

```bash
cd swift && swift build -c release && .build/release/atvswift verify --host <tv ip> --port <companion port> --creds "<credentials>" --press play_pause
```

## Developer install

```bash
git clone https://github.com/lightsgoblack/clicker.git ~/Developer/clicker && ~/Developer/clicker/start.command
```

`mac/` holds the app-bundle pieces (`Info.plist`, `launcher.sh`, `AppIcon.icns`, regenerated from `icon.svg` with `qlmanage` + `iconutil`). `install.sh` assembles them into `~/Applications/Clicker.app`; set `CLICKER_SRC=/path/to/checkout` to build from a local copy instead of downloading. `build-app.sh` makes the self-contained PyInstaller build and zip for Releases (arch of the building Mac). To ship a version: bump `VERSION` in `server.py`, commit, then `./release.sh "what changed"` builds, tags, and publishes the release; installed apps pick it up on their next check.

## More than a remote

- **Continue watching.** The last five things you watched appear as a row. Tap one to jump back in (a real deep link for YouTube, Netflix, and Apple TV+; the app itself for everything else).
- **Sleep timer.** Under the remote: 15, 30, 45, 60, or 90 minutes, then the TV goes to sleep. Runs on the Mac, so closing the tab does not cancel it.
- **Bedtime.** A favorite in the Popular list: home, then sleep. Add your own steps to it as a macro.
- **Find on TV.** Type a show, movie, or person; Clicker opens the TV's search, types it, and presses Select.
- **Party mode.** The QR button in the header. Turn it on and anyone on your Wi-Fi scans the code to get the remote on their phone, no install or pairing. Turn it off and every phone is locked out. When it is off, only this Mac can reach the remote at all.
- **Roulette.** A dice tile at the end of Favorites picks one at random and opens it. Decides the night for you.
- **Watch stats.** Hours by app and by show over the last 30 days, with a daily sparkline. Counted on the Mac from what the TV reports, shared with no one. Stats button on the Continue watching card, or in Settings.
- **Sounds.** Off by default, in Settings. Synthesized in the browser, flavored by theme: typewriter in Terminal, tape thunk in VHS, random bleeps in CHAOS, a soft tock in Braun.
- **Several TVs.** Once two Apple TVs have been connected, the header shows a tab per TV. Tap to switch.
- **Siri and Shortcuts.** Party mode's sheet lists ready-to-copy URLs (open an app, play/pause, sleep, volume) for a Shortcuts "Get Contents of URL" action, so "Hey Siri, Netflix on the big TV" is a two-minute setup. From the Mac itself, `http://localhost:8765/api/do/launch?target=com.netflix.Netflix` works with no token.
- **Kid mode.** In Settings. Remote and favorites only: no settings, typing, power, editing, or About. A PIN you choose turns it off, and the server refuses the grown-up actions while it is on.

## About what's playing (optional)

Tap the Now Playing card, or the About button, for a panel about whatever is on: a Wikipedia summary with poster, and a "Rare but true" list of five surprising facts written by Claude.

This is **off by default** because it is the one feature that talks to the internet. When on, the title of what you are watching (plus series, artist, and app name) is sent to Wikipedia, and to Colin's facts service or straight to Anthropic's API if you use your own key. Nothing else is sent. Turn it on in Settings, or from the panel itself the first time.

The facts come from Claude two ways, chosen in Settings:

- **Colin's service** (default, no setup): a tiny budget-capped function Colin hosts (see `facts-service/`). It asks Claude and keeps nothing. When his monthly budget is used up it says so, and you can switch to your own key or wait for next month.
- **Your own key** from [console.anthropic.com](https://console.anthropic.com/): paste it in Settings. It is stored only in Clicker's preferences file on this Mac, and each lookup costs you about two cents.

Either way, results are cached, "More facts" asks again, and the facts are AI-written from Claude's knowledge, so verify before betting money on one.

## Updates

Clicker checks GitHub once a day (and whenever you press **Check for updates** in Settings) for a newer release. If there is one, a banner offers **Update now**: it downloads the new version, swaps it into place, and relaunches. Pairing, favorites, and settings live outside the app, so they survive. The check is a single request for the release list; it carries nothing about you or what you watch, and it can be turned off in Settings.

Builds older than 1.1.0 predate the updater and need one manual reinstall. Copies run from a git checkout update with `git pull`.

Share this link, which always points at the newest version: https://github.com/lightsgoblack/clicker/releases/latest

## Keyboard

Arrows, Enter, Esc or Backspace for back, Space for play/pause, `H` home, `[` `]` skip, `,` `.` previous/next, `-` `=` volume, `C` Control Center, `P` power, `T` to focus the typing box, `K` themes, `?` for the full list.

## Phone

The server binds to all interfaces, so on the same Wi-Fi open `http://<your Mac's IP>:8765/` on a phone. Add it to the Home Screen for an app-like feel.

## Layout

- `server.py`: aiohttp server wrapping pyatv. Scan, pair, connect, commands, app list, launch, keyboard, power.
- `index.html`: the whole UI. No build step, no framework, no external assets.
- `start.command`: double-clickable launcher that bootstraps the venv on first run.

## Limits worth knowing

- Apple TV has no "mute" command over this protocol, so there is no mute button.
- App launching, typing, and power need the Companion pairing (tvOS 13 or later; Apple TV 4K and HD).
- The server must be running on the Mac for the remote to work.
