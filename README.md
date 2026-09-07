# Colin's Cool Couch Clicker

Colossally convenient couch control, courtesy of Colin. A free, local web remote for Apple TV that runs on your Mac. Open it in a browser tab (or on your phone over Wi-Fi), pair once with the PIN on the TV, and you have navigation, playback, volume, power, typing, and a customizable grid of favorites that launch apps or jump straight to a show.

Nothing leaves your network. There are no accounts, no subscriptions, and no telemetry. Pairing credentials are stored in `~/Library/Application Support/Clicker/`.

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

Dark (default), Light, two ink themes (Blackwork: bone on black; Flash Sheet: black on bone paper, both monochrome and brutalist with tattoo-flash ornament), and five for when the lights are off: Acid Trip, Lava Lamp, Vaporwave, Blacklight, and Rainbow Road. The theme button in the header opens the picker, or press `K`. The psychedelic ones animate; they respect the system "reduce motion" setting.

## Install on any Mac (the easy way)

Paste this one line into Terminal (press `Cmd+Space`, type Terminal, press Return) and hit Return:

```bash
curl -fsSL https://raw.githubusercontent.com/lightsgoblack/clicker/main/install.sh | bash
```

That builds a `Clicker` app in your Applications folder, installs its one dependency inside it, and opens it. From then on, open Clicker from Launchpad or Spotlight like any app. It runs quietly in the background and opens the remote in your browser. Quit it from the gear menu in the remote.

The only speed bump: if the Mac has never had Apple's Command Line Tools, a dialog offers to install them. Click Install, wait, and the installer continues on its own.

Because the app is assembled on your own Mac rather than downloaded as an app, there is no "unidentified developer" warning to fight.

## Share it with friends

This cannot live on Vercel or any web host: the server has to sit on the same Wi-Fi as the Apple TV, so each person runs it on their own Mac. Send them the one-line install above. They need a Mac on the same Wi-Fi as their Apple TV, and the PIN the TV shows during the two pairing steps. That is the whole setup. Phones then work by opening the Mac's address in a browser while the Mac is awake.

## Developer install

```bash
git clone https://github.com/lightsgoblack/clicker.git ~/Developer/clicker && ~/Developer/clicker/start.command
```

`mac/` holds the app-bundle pieces (`Info.plist`, `launcher.sh`, `AppIcon.icns`, regenerated from `icon.svg` with `qlmanage` + `iconutil`). `install.sh` assembles them into `~/Applications/Clicker.app`; set `CLICKER_SRC=/path/to/checkout` to build from a local copy instead of downloading.

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
