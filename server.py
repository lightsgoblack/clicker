#!/usr/bin/env python3
"""Clicker: a local web remote for Apple TV.

Runs on your Mac, talks to the Apple TV over the local network using pyatv
(the open-source implementation of Apple's remote protocols), and serves a
single-file web remote at http://localhost:PORT.

Nothing leaves your network. Credentials live in
~/Library/Application Support/Clicker/ and are never stored in the repo.

Usage:
    python3 server.py            # normal
    python3 server.py --demo     # fake device, for trying the UI with no Apple TV
    PORT=9000 python3 server.py  # different port
"""

import argparse
import asyncio
import json
import logging
import os
import hashlib
import ipaddress
import platform
import secrets
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from collections import OrderedDict
from pathlib import Path

import aiohttp
from aiohttp import web

import pyatv
from pyatv import exceptions
from pyatv.const import InputAction, Protocol, PowerState, DeviceState
from pyatv.interface import DeviceListener
from pyatv.storage.file_storage import FileStorage

# Frozen by PyInstaller? Data files live next to the bundled interpreter.
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VERSION = "1.6.2"
APP_VERSION = os.environ.get("CLICKER_VERSION_OVERRIDE") or VERSION  # override is for updater tests only
REPO = "lightsgoblack/clicker"
FROZEN = bool(getattr(sys, "frozen", False))
DATA_DIR = Path.home() / "Library" / "Application Support" / "Clicker"
PORT = int(os.environ.get("PORT", "8765"))

log = logging.getLogger("clicker")

# Remote buttons the UI may send. Each maps to a method on atv.remote_control.
# Values are (method name, accepts InputAction).
COMMANDS = {
    "up": ("up", True),
    "down": ("down", True),
    "left": ("left", True),
    "right": ("right", True),
    "select": ("select", True),
    "menu": ("menu", True),
    "home": ("home", True),
    "home_hold": ("home_hold", False),
    "top_menu": ("top_menu", False),
    "play": ("play", False),
    "pause": ("pause", False),
    "play_pause": ("play_pause", False),
    "stop": ("stop", False),
    "next": ("next", False),
    "previous": ("previous", False),
    "skip_forward": ("skip_forward", False),
    "skip_backward": ("skip_backward", False),
    "volume_up": ("volume_up", False),
    "volume_down": ("volume_down", False),
    "control_center": ("control_center", False),
    "screensaver": ("screensaver", False),
    "guide": ("guide", False),
    "channel_up": ("channel_up", False),
    "channel_down": ("channel_down", False),
    "suspend": ("suspend", False),
    "wakeup": ("wakeup", False),
}

ACTIONS = {
    "tap": InputAction.SingleTap,
    "double": InputAction.DoubleTap,
    "hold": InputAction.Hold,
}

PROTOCOLS = {"airplay": Protocol.AirPlay, "companion": Protocol.Companion, "mrp": Protocol.MRP}


def json_error(message, status=400):
    return web.json_response({"ok": False, "error": message}, status=status)


class Listener(DeviceListener):
    def __init__(self, app_state):
        self.state = app_state

    def connection_lost(self, exception):
        log.warning("Connection lost: %s", exception)
        self.state.atv = None

    def connection_closed(self):
        log.info("Connection closed")
        self.state.atv = None


class State:
    """Everything the server holds in memory."""

    def __init__(self, loop, demo=False):
        self.loop = loop
        self.demo = demo
        self.storage = None
        self.atv = None
        self.config = None
        self.configs = {}  # identifier -> BaseConfig from last scan
        self.pairing = None
        self.pairing_protocol = None
        self.prefs_path = DATA_DIR / "prefs.json"
        self.prefs = self.load_prefs()
        self.info = InfoService(self)
        self.updater = Updater(self)
        self.history_path = DATA_DIR / "history.json"
        try:
            self.history = json.loads(self.history_path.read_text())
        except Exception:
            self.history = []
        self.sleep_at = None
        self.sleep_task = None
        self.stats_path = DATA_DIR / "stats.json"
        try:
            self.stats = json.loads(self.stats_path.read_text())
        except Exception:
            self.stats = {}
        self._stats_saved = 0

    # ---- watch stats (local only) ----
    def record_stats(self, p, app, seconds):
        if not p or p.get("state") != "playing":
            return
        day = time.strftime("%Y-%m-%d")
        d = self.stats.setdefault(day, {"apps": {}, "titles": {}})
        app_name = (getattr(app, "name", None) if app else None) or "Unknown"
        d["apps"][app_name] = d["apps"].get(app_name, 0) + seconds
        label = (p.get("series") or p.get("artist") or p.get("title") or "Unknown")
        key = f"{app_name}|{label}"
        d["titles"][key] = d["titles"].get(key, 0) + seconds
        if time.time() - self._stats_saved > 60:
            self._stats_saved = time.time()
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                self.stats_path.write_text(json.dumps(self.stats))
            except Exception:
                pass

    def stats_summary(self, days=30):
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        apps, titles, per_day = {}, {}, {}
        for day, d in self.stats.items():
            if day < cutoff:
                continue
            per_day[day] = sum(d.get("apps", {}).values())
            for a, sec in d.get("apps", {}).items():
                apps[a] = apps.get(a, 0) + sec
            for k, sec in d.get("titles", {}).items():
                titles[k] = titles.get(k, 0) + sec
        total = sum(apps.values())
        top_apps = sorted(apps.items(), key=lambda x: -x[1])
        top_titles = sorted(titles.items(), key=lambda x: -x[1])[:8]
        out = {"days": days, "total": total,
               "apps": [{"name": a, "seconds": sec, "share": (sec / total if total else 0)} for a, sec in top_apps],
               "titles": [{"app": k.split("|", 1)[0], "name": k.split("|", 1)[1], "seconds": sec, "share": (sec / total if total else 0)} for k, sec in top_titles],
               "perDay": per_day, "activeDays": len([v for v in per_day.values() if v > 0])}
        if top_apps:
            a, sec = top_apps[0]
            within = [t for t in top_titles if t[0].startswith(a + "|")]
            out["headline"] = {"app": a, "hours": sec / 3600, "topTitle": within[0][0].split("|", 1)[1] if within else None,
                               "topShare": (within[0][1] / sec) if within and sec else 0}
        return out

    def remember_device(self, conf):
        if self.demo or str(conf.identifier).startswith("demo"):
            return
        known = [k for k in self.prefs.get("known", []) if k.get("identifier") != conf.identifier and not str(k.get("identifier", "")).startswith("demo")]
        known.insert(0, {"identifier": conf.identifier, "name": conf.name})
        self.prefs["known"] = known[:6]

    # ---- continue watching ----
    def record_history(self, p, app):
        if not p or not p.get("title") or p.get("state") not in ("playing", "paused"):
            return
        app_id = getattr(app, "identifier", None) if app else None
        app_name = getattr(app, "name", None) if app else None
        key = (p.get("series") or p.get("title"), app_id)
        entry = {"title": p.get("title"), "series": p.get("series"), "artist": p.get("artist"), "season": p.get("season"),
                 "episode": p.get("episode"), "app": app_id, "appName": app_name, "contentId": p.get("contentId"),
                 "type": p.get("mediaType"), "position": p.get("position"), "total": p.get("total"), "ts": int(time.time())}
        if self.history and (self.history[0].get("series") or self.history[0].get("title"), self.history[0].get("app")) == key:
            self.history[0].update({k: v for k, v in entry.items() if v is not None})
            changed = True
        else:
            self.history = [h for h in self.history if (h.get("series") or h.get("title"), h.get("app")) != key]
            self.history.insert(0, entry)
            changed = True
        self.history = self.history[:20]
        if changed and (not hasattr(self, "_hist_saved") or time.time() - self._hist_saved > 15):
            self._hist_saved = time.time()
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                self.history_path.write_text(json.dumps(self.history))
            except Exception:
                pass

    @staticmethod
    def deep_link(h):
        """Best-effort link back into the content; falls back to just the app."""
        app, cid = h.get("app") or "", h.get("contentId") or ""
        if app == "com.google.ios.youtube" and re.fullmatch(r"[\w-]{11}", cid):
            return f"https://www.youtube.com/watch?v={cid}"
        if app == "com.netflix.Netflix" and re.fullmatch(r"\d{5,}", cid):
            return f"https://www.netflix.com/title/{cid}"
        if app == "com.apple.TVWatchList" and cid.startswith("umc."):
            return f"https://tv.apple.com/show/{cid}"
        return app or None

    # ---- sleep timer ----
    async def sleep_set(self, minutes):
        if self.sleep_task:
            self.sleep_task.cancel()
            self.sleep_task = None
        self.sleep_at = None
        if not minutes:
            return
        self.sleep_at = time.time() + minutes * 60

        async def run():
            try:
                await asyncio.sleep(minutes * 60)
                if self.atv is not None:
                    try:
                        await self.atv.power.turn_off()
                    except Exception:
                        await self.atv.remote_control.suspend()
            finally:
                self.sleep_at = None
                self.sleep_task = None
        self.sleep_task = asyncio.get_event_loop().create_task(run())

    # ---- kid mode ----
    @staticmethod
    def pin_hash(pin):
        return hashlib.sha256(("clicker-kid:" + str(pin).strip()).encode()).hexdigest()

    def kid_check(self, pin):
        return bool(self.prefs.get("kid_pin")) and self.pin_hash(pin) == self.prefs.get("kid_pin")

    # ---- party mode ----
    def party_token(self):
        return self.prefs.get("party_token")

    def party_url(self, port):
        ips = []
        try:
            import ifaddr
            for a in ifaddr.get_adapters():
                for ip in a.ips:
                    if isinstance(ip.ip, str) and ipaddress.ip_address(ip.ip).is_private and not ip.ip.startswith("127."):
                        ips.append(ip.ip)
        except Exception:
            pass
        ips.sort(key=lambda x: (not x.startswith("192.168."), not x.startswith("10."), x))
        tok = self.party_token()
        return f"http://{ips[0]}:{port}/?party={tok}" if ips and tok else None

    def load_prefs(self):
        try:
            return json.loads(self.prefs_path.read_text())
        except Exception:
            return {}

    def save_prefs(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.prefs_path.write_text(json.dumps(self.prefs, indent=2))
        try:
            os.chmod(self.prefs_path, 0o600)  # may hold an API key
        except Exception:
            pass

    async def init_storage(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.storage = FileStorage(str(DATA_DIR / "credentials.json"), self.loop)
        await self.storage.load()

    async def scan(self, timeout=5):
        if self.demo:
            return [DemoDevice.config()]
        configs = await pyatv.scan(self.loop, timeout=timeout, storage=self.storage)
        # Only things that are actually Apple TVs (or can take remote commands).
        keep = []
        for c in configs:
            protos = {s.protocol for s in c.services}
            if Protocol.Companion in protos or Protocol.MRP in protos or Protocol.DMAP in protos:
                keep.append(c)
        self.configs = {c.identifier: c for c in keep}
        return keep

    def describe(self, c):
        services = []
        for s in c.services:
            services.append({
                "protocol": s.protocol.name.lower(),
                "paired": bool(s.credentials),
                "pairing": s.pairing.name,
            })
        return {
            "identifier": c.identifier,
            "name": c.name,
            "address": str(c.address),
            "model": c.device_info.model.name if c.device_info else "Unknown",
            "os": c.device_info.version if c.device_info else None,
            "services": services,
        }

    async def connect(self, identifier):
        if self.demo:
            self.atv = DemoDevice()
            self.config = DemoDevice.config()
            self.remember_device(self.config)
            return
        conf = self.configs.get(identifier)
        if conf is None:
            await self.scan()
            conf = self.configs.get(identifier)
        if conf is None:
            raise RuntimeError("Device not found on the network. Is it awake and on the same Wi-Fi?")
        await self.disconnect()
        atv = await pyatv.connect(conf, self.loop, storage=self.storage)
        atv.listener = Listener(self)
        self.atv = atv
        self.config = conf
        self.prefs["last"] = identifier
        self.remember_device(conf)
        self.save_prefs()

    async def disconnect(self):
        if self.atv is not None:
            try:
                self.atv.close()
            except Exception:
                pass
            self.atv = None

    async def pair_start(self, identifier, protocol_name):
        if self.demo:
            self.pairing = "demo"
            return {"needsPin": True}
        conf = self.configs.get(identifier)
        if conf is None:
            await self.scan()
            conf = self.configs.get(identifier)
        if conf is None:
            raise RuntimeError("Device not found on the network.")
        proto = PROTOCOLS[protocol_name]
        if conf.get_service(proto) is None:
            raise RuntimeError(f"Device does not offer {protocol_name}.")
        await self.pair_cancel()
        await self.disconnect()
        handler = await pyatv.pair(conf, proto, self.loop, storage=self.storage)
        await handler.begin()
        self.pairing = handler
        self.pairing_protocol = protocol_name
        return {"needsPin": handler.device_provides_pin}

    async def pair_finish(self, pin):
        if self.pairing is None:
            raise RuntimeError("No pairing in progress.")
        if self.demo:
            self.pairing = None
            if str(pin) != "1234":
                raise RuntimeError("Demo PIN is 1234.")
            return True
        handler = self.pairing
        try:
            if pin is not None and str(pin).strip():
                handler.pin(int(str(pin).strip()))
            await handler.finish()
            ok = handler.has_paired
        finally:
            await handler.close()
            self.pairing = None
        if ok:
            await self.storage.save()
        return ok

    async def pair_cancel(self):
        if self.pairing is not None and self.pairing != "demo":
            try:
                await self.pairing.close()
            except Exception:
                pass
        self.pairing = None

    async def forget(self, identifier):
        if self.demo:
            return
        conf = self.configs.get(identifier)
        if conf is None:
            await self.scan()
            conf = self.configs.get(identifier)
        if conf is None:
            raise RuntimeError("Device not found.")
        if self.config is not None and self.config.identifier == identifier:
            await self.disconnect()
        settings = await self.storage.get_settings(conf)
        await self.storage.remove_settings(settings)
        await self.storage.save()

    async def status(self):
        out = {"connected": self.atv is not None, "demo": self.demo, "version": APP_VERSION}
        u = self.updater.summary()
        if u.get("available") or u.get("busy"):
            out["update"] = {"latest": u.get("latest"), "busy": u["busy"], "progress": u["progress"]}
        if self.atv is None:
            out["last"] = self.prefs.get("last")
            return out
        c = self.config
        out["device"] = {"name": c.name, "identifier": c.identifier, "model": self.describe(c)["model"]}
        try:
            playing = await self.atv.metadata.playing()
            out["playing"] = {
                "state": playing.device_state.name.lower(),
                "title": playing.title,
                "artist": playing.artist,
                "album": playing.album,
                "series": playing.series_name,
                "season": playing.season_number,
                "episode": playing.episode_number,
                "position": playing.position,
                "total": playing.total_time,
                "mediaType": playing.media_type.name.lower(),
                "contentId": getattr(playing, "content_identifier", None),
            }
            self.record_history(out["playing"], getattr(self.atv.metadata, "app", None))
        except Exception as e:  # metadata is best-effort
            out["playing"] = None
            out["playingError"] = str(e)
        try:
            app = self.atv.metadata.app
            out["app"] = {"name": app.name, "identifier": app.identifier} if app else None
        except Exception:
            out["app"] = None
        try:
            out["power"] = self.atv.power.power_state.name.lower()
        except Exception:
            out["power"] = None
        out["sleepAt"] = self.sleep_at
        out["kidMode"] = bool(self.prefs.get("kid_mode"))
        return out


# ---------------------------------------------------------------------------
# Demo device: lets you click around the UI with no Apple TV on the network.
# ---------------------------------------------------------------------------

class _DemoRemote:
    def __init__(self, dev):
        self.dev = dev

    def __getattr__(self, name):
        if name not in {v[0] for v in COMMANDS.values()}:
            raise AttributeError(name)

        async def cmd(action=None):
            self.dev.log.append({"cmd": name, "action": str(action)})
            if name in ("play", "play_pause"):
                self.dev.playing = not self.dev.playing
            if name == "volume_up":
                self.dev.vol = min(100, self.dev.vol + 5)
            if name == "volume_down":
                self.dev.vol = max(0, self.dev.vol - 5)
            return None
        return cmd


class _DemoApps:
    def __init__(self, dev):
        self.dev = dev

    async def app_list(self):
        from types import SimpleNamespace as NS
        return [NS(name=n, identifier=i) for n, i in DemoDevice.APPS]

    async def launch_app(self, target):
        self.dev.log.append({"launch": target})
        for n, i in DemoDevice.APPS:
            if i == target:
                self.dev.app = (n, i)
                break
        else:
            self.dev.app = ("Link", target)


class _DemoKeyboard:
    def __init__(self, dev):
        self.dev = dev

    async def text_set(self, text):
        self.dev.log.append({"text": text})

    async def text_clear(self):
        self.dev.log.append({"text": ""})


class _DemoPower:
    def __init__(self, dev):
        self.dev = dev

    @property
    def power_state(self):
        return PowerState.On if self.dev.on else PowerState.Off

    async def turn_on(self):
        self.dev.on = True

    async def turn_off(self):
        self.dev.on = False


class _DemoMetadata:
    def __init__(self, dev):
        self.dev = dev

    async def playing(self):
        from types import SimpleNamespace as NS
        from pyatv.const import MediaType
        return NS(
            device_state=DeviceState.Playing if self.dev.playing else DeviceState.Paused,
            title="Severance", artist=None, album=None, series_name="Severance",
            season_number=2, episode_number=4, position=1520, total_time=3300,
            media_type=MediaType.TV,
        )

    @property
    def app(self):
        from types import SimpleNamespace as NS
        return NS(name=self.dev.app[0], identifier=self.dev.app[1])


class DemoDevice:
    APPS = [
        ("TV", "com.apple.TVWatchList"), ("Netflix", "com.netflix.Netflix"),
        ("YouTube", "com.google.ios.youtube"), ("Disney+", "com.disney.disneyplus"),
        ("Max", "com.wbd.stream"), ("Prime Video", "com.amazon.aiv.AIVApp"),
        ("Spotify", "com.spotify.client"), ("Settings", "com.apple.TVSettings"),
        ("Music", "com.apple.TVMusic"), ("Plex", "com.plexapp.plex"),
    ]

    def __init__(self):
        self.log = []
        self.playing = True
        self.vol = 40
        self.on = True
        self.app = ("TV", "com.apple.TVWatchList")
        self.remote_control = _DemoRemote(self)
        self.apps = _DemoApps(self)
        self.keyboard = _DemoKeyboard(self)
        self.power = _DemoPower(self)
        self.metadata = _DemoMetadata(self)
        self.listener = None

    def close(self):
        return None

    @staticmethod
    def config():
        from types import SimpleNamespace as NS
        svc = lambda p: NS(protocol=p, credentials="demo", pairing=NS(name="Mandatory"))
        return NS(
            identifier="demo-apple-tv", name="Demo Apple TV", address="127.0.0.1",
            device_info=NS(model=NS(name="AppleTV4KGen3"), version="18.0"),
            services=[svc(Protocol.AirPlay), svc(Protocol.Companion)],
        )



# ---------------------------------------------------------------------------
# "About what's playing": Wikipedia summary + optional rare facts from Claude.
# Both are OPT-IN (prefs["info_enabled"]) because they send the title of what
# you are watching off the local network. Nothing here runs unless enabled.
# ---------------------------------------------------------------------------

WIKI_UA = "Clicker/1.0 (https://github.com/lightsgoblack/clicker; local Apple TV remote, personal use)"
FACTS_MODEL = "claude-opus-5"
FACTS_SERVICE_URL = "https://clicker-facts.vercel.app/api/facts"  # Colin-hosted, budget-capped; see facts-service/
FACTS_SYSTEM = (
    "You write trivia for a living-room TV remote app. The user tells you what is on screen "
    "(a show, film, video, song, or channel). Reply with 5 genuinely surprising, specific, "
    "little-known facts about that work or its people that you are highly confident are TRUE "
    "and documented (production stories, casting near-misses, real-world consequences, records, "
    "odd coincidences). No spoilers for plot twists. One or two sentences each, plain text, no markdown. "
    "If you do not actually know this work well enough to be sure, return exactly one item that says so "
    "instead of inventing anything. Output ONLY a JSON array of strings."
)


class InfoService:
    def __init__(self, state):
        self.state = state
        self.cache = OrderedDict()  # key -> dict, small LRU
        self.hosted_meter = None

    def _remember(self, key, value):
        self.cache[key] = value
        while len(self.cache) > 60:
            self.cache.popitem(last=False)

    @staticmethod
    async def _wiki_get(session, params, tries=3):
        """GET api.php as JSON, retrying briefly on 429 (Wikipedia throttles quick successive calls)."""
        for attempt in range(tries):
            async with session.get("https://en.wikipedia.org/w/api.php", params=params,
                                   headers={"User-Agent": WIKI_UA}, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    return await r.json()
                if r.status != 429 or attempt == tries - 1:
                    log.info("wikipedia HTTP %s for %s", r.status, params.get("gsrsearch") or params.get("titles"))
                    return None
                wait = float(r.headers.get("Retry-After") or 0) or 1.2 * (attempt + 1)
            await asyncio.sleep(min(wait, 4))
        return None

    @staticmethod
    def _words(s):
        return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 4}

    async def wiki(self, session, query, base=None):
        """Best Wikipedia article for query in one API call (search + intro + thumbnail).
        `base` is the bare title used for the sanity check when `query` carries a hint like "TV series"."""
        if not query or len(query) < 2:
            return None
        base = base or query
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "generator": "search", "gsrsearch": query, "gsrlimit": "4",
            "prop": "extracts|pageimages|description|pageprops",
            "exintro": "1", "explaintext": "1", "exlimit": "4", "exsentences": "4",
            "pithumbsize": "480", "pilicense": "any", "pilimit": "4", "ppprop": "disambiguation",
        }
        data = await self._wiki_get(session, params)
        if not data:
            return None
        pages = data.get("query", {}).get("pages", [])
        pages.sort(key=lambda pg: pg.get("index", 99))
        qw = self._words(base)
        for pg in pages:
            title = pg.get("title", "")
            if "disambiguation" in (pg.get("pageprops") or {}):
                continue
            if qw and not (qw & self._words(title)) and not (qw & self._words(pg.get("extract", "")[:300])):
                continue
            if not pg.get("extract"):
                continue
            thumb = (pg.get("thumbnail") or {}).get("source")
            if not thumb:
                thumb = await self.lead_image(session, title)
            return {
                "title": title,
                "description": pg.get("description"),
                "extract": pg.get("extract"),
                "thumbnail": thumb,
                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            }
        return None

    async def lead_image(self, session, title):
        """First real picture in the article (posters are non-free, so pageimages skips them)."""
        try:
            params = {"action": "query", "format": "json", "formatversion": "2", "titles": title, "prop": "images", "imlimit": "50"}
            data = await self._wiki_get(session, params)
            if not data:
                return None
            pages = data.get("query", {}).get("pages", [])
            names = [im.get("title", "") for pg in pages for im in pg.get("images", [])]
            names = [n for n in names if re.search(r"\.(jpe?g|png|webp|svg)$", n, re.I) and not re.search(r"(icon|symbol|question_book|edit-|wiki|disambig|padlock|nuvola|oojs|commons)", n, re.I)]
            if not names:
                return None
            tw = self._words(re.sub(r"\(.*?\)", "", title)) - {"series", "film", "season", "show"}
            def score(n):
                w = self._words(n)
                return (2 if (tw & w) else 0) + (3 if re.search(r"(poster|cover|title[ _]?card|key[ _]?art|artwork|logo)", n, re.I) else 0) - (1 if n.lower().endswith(".svg") else 0)
            pick = max(names, key=score)
            log.debug("lead image for %r: %d candidates, picked %r", title, len(names), pick)
            # Special:FilePath redirects straight to a rendered thumbnail (SVGs included) with no
            # second API call, which Wikipedia rate-limits when it has to render on demand.
            fname = pick.split(":", 1)[1].replace(" ", "_")
            return "https://en.wikipedia.org/wiki/Special:FilePath/" + urllib.parse.quote(fname) + "?width=480"
        except Exception as e:
            log.info("lead image failed for %r: %s", title, e)
            return None

    def facts_source(self):
        """'own' when the user chose their key (and has one), else 'hosted'."""
        has_key = bool(self.state.prefs.get("anthropic_key"))
        src = self.state.prefs.get("facts_source")
        if not src:
            return "own" if has_key else "hosted"
        if src == "own" and not has_key:
            return "hosted"
        return src

    async def facts_hosted(self, ctx, fresh=False):
        """Five rare facts via Colin's budget-capped service (no key needed)."""
        payload = {**ctx, "fresh": bool(fresh)}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(FACTS_SERVICE_URL, json=payload,
                                        headers={"X-Clicker-Client": APP_VERSION, "User-Agent": WIKI_UA},
                                        timeout=aiohttp.ClientTimeout(total=75)) as r:
                    try:
                        d = await r.json()
                    except Exception:
                        d = {}
        except Exception:
            raise RuntimeError("Could not reach the facts service. Check the internet connection.")
        if not d.get("ok"):
            code = d.get("code")
            msg = d.get("error") or f"Facts service error (HTTP {r.status})."
            if code == "unconfigured":
                msg = "The shared facts service is not switched on yet. Add your own key in Settings, or try later."
            raise RuntimeError(msg)
        self.hosted_meter = {"spent": d.get("spent"), "budget": d.get("budget")}
        return d.get("facts") or []

    async def facts(self, ctx, fresh=False):
        """Five rare facts from Claude: hosted service by default, or the user's own key."""
        if self.facts_source() == "hosted":
            try:
                return await self.facts_hosted(ctx, fresh)
            except RuntimeError as e:
                # Hosted service off or out of budget: quietly use the user's own key if they have one.
                if not self.state.prefs.get("anthropic_key"):
                    raise
                log.info("hosted facts unavailable (%s); using own key", e)
                self.hosted_meter = None
        import anthropic
        key = self.state.prefs.get("anthropic_key") or None
        try:
            client = anthropic.AsyncAnthropic(api_key=key) if key else anthropic.AsyncAnthropic()
        except Exception as e:
            raise RuntimeError("No Claude API key. Add one in Settings.") from e
        desc = ", ".join(f"{k}: {v}" for k, v in ctx.items() if v)
        try:
            resp = await client.messages.create(
                model=FACTS_MODEL,
                max_tokens=2000,
                system=FACTS_SYSTEM,
                extra_body={"output_config": {"effort": "medium"}},  # extra_body works on old and new SDKs alike
                messages=[{"role": "user", "content": f"On screen right now: {desc}"}],
            )
        except anthropic.AuthenticationError:
            raise RuntimeError("No valid Claude API key. Add one in Settings.")
        except anthropic.RateLimitError:
            raise RuntimeError("Claude is rate-limited right now. Try again in a minute.")
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"Claude error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise RuntimeError("Could not reach Claude. Check the internet connection.")
        except Exception as e:
            if "authentication method" in str(e).lower():
                raise RuntimeError("No Claude API key. Add one in Settings.") from e
            raise
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude declined to write facts for this one.")
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        m = re.search(r"\[.*\]", text, re.S)
        try:
            items = json.loads(m.group(0) if m else text)
            items = [str(x).strip() for x in items if str(x).strip()]
        except Exception:
            items = [ln.strip("-•* ").strip() for ln in text.splitlines() if ln.strip()]
        return items[:6]

    async def lookup(self, ctx, fresh=False):
        key = json.dumps(ctx, sort_keys=True)
        if not fresh and key in self.cache:
            return {**self.cache[key], "cached": True}
        out = {"wiki": None, "facts": None, "factsError": None}
        app = (ctx.get("app") or "").lower()
        is_video_platform = app in ("youtube", "youtube tv", "twitch")
        is_music = app in ("music", "apple music", "spotify", "tidal", "pandora", "soundcloud", "amazon music")
        series, title, artist, mtype = ctx.get("series"), ctx.get("title"), ctx.get("artist"), (ctx.get("type") or "").lower()
        is_music = is_music or mtype == "music"
        cands = []  # (query, base)
        if series:
            cands += [(f"{series} TV series", series), (series, series)]
        if artist and is_video_platform:
            cands += [(artist, artist)]
        if title and is_music:
            cands += [(f"{title} song {artist or ''}".strip(), title), (title, title)]
        elif title and not series:
            if mtype == "tv":
                cands += [(f"{title} TV series", title), (f"{title} film", title), (title, title)]
            else:
                cands += [(f"{title} film", title), (f"{title} TV series", title), (title, title)]
        elif title:
            cands += [(title, title)]
        if artist:
            cands += [(artist, artist)]
        seen = set()
        async with aiohttp.ClientSession() as session:
            for q, base in cands:
                if not q or q in seen:
                    continue
                seen.add(q)
                try:
                    hit = await self.wiki(session, q, base)
                except Exception as e:
                    log.info("wiki lookup failed for %r: %s", q, e)
                    hit = None
                if hit:
                    out["wiki"] = hit
                    break
        try:
            out["facts"] = await self.facts(ctx, fresh)
        except RuntimeError as e:
            out["factsError"] = str(e)
        except Exception as e:
            out["factsError"] = f"Facts unavailable: {e}"
        out["source"] = "hosted" if self.hosted_meter else ("own" if self.state.prefs.get("anthropic_key") else "hosted")
        out["meter"] = self.hosted_meter
        if out["facts"]:  # never cache a failure; the next tap should try again
            self._remember(key, out)
        return {**out, "cached": False}



# ---------------------------------------------------------------------------
# Updater: checks GitHub Releases (on launch and daily, or on demand) and can
# swap the running app in place. The check sends nothing but a request for the
# release list. Opt out with prefs["update_check"] = false.
# ---------------------------------------------------------------------------

def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def app_bundle():
    """Path to the .app we are running from, or None (dev checkout / plain script)."""
    for start in (Path(sys.executable).resolve(), HERE.resolve()):
        for parent in [start] + list(start.parents):
            if parent.suffix == ".app" and (parent / "Contents" / "MacOS").is_dir():
                return parent
    return None


class Updater:
    def __init__(self, state):
        self.state = state
        self.latest = None       # dict from last successful check
        self.checked_at = 0
        self.error = None
        self.busy = False
        self.progress = ""

    def enabled(self):
        return self.state.prefs.get("update_check", True)

    async def check(self, force=False):
        if not force and self.latest and time.time() - self.checked_at < 3600:
            return self.summary()
        url = os.environ.get("CLICKER_RELEASES_URL") or f"https://api.github.com/repos/{REPO}/releases/latest"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"User-Agent": WIKI_UA, "Accept": "application/vnd.github+json"},
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 404:
                        raise RuntimeError("No public release found (is the repo private?).")
                    if r.status != 200:
                        raise RuntimeError(f"GitHub answered HTTP {r.status}.")
                    d = await r.json()
            arch = platform.machine()
            asset = next((a for a in d.get("assets", []) if a.get("name") == f"Clicker-mac-{arch}.zip"), None)
            self.latest = {
                "version": d.get("tag_name", "").lstrip("v"),
                "notes": (d.get("body") or "").strip(),
                "page": d.get("html_url"),
                "asset": asset.get("browser_download_url") if asset else None,
                "size": asset.get("size") if asset else None,
                "tarball": d.get("tarball_url"),
            }
            self.checked_at = time.time()
            self.error = None
        except Exception as e:
            self.error = str(e)
            log.info("Update check failed: %s", e)
        return self.summary()

    def summary(self):
        dev_checkout = (HERE / ".git").exists()
        out = {"current": APP_VERSION, "checkedAt": self.checked_at, "error": self.error,
               "busy": self.busy, "progress": self.progress, "enabled": self.enabled(),
               "canSelfUpdate": app_bundle() is not None and not dev_checkout}
        if self.latest:
            out["latest"] = self.latest["version"]
            out["notes"] = self.latest["notes"]
            out["page"] = self.latest["page"]
            out["available"] = _vtuple(self.latest["version"]) > _vtuple(APP_VERSION)
        else:
            out["available"] = False
        return out

    async def apply(self):
        """Download the newest release and replace this app in place, then relaunch."""
        if self.busy:
            raise RuntimeError("An update is already in progress.")
        if not self.latest or not self.summary()["available"]:
            await self.check(force=True)
            if not self.latest or not self.summary()["available"]:
                raise RuntimeError(self.error or "Already on the newest version.")
        bundle = app_bundle()
        if bundle is None or (HERE / ".git").exists():
            raise RuntimeError("This copy runs from source. Update it with git pull or the installer line.")
        self.busy = True
        try:
            tmp = Path(tempfile.mkdtemp(prefix="clicker-update-"))
            if FROZEN:
                if not self.latest.get("asset"):
                    raise RuntimeError(f"The release has no download for this Mac ({platform.machine()}).")
                await self._download(self.latest["asset"], tmp / "Clicker.zip", self.latest.get("size"))
                self.progress = "Unpacking…"
                subprocess.run(["ditto", "-xk", str(tmp / "Clicker.zip"), str(tmp / "unz")], check=True, capture_output=True)
                new_app = tmp / "unz" / "Clicker.app"
                if not (new_app / "Contents" / "MacOS" / "Clicker").exists():
                    raise RuntimeError("Downloaded app looks incomplete; not installing it.")
                self.progress = "Installing…"
                old = tmp / "old.app"
                shutil.move(str(bundle), str(old))
                try:
                    subprocess.run(["ditto", str(new_app), str(bundle)], check=True, capture_output=True)
                except Exception:
                    shutil.move(str(old), str(bundle))  # roll back
                    raise
            else:
                # Script-built app (install.sh): replace the files inside Contents/Resources/app.
                await self._download(self.latest["tarball"], tmp / "src.tgz", None)
                subprocess.run(["tar", "-xzf", str(tmp / "src.tgz"), "-C", str(tmp)], check=True, capture_output=True)
                src = next(p for p in tmp.iterdir() if p.is_dir() and p.name.startswith("lightsgoblack-clicker"))
                self.progress = "Installing…"
                for name in ("server.py", "index.html", "start.command", "apple-touch-icon.png", "icon-512.png", "manifest.webmanifest"):
                    shutil.copy2(src / name, HERE / name)
                shutil.copy2(src / "mac" / "launcher.sh", bundle / "Contents" / "MacOS" / "Clicker")
                shutil.copy2(src / "mac" / "Info.plist", bundle / "Contents" / "Info.plist")
                shutil.copy2(src / "mac" / "AppIcon.icns", bundle / "Contents" / "Resources" / "AppIcon.icns")
            self.progress = "Restarting…"
            subprocess.Popen(["/bin/sh", "-c", f'sleep 1.5; open -a "{bundle}" --args --no-open'],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            asyncio.get_event_loop().call_later(0.6, lambda: os.kill(os.getpid(), signal.SIGTERM))
        except Exception:
            self.busy = False
            self.progress = ""
            raise

    async def _download(self, url, dest, expected_size):
        self.progress = "Downloading…"
        if not url.startswith(("https://github.com/", "https://api.github.com/", "https://objects.githubusercontent.com/")) and not os.environ.get("CLICKER_RELEASES_URL"):
            raise RuntimeError("Refusing to download from an unexpected host.")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": WIKI_UA}, timeout=aiohttp.ClientTimeout(total=600)) as r:
                if r.status != 200:
                    raise RuntimeError(f"Download failed (HTTP {r.status}).")
                got = 0
                with open(dest, "wb") as fh:
                    async for chunk in r.content.iter_chunked(1 << 16):
                        fh.write(chunk)
                        got += len(chunk)
                        if expected_size:
                            self.progress = f"Downloading… {int(got * 100 / expected_size)}%"
        if expected_size and got != expected_size:
            raise RuntimeError("Download was incomplete. Try again.")


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

def routes(state: State):
    r = web.RouteTableDef()

    def need_device():
        if state.atv is None:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"ok": False, "error": "Not connected to an Apple TV."}),
                content_type="application/json",
            )
        return state.atv

    @r.get("/")
    async def index(_):
        return web.FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})

    STATIC = {
        "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
        "/icon-512.png": ("icon-512.png", "image/png"),
        "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    }

    @r.get("/apple-touch-icon.png")
    @r.get("/apple-touch-icon-precomposed.png")
    @r.get("/icon-512.png")
    @r.get("/manifest.webmanifest")
    async def static(req):
        name, ctype = STATIC.get(req.path, STATIC["/apple-touch-icon.png"])
        return web.FileResponse(HERE / name, headers={"Content-Type": ctype, "Cache-Control": "no-cache"})

    @r.get("/api/status")
    async def status(_):
        return web.json_response({"ok": True, **(await state.status())})

    @r.get("/api/scan")
    async def scan(req):
        timeout = int(req.query.get("timeout", "5"))
        try:
            found = await state.scan(timeout=timeout)
        except Exception as e:
            return json_error(f"Scan failed: {e}", 500)
        return web.json_response({"ok": True, "devices": [state.describe(c) for c in found]})

    @r.post("/api/connect")
    async def connect(req):
        body = await req.json()
        try:
            await state.connect(body.get("identifier"))
        except exceptions.AuthenticationError as e:
            return json_error(f"Not paired yet ({e}). Pair first.", 401)
        except Exception as e:
            return json_error(f"Could not connect: {e}", 500)
        return web.json_response({"ok": True, **(await state.status())})

    @r.post("/api/disconnect")
    async def disconnect(_):
        await state.disconnect()
        return web.json_response({"ok": True})

    @r.post("/api/pair/start")
    async def pair_start(req):
        body = await req.json()
        proto = body.get("protocol", "airplay")
        if proto not in PROTOCOLS:
            return json_error("Unknown protocol.")
        try:
            info = await state.pair_start(body.get("identifier"), proto)
        except Exception as e:
            return json_error(f"Could not start pairing: {e}", 500)
        return web.json_response({"ok": True, "protocol": proto, **info})

    @r.post("/api/pair/finish")
    async def pair_finish(req):
        body = await req.json()
        try:
            ok = await state.pair_finish(body.get("pin"))
        except Exception as e:
            return json_error(f"Pairing failed: {e}", 400)
        if not ok:
            return json_error("Pairing did not complete. Check the PIN and try again.", 400)
        return web.json_response({"ok": True})

    @r.post("/api/pair/cancel")
    async def pair_cancel(_):
        await state.pair_cancel()
        return web.json_response({"ok": True})

    @r.post("/api/forget")
    async def forget(req):
        body = await req.json()
        try:
            await state.forget(body.get("identifier"))
        except Exception as e:
            return json_error(str(e), 400)
        return web.json_response({"ok": True})

    @r.post("/api/cmd")
    async def cmd(req):
        atv = need_device()
        body = await req.json()
        name = body.get("name")
        if name not in COMMANDS:
            return json_error(f"Unknown command: {name}")
        method, takes_action = COMMANDS[name]
        action = ACTIONS.get(body.get("action", "tap"), InputAction.SingleTap)
        try:
            fn = getattr(atv.remote_control, method)
            if takes_action:
                await fn(action=action)
            else:
                await fn()
        except exceptions.NotSupportedError:
            return json_error(f"'{name}' is not supported by this device or protocol.", 501)
        except Exception as e:
            return json_error(f"{name} failed: {e}", 500)
        return web.json_response({"ok": True})

    @r.get("/api/apps")
    async def apps(_):
        atv = need_device()
        try:
            lst = await atv.apps.app_list()
        except exceptions.NotSupportedError:
            return json_error("Listing apps needs Companion pairing.", 501)
        except Exception as e:
            return json_error(f"Could not list apps: {e}", 500)
        apps_ = sorted(
            [{"name": a.name, "identifier": a.identifier} for a in lst],
            key=lambda a: (a["name"] or "").lower(),
        )
        return web.json_response({"ok": True, "apps": apps_})

    @r.post("/api/launch")
    async def launch(req):
        atv = need_device()
        body = await req.json()
        target = (body.get("target") or "").strip()
        if not target:
            return json_error("Nothing to launch.")
        try:
            await atv.apps.launch_app(target)
        except exceptions.NotSupportedError:
            return json_error("Launching apps needs Companion pairing.", 501)
        except Exception as e:
            return json_error(f"Launch failed: {e}", 500)
        return web.json_response({"ok": True})

    @r.post("/api/text")
    async def text(req):
        atv = need_device()
        body = await req.json()
        txt = body.get("text", "")
        try:
            if txt == "":
                await atv.keyboard.text_clear()
            else:
                await atv.keyboard.text_set(txt)
        except exceptions.NotSupportedError:
            return json_error("Typing needs Companion pairing.", 501)
        except Exception as e:
            return json_error(f"Typing failed: {e}. Is a text field focused on the TV?", 500)
        return web.json_response({"ok": True})

    @r.post("/api/power")
    async def power(req):
        atv = need_device()
        body = await req.json()
        try:
            if body.get("on"):
                await atv.power.turn_on()
            else:
                await atv.power.turn_off()
        except exceptions.NotSupportedError:
            return json_error("Power control needs Companion pairing.", 501)
        except Exception as e:
            return json_error(f"Power failed: {e}", 500)
        return web.json_response({"ok": True})

    @r.get("/api/prefs")
    async def prefs_get(_):
        k = state.prefs.get("anthropic_key") or ""
        return web.json_response({"ok": True, "infoEnabled": bool(state.prefs.get("info_enabled")),
                                  "hasKey": bool(k), "keyHint": ("…" + k[-4:]) if k else "",
                                  "updateCheck": bool(state.prefs.get("update_check", True)), "version": APP_VERSION,
                                  "factsSource": state.prefs.get("facts_source") or "hosted"})

    @r.post("/api/prefs")
    async def prefs_set(req):
        body = await req.json()
        if "infoEnabled" in body:
            state.prefs["info_enabled"] = bool(body["infoEnabled"])
        if "factsSource" in body and body["factsSource"] in ("hosted", "own"):
            state.prefs["facts_source"] = body["factsSource"]
        if "updateCheck" in body:
            state.prefs["update_check"] = bool(body["updateCheck"])
        if "anthropicKey" in body:
            k = (body.get("anthropicKey") or "").strip()
            if k:
                state.prefs["anthropic_key"] = k
            else:
                state.prefs.pop("anthropic_key", None)
        state.save_prefs()
        return await prefs_get(req)

    @r.get("/api/info")
    async def info(req):
        if not state.prefs.get("info_enabled"):
            return json_error("Info lookups are turned off. Enable them in Settings.", 403)
        q = req.query
        ctx = {k: (q.get(k) or "").strip() for k in ("title", "series", "artist", "app", "season", "episode", "type")}
        ctx = {k: v for k, v in ctx.items() if v}
        if not ctx.get("title") and not ctx.get("series") and not ctx.get("artist"):
            return json_error("Nothing is playing to look up.")
        try:
            data = await state.info.lookup(ctx, fresh=q.get("fresh") == "1")
        except Exception as e:
            return json_error(f"Lookup failed: {e}", 500)
        return web.json_response({"ok": True, **data})

    @r.get("/api/history")
    async def history(_):
        items = [{**h, "link": state.deep_link(h)} for h in state.history[:5]]
        return web.json_response({"ok": True, "items": items})

    @r.post("/api/history/remove")
    async def history_remove(req):
        body = await req.json()
        key = (body.get("series") or body.get("title"), body.get("app"))
        state.history = [h for h in state.history if (h.get("series") or h.get("title"), h.get("app")) != key]
        try:
            state.history_path.write_text(json.dumps(state.history))
        except Exception:
            pass
        return web.json_response({"ok": True})

    @r.post("/api/sleep")
    async def sleep(req):
        body = await req.json()
        try:
            await state.sleep_set(int(body.get("minutes") or 0))
        except Exception as e:
            return json_error(str(e))
        return web.json_response({"ok": True, "sleepAt": state.sleep_at})

    @r.post("/api/search")
    async def search(req):
        atv = need_device()
        body = await req.json()
        q = (body.get("q") or "").strip()
        if not q:
            return json_error("Type something to search for.")
        try:
            await atv.apps.launch_app("com.apple.TVSearch")
            await asyncio.sleep(3.0)
            await atv.keyboard.text_set(q)
            await asyncio.sleep(0.8)
            await atv.remote_control.select()
        except exceptions.NotSupportedError:
            return json_error("Search needs the apps pairing.", 501)
        except Exception as e:
            return json_error(f"Search failed: {e}", 500)
        return web.json_response({"ok": True})

    @r.get("/api/stats")
    async def stats(req):
        return web.json_response({"ok": True, **state.stats_summary(int(req.query.get("days", "30")))})

    @r.get("/api/devices/known")
    async def known(_):
        cur = state.config.identifier if state.config else None
        devs = [k for k in state.prefs.get("known", []) if not str(k.get("identifier", "")).startswith("demo")]
        return web.json_response({"ok": True, "current": cur, "devices": devs})

    # GET twins of the two commands Shortcuts and Siri most want, so a plain URL is enough.
    @r.get("/api/do/cmd")
    async def do_cmd(req):
        atv = need_device()
        name = req.query.get("name", "")
        if name not in COMMANDS:
            return json_error(f"Unknown command: {name}")
        method, takes_action = COMMANDS[name]
        try:
            fn = getattr(atv.remote_control, method)
            await (fn(action=InputAction.SingleTap) if takes_action else fn())
        except Exception as e:
            return json_error(f"{name} failed: {e}", 500)
        return web.json_response({"ok": True})

    @r.get("/api/do/launch")
    async def do_launch(req):
        atv = need_device()
        target = (req.query.get("target") or "").strip()
        if not target:
            return json_error("Nothing to launch.")
        try:
            await atv.apps.launch_app(target)
        except Exception as e:
            return json_error(f"Launch failed: {e}", 500)
        return web.json_response({"ok": True})

    @r.get("/api/kid")
    async def kid_get(_):
        return web.json_response({"ok": True, "on": bool(state.prefs.get("kid_mode")), "hasPin": bool(state.prefs.get("kid_pin"))})

    @r.post("/api/kid")
    async def kid_set(req):
        body = await req.json()
        pin = str(body.get("pin") or "").strip()
        if body.get("on"):
            if not state.prefs.get("kid_pin"):
                if not re.fullmatch(r"\d{4,8}", pin):
                    return json_error("Choose a PIN of 4 to 8 digits first.")
                state.prefs["kid_pin"] = state.pin_hash(pin)
            state.prefs["kid_mode"] = True
        else:
            if not state.kid_check(pin):
                return json_error("Wrong PIN.", 403)
            state.prefs["kid_mode"] = False
        if body.get("newPin") is not None and (not state.prefs.get("kid_mode") or state.kid_check(pin)):
            np_ = str(body.get("newPin")).strip()
            if not re.fullmatch(r"\d{4,8}", np_):
                return json_error("A PIN is 4 to 8 digits.")
            state.prefs["kid_pin"] = state.pin_hash(np_)
        state.save_prefs()
        return await kid_get(req)

    @r.get("/api/party")
    async def party_get(req):
        return web.json_response({"ok": True, "on": bool(state.party_token()), "url": state.party_url(req.url.port or PORT)})

    @r.post("/api/party")
    async def party_set(req):
        body = await req.json()
        if body.get("on"):
            if not state.party_token():
                state.prefs["party_token"] = secrets.token_urlsafe(12)
        else:
            state.prefs.pop("party_token", None)
        state.save_prefs()
        return await party_get(req)

    @r.get("/api/party/qr")
    async def party_qr(req):
        url = state.party_url(req.url.port or PORT)
        if not url:
            return json_error("Party mode is off.")
        import segno
        svg = segno.make(url, error="m").svg_inline(scale=6, dark="#000", light=None)
        return web.Response(text=svg, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @r.get("/api/update/check")
    async def update_check(req):
        return web.json_response({"ok": True, **(await state.updater.check(force=req.query.get("force") == "1"))})

    @r.post("/api/update/apply")
    async def update_apply(_):
        try:
            await state.updater.apply()
        except Exception as e:
            return json_error(str(e), 400)
        return web.json_response({"ok": True})

    @r.post("/api/quit")
    async def quit_(_):
        loop = asyncio.get_event_loop()
        loop.call_later(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM))
        return web.json_response({"ok": True})

    @r.get("/api/demo/log")
    async def demo_log(_):
        if not state.demo or state.atv is None:
            return json_error("Not in demo mode.")
        return web.json_response({"ok": True, "log": state.atv.log})

    return r


KID_BLOCKED = {"/api/prefs", "/api/quit", "/api/update/apply", "/api/text", "/api/search", "/api/party", "/api/forget",
               "/api/pair/start", "/api/pair/finish", "/api/disconnect", "/api/launch", "/api/sleep", "/api/history/remove"}


@web.middleware
async def party_gate(request, handler):
    """Localhost always works. Other devices on the Wi-Fi need party mode on and the scanned token."""
    peer = request.remote or ""
    try:
        local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local = peer in ("localhost", "")
    state = request.app["state"]
    if state.prefs.get("kid_mode") and request.method == "POST" and request.path in KID_BLOCKED:
        return web.json_response({"ok": False, "error": "Kid mode is on. Unlock it with the PIN first."}, status=423)
    if local:
        return await handler(request)
    tok = state.party_token()
    if tok and request.cookies.get("clicker_party") == tok:
        return await handler(request)
    if tok and (request.headers.get("X-Party-Token") == tok or (request.path.startswith("/api/") and request.query.get("party") == tok)):
        return await handler(request)
    if tok and request.query.get("party") == tok:
        resp = web.HTTPFound("/")
        resp.set_cookie("clicker_party", tok, max_age=30 * 86400, httponly=True, samesite="Lax")
        return resp
    if request.path.startswith("/api/"):
        return web.json_response({"ok": False, "error": "Party mode is off on the host Mac."}, status=403)
    return web.Response(text="<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><body style='font:17px -apple-system,sans-serif;background:#0b0c0f;color:#e8e9ec;display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px'><div><div style='font-size:44px'>🎉</div><h2>Party mode is off</h2><p style='color:#8b909c'>Ask whoever runs Clicker to turn on Party mode in Settings and scan the code again.</p></div></body>", content_type="text/html", status=403)


async def make_app(state: State):
    await state.init_storage()
    app = web.Application(middlewares=[party_gate])
    app["state"] = state
    app.add_routes(routes(state))

    async def auto_connect():
        # Runs in the background so the port opens immediately; the UI polls
        # /api/status and flips to "connected" when this finishes.
        last = state.prefs.get("last")
        if state.demo or not last:
            return
        try:
            await state.scan(timeout=4)
            await state.connect(last)
            log.info("Reconnected to %s", state.config.name)
        except Exception as e:
            log.info("Auto-connect skipped: %s", e)

    async def watch_loop():
        # Every 10s, note what is playing (history + stats), whether or not a page is open.
        while True:
            await asyncio.sleep(10)
            atv = state.atv
            if atv is None:
                continue
            try:
                playing = await atv.metadata.playing()
                p = {"state": playing.device_state.name.lower(), "title": playing.title, "series": playing.series_name,
                     "artist": playing.artist, "season": playing.season_number, "episode": playing.episode_number,
                     "position": playing.position, "total": playing.total_time, "mediaType": playing.media_type.name.lower(),
                     "contentId": getattr(playing, "content_identifier", None)}
                app = getattr(atv.metadata, "app", None)
                state.record_history(p, app)
                state.record_stats(p, app, 10)
            except Exception:
                pass

    async def update_loop():
        await asyncio.sleep(8)
        while True:
            if state.updater.enabled() and not state.demo:
                await state.updater.check()
            await asyncio.sleep(24 * 3600)

    async def on_startup(_):
        asyncio.get_event_loop().create_task(auto_connect())
        asyncio.get_event_loop().create_task(update_loop())
        asyncio.get_event_loop().create_task(watch_loop())

    async def on_cleanup(_):
        await state.pair_cancel()
        await state.disconnect()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    ap = argparse.ArgumentParser(description="Clicker: local web remote for Apple TV")
    ap.add_argument("--demo", action="store_true", help="fake device, no Apple TV needed")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (0.0.0.0 = reachable from your phone on the same Wi-Fi)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        logging.getLogger("pyatv").setLevel(logging.WARNING)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state = State(loop, demo=args.demo)
    app = loop.run_until_complete(make_app(state))

    url = f"http://localhost:{args.port}/"
    # Already running (double-launch from the Dock, a second start.command)? Just open the page.
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/status", timeout=1) as r:
            if json.loads(r.read().decode()).get("ok"):
                print(f"\n  Clicker is already running at {url}\n")
                if not args.no_open:
                    webbrowser.open(url)
                return
    except Exception:
        pass
    print(f"\n  Clicker is running at {url}{'  (demo mode)' if args.demo else ''}\n  Press Ctrl+C to stop.\n")
    if not args.no_open:
        loop.call_later(0.8, webbrowser.open, url)
    try:
        web.run_app(app, host=args.host, port=args.port, loop=loop, print=None, access_log=None)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
