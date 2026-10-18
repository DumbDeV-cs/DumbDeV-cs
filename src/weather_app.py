#!/usr/bin/env python3
"""
Stealth Weather Pro
===================
A polished desktop weather app (Open-Meteo, no API key) with a hidden,
encrypted vault that bookmarks file/folder LOCATIONS (never file contents).

Install:
    pip install customtkinter requests Pillow cryptography

Run:
    python stealth_weather_pro.py

How it works
------------
* First launch: you create your own secret access code (nothing is hard-coded).
  Only a salted PBKDF2-SHA256 verifier is stored, never the code itself.
* Typing the exact code into the city search box and pressing Enter (or Search)
  opens the vault. The check happens locally BEFORE any network call.
* Anything else is a normal city search ("Location not found" if no match).
* The vault is a Fernet-encrypted file keyed from your code. It stores paths,
  display names, dates, tags and notes. No file contents are ever copied.

Access-code rules (and why):
* At least 6 characters.
* Must START with a digit or symbol (e.g. "#Moon4721"). City names start with a
  letter, so the app never sends anything that starts with a non-letter to the
  autocomplete service. That keeps your code from leaking to a third party while
  you type it.

Forgot your code? There is no recovery by design. Delete the folder
~/.wx_pro (or set WX_PRO_HOME to relocate it) to reset; the vault is lost.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
import tkinter.font as tkfont

try:
    import customtkinter as ctk
    import requests
    from cryptography.fernet import Fernet, InvalidToken
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"Missing dependency: {exc.name}\n"
        "Install everything with:\n"
        "    pip install customtkinter requests Pillow cryptography"
    )

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # icon is optional
    Image = None

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
APP_DIR = Path(os.environ.get("WX_PRO_HOME", Path.home() / ".wx_pro"))
CONFIG_PATH = APP_DIR / ".config.json"
VAULT_PATH = APP_DIR / "forecast_cache.bin"  # looks like a weather cache

PBKDF2_ITERS = 300_000
IDLE_LOCK_MS = 5 * 60 * 1000       # vault auto-locks after 5 idle minutes
REFRESH_MS = 10 * 60 * 1000        # weather auto-refresh
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WHITE = "#FFFFFF"
MUTED = "#CFD8E6"

# Vault palette
V_BG, V_CARD, V_LINE = "#0F1218", "#171B24", "#262C3A"
V_TEXT, V_MUTED = "#E8ECF4", "#8B94A7"
V_ACCENT, V_DANGER = "#4C8DFF", "#E5484D"


# ----------------------------------------------------------------------------
# SMALL UTILITIES
# ----------------------------------------------------------------------------
def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def font(size: int, weight: str = "normal") -> "ctk.CTkFont":
    return ctk.CTkFont(size=size, weight=weight)


def is_ctk(w) -> bool:
    return hasattr(w, "_bg_color") and hasattr(w, "_apply_appearance_mode")


def hide_on_windows(path: Path):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
        except Exception:
            pass


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def reveal_in_file_manager(path: str) -> str | None:
    """Open Explorer/Finder/file manager at `path`. Returns a note if a fallback was used."""
    p = Path(path)
    note = None
    target = p
    if not p.exists():
        parent = next((a for a in p.parents if a.exists()), None)
        if parent is None:
            raise FileNotFoundError("Neither the item nor any parent folder exists.")
        target, note = parent, "Item not found; opened the nearest existing folder."
    system = platform.system()
    if system == "Windows":
        if target.is_dir():
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer", f"/select,{target}"])
    elif system == "Darwin":
        subprocess.Popen(["open", "-R", str(target)] if target.is_file() else ["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
    return note


# ----------------------------------------------------------------------------
# SECURITY: PIN hashing + encrypted vault
# ----------------------------------------------------------------------------
def validate_pin(pin: str, confirm: str | None = None) -> str | None:
    if len(pin) < 6:
        return "Use at least 6 characters."
    if pin != pin.strip():
        return "No spaces at the start or end."
    if pin[0].isalpha():
        return "Must start with a digit or symbol (e.g. #Moon4721)."
    if confirm is not None and pin != confirm:
        return "The two codes don't match."
    return None


class Security:
    """Stores a salted PBKDF2 verifier plus non-sensitive prefs in .config.json."""

    def __init__(self):
        self.cfg: dict = {}

    @property
    def configured(self) -> bool:
        return "verifier" in self.cfg

    def load(self):
        try:
            self.cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cfg = {}

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        hide_on_windows(APP_DIR)
        atomic_write(CONFIG_PATH, json.dumps(self.cfg).encode("utf-8"))

    @staticmethod
    def derive(pin: str, salt: bytes, iters: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iters, 32)

    @staticmethod
    def verifier(key: bytes) -> str:
        return hashlib.sha256(b"wx-verify" + key).hexdigest()

    def set_pin(self, pin: str) -> bytes:
        salt = os.urandom(16)
        key = self.derive(pin, salt, PBKDF2_ITERS)
        self.cfg.update(
            v=1,
            salt=base64.b64encode(salt).decode(),
            iters=PBKDF2_ITERS,
            verifier=self.verifier(key),
        )
        self.save()
        return key

    def check_pin(self, pin: str) -> bytes | None:
        if not self.configured:
            return None
        try:
            salt = base64.b64decode(self.cfg["salt"])
            key = self.derive(pin, salt, int(self.cfg["iters"]))
        except Exception:
            return None
        if hmac.compare_digest(self.verifier(key), self.cfg["verifier"]):
            return key
        return None

    def update_prefs(self, **kw):
        self.cfg.update(kw)
        if self.configured:
            self.save()


class Vault:
    """Encrypted list of path bookmarks. Contents of files are never stored."""

    def __init__(self, key: bytes):
        self.key = key
        self.entries: list[dict] = []
        self._load()

    def _fernet(self) -> Fernet:
        return Fernet(base64.urlsafe_b64encode(self.key))

    def _load(self):
        if VAULT_PATH.exists():
            plain = self._fernet().decrypt(VAULT_PATH.read_bytes())  # InvalidToken if wrong key
            self.entries = json.loads(plain.decode("utf-8"))

    def save(self):
        blob = self._fernet().encrypt(json.dumps(self.entries).encode("utf-8"))
        atomic_write(VAULT_PATH, blob)
        hide_on_windows(VAULT_PATH)

    def add_path(self, path: str) -> bool:
        path = os.path.abspath(path)
        if any(e["path"] == path for e in self.entries):
            return False
        self.entries.append(
            {
                "id": uuid.uuid4().hex[:12],
                "name": os.path.basename(path.rstrip("\\/")) or path,
                "path": path,
                "kind": "folder" if os.path.isdir(path) else "file",
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "tags": "",
                "note": "",
            }
        )
        self.save()
        return True

    def get(self, eid: str) -> dict | None:
        return next((e for e in self.entries if e["id"] == eid), None)

    def update(self, eid: str, **fields):
        e = self.get(eid)
        if e:
            e.update(fields)
            self.save()

    def remove(self, ids):
        ids = set(ids)
        self.entries = [e for e in self.entries if e["id"] not in ids]
        self.save()

    def rekey(self, new_key: bytes):
        self.key = new_key
        self.save()


# ----------------------------------------------------------------------------
# WEATHER DATA (Open-Meteo)
# ----------------------------------------------------------------------------
class WeatherError(Exception):
    pass


@dataclass
class Place:
    name: str
    admin: str
    country: str
    lat: float
    lon: float
    population: int = 0
    feature: str = ""

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.admin, self.country) if p)


def http_get_json(url: str, params: dict) -> dict:
    try:
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "StealthWeatherPro/1.0"})
    except requests.exceptions.ConnectionError:
        raise WeatherError("No internet connection. Check your network and try again.")
    except requests.exceptions.Timeout:
        raise WeatherError("The weather service took too long to respond.")
    except requests.RequestException:
        raise WeatherError("Couldn't reach the weather service.")
    if r.status_code != 200:
        raise WeatherError(f"Weather service error ({r.status_code}). Try again shortly.")
    try:
        return r.json()
    except ValueError:
        raise WeatherError("Received an unreadable response from the weather service.")


def search_places(query: str, count: int = 6) -> list[Place]:
    base, _, rest = query.partition(",")  # "Paris, France" -> search "Paris", rank by "france"
    base, rest = base.strip(), rest.strip().lower()
    data = http_get_json(
        GEOCODE_URL,
        {"name": base, "count": max(15, count * 3), "language": "en", "format": "json"},
    )
    out, seen = [], set()
    for r in data.get("results") or []:
        p = Place(r.get("name", ""), r.get("admin1") or r.get("admin2") or "", r.get("country") or "", r["latitude"], r["longitude"], int(r.get("population") or 0), r.get("feature_code") or "")
        if p.label in seen:
            continue
        seen.add(p.label)
        out.append(p)
    # Rank exact and prefix matches first, then well-known populated places. A
    # typed country/region after a comma is treated as a strong disambiguator.
    query_lower = base.lower()
    out.sort(key=lambda p: (
        0 if p.name.lower() == query_lower else 1 if p.name.lower().startswith(query_lower) else 2,
        0 if rest and rest in p.label.lower() else 1 if rest else 0,
        -(p.population or 0), p.label.lower()
    ))
    return out[:count]


# WMO weather codes -> (label, kind, emoji)
def describe(code: int, is_day: bool = True):
    table = {
        0: ("Clear sky", "clear"), 1: ("Mainly clear", "clear"), 2: ("Partly cloudy", "clouds"),
        3: ("Overcast", "clouds"), 45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
        51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
        56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
        61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
        66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
        71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
        77: ("Snow grains", "snow"), 80: ("Rain showers", "rain"), 81: ("Rain showers", "rain"),
        82: ("Violent showers", "rain"), 85: ("Snow showers", "snow"), 86: ("Heavy snow showers", "snow"),
        95: ("Thunderstorm", "storm"), 96: ("Thunderstorm, hail", "storm"), 99: ("Thunderstorm, hail", "storm"),
    }
    label, kind = table.get(code, ("Unknown", "clouds"))
    emoji = {
        "clear": "☀️" if is_day else "🌙",
        "clouds": ("⛅" if code == 2 else "☁️") if is_day else "☁️",
        "fog": "🌫️", "rain": "🌧️", "snow": "❄️", "storm": "⛈️",
    }[kind]
    return label, kind, emoji


THEMES = {
    ("clear", True): "#2E7BE0", ("clear", False): "#0E1633",
    ("clouds", True): "#5A6F8A", ("clouds", False): "#1D2433",
    ("rain", True): "#33475E", ("rain", False): "#18202C",
    ("snow", True): "#7E96B0", ("snow", False): "#2A3547",
    ("storm", True): "#2A2D45", ("storm", False): "#15162A",
    ("fog", True): "#6A7683", ("fog", False): "#242A33",
}


def fetch_forecast(place: Place, units: str) -> dict:
    metric = units == "metric"
    j = http_get_json(
        FORECAST_URL,
        {
            "latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": 7,
            "temperature_unit": "celsius" if metric else "fahrenheit",
            "wind_speed_unit": "kmh" if metric else "mph",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                       "wind_speed_10m,wind_direction_10m",
            "hourly": "temperature_2m,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max",
        },
    )
    try:
        cur, hr, dy = j["current"], j["hourly"], j["daily"]
        start_key = cur["time"][:13] + ":00"
        start = next((i for i, t in enumerate(hr["time"]) if t >= start_key), 0)
        hourly = [
            {"time": hr["time"][i], "temp": hr["temperature_2m"][i], "code": hr["weather_code"][i],
             "is_day": bool(hr["is_day"][i])}
            for i in range(start, min(start + 8, len(hr["time"])))
        ]
        daily = [
            {"date": dy["time"][i], "code": dy["weather_code"][i], "hi": dy["temperature_2m_max"][i],
             "lo": dy["temperature_2m_min"][i]}
            for i in range(len(dy["time"]))
        ]
        return {
            "time": cur["time"], "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
            "humidity": cur["relative_humidity_2m"], "wind": cur["wind_speed_10m"],
            "wind_dir": cur.get("wind_direction_10m"), "code": cur["weather_code"],
            "is_day": bool(cur["is_day"]), "hi": dy["temperature_2m_max"][0], "lo": dy["temperature_2m_min"][0],
            "uv": dy["uv_index_max"][0], "sunrise": dy["sunrise"][0], "sunset": dy["sunset"][0],
            "hourly": hourly, "daily": daily,
        }
    except (KeyError, IndexError, TypeError):
        raise WeatherError("The weather service returned incomplete data.")


def fmt_clock(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%I:%M %p").lstrip("0")


def fmt_local(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a, %b %d · ") + dt.strftime("%I:%M %p").lstrip("0")


def compass(deg) -> str:
    if deg is None:
        return "—"
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg % 360) / 45 + 0.5) % 8]


def uv_label(uv) -> str:
    if uv is None:
        return "No data"
    return "Low" if uv < 3 else "Moderate" if uv < 6 else "High" if uv < 8 else "Very high" if uv < 11 else "Extreme"


# ----------------------------------------------------------------------------
# ANIMATED WIDGETS
# ----------------------------------------------------------------------------
class WeatherIcon(tk.Canvas):
    """Animated weather illustration drawn with canvas primitives."""

    def __init__(self, master, size=150, bg="#000000"):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        self.size, self.kind, self.is_day, self.t, self._job = size, "clear", True, 0, None
        self._tick()

    def set_state(self, kind: str, is_day: bool):
        self.kind, self.is_day = kind, is_day

    def destroy(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        super().destroy()

    def _tick(self):
        try:
            self.t += 1
            self._draw()
            self._job = self.after(40, self._tick)
        except tk.TclError:
            pass

    # primitives -----------------------------------------------------------
    def _cloud(self, cx, cy, s, fill=WHITE):
        c = self.create_oval
        c(cx - 40 * s, cy - 6 * s, cx - 4 * s, cy + 30 * s, fill=fill, outline="")
        c(cx - 24 * s, cy - 30 * s, cx + 22 * s, cy + 16 * s, fill=fill, outline="")
        c(cx + 4 * s, cy - 12 * s, cx + 42 * s, cy + 30 * s, fill=fill, outline="")
        self.create_rectangle(cx - 22 * s, cy + 4 * s, cx + 24 * s, cy + 30 * s, fill=fill, outline="")

    def _sun(self, x, y, s):
        for i in range(12):
            a = self.t * 0.02 + i * math.pi / 6
            r1, r2 = 36 * s, (46 + 3 * math.sin(self.t * 0.1 + i)) * s
            self.create_line(x + r1 * math.cos(a), y + r1 * math.sin(a), x + r2 * math.cos(a), y + r2 * math.sin(a),
                             fill="#FFD43B", width=max(2, 4 * s), capstyle="round")
        self.create_oval(x - 26 * s, y - 26 * s, x + 26 * s, y + 26 * s, fill="#FFD43B", outline="")
        self.create_oval(x - 19 * s, y - 19 * s, x + 19 * s, y + 19 * s, fill="#FFE066", outline="")

    def _moon(self, x, y, s, bg):
        self.create_oval(x - 26 * s, y - 26 * s, x + 26 * s, y + 26 * s, fill="#F4F1DE", outline="")
        self.create_oval(x - 14 * s, y - 34 * s, x + 30 * s, y + 10 * s, fill=bg, outline="")
        for i, (dx, dy) in enumerate([(-50, -40), (44, -46), (50, 20), (-46, 34), (10, 52)]):
            col = mix(bg, WHITE, 0.45 + 0.4 * math.sin(self.t * 0.08 + i * 1.7))
            r = 2 * s
            self.create_oval(x + dx * s - r, y + dy * s - r, x + dx * s + r, y + dy * s + r, fill=col, outline="")

    def _drops(self, cx, cy, s, bg, n=7, color="#8EC5FF"):
        for i in range(n):
            phase = (self.t * 3 + i * 23) % 48
            x = cx - 32 * s + i * (64 * s / (n - 1)) + phase * 0.15 * s
            y = cy + 22 * s + phase * s
            col = mix(color, bg, phase / 56)
            self.create_line(x, y, x - 3 * s, y + 10 * s, fill=col, width=2.5 * s, capstyle="round")

    def _draw(self):
        self.delete("all")
        s = self.size / 150
        cx = cy = self.size / 2
        t, bg, k = self.t, self.cget("bg"), self.kind
        if k == "clear":
            self._sun(cx, cy, s) if self.is_day else self._moon(cx, cy, s, bg)
        elif k == "clouds":
            self._sun(cx + 22 * s, cy - 24 * s, s * 0.7) if self.is_day else self._moon(cx + 22 * s, cy - 24 * s, s * 0.7, bg)
            dx = math.sin(t * 0.04) * 7 * s
            self._cloud(cx + 28 * s - dx * 0.6, cy + 22 * s, s * 0.8, "#DCE6F2")
            self._cloud(cx - 10 * s + dx, cy + 12 * s, s * 1.1, WHITE)
        elif k == "rain":
            self._cloud(cx, cy - 14 * s, s, "#E6ECF3")
            self._drops(cx, cy - 10 * s, s, bg)
        elif k == "snow":
            self._cloud(cx, cy - 14 * s, s, "#EEF3F8")
            for i in range(8):
                phase = (t * 1.2 + i * 17) % 50
                x = cx - 34 * s + i * (68 * s / 7) + math.sin((t + i * 9) * 0.08) * 5 * s
                y = cy + 16 * s + phase * s
                r = 2.6 * s
                self.create_oval(x - r, y - r, x + r, y + r, fill=mix(WHITE, bg, phase / 70), outline="")
        elif k == "storm":
            self._cloud(cx, cy - 18 * s, s, "#B9C0CC")
            self._drops(cx, cy - 14 * s, s, bg, n=5)
            if (t // 8) % 7 < 2:
                pts = [(2, 8), (-12, 36), (-2, 36), (-10, 60), (14, 28), (3, 28), (12, 8)]
                self.create_polygon([(cx + x * s, cy + y * s) for x, y in pts], fill="#FFE066", outline="")
        else:  # fog
            self._cloud(cx, cy - 18 * s, s, "#DDE3EB")
            for i in range(3):
                off = math.sin(t * 0.03 + i) * 10 * s
                y = cy + 22 * s + i * 14 * s
                self.create_line(cx - 36 * s + off, y, cx + 36 * s + off, y,
                                 fill=mix("#E2E8F0", bg, 0.15 * i), width=6 * s, capstyle="round")


class Spinner(tk.Canvas):
    def __init__(self, master, size=26, bg="#000000", color=WHITE):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        self.size, self.color, self.angle, self.running = size, color, 0, False

    def start(self):
        if not self.running:
            self.running = True
            self._tick()

    def stop(self):
        self.running = False
        try:
            self.delete("all")
        except tk.TclError:
            pass

    def _tick(self):
        if not self.running:
            return
        try:
            self.delete("all")
            p = 4
            self.create_oval(p, p, self.size - p, self.size - p, outline=mix(self.cget("bg"), WHITE, 0.2), width=3)
            self.create_arc(p, p, self.size - p, self.size - p, start=self.angle, extent=110, style="arc",
                            outline=self.color, width=3)
            self.angle = (self.angle + 16) % 360
            self.after(30, self._tick)
        except tk.TclError:
            self.running = False


class PulseDot(tk.Canvas):
    def __init__(self, master, bg="#000000"):
        super().__init__(master, width=18, height=18, bg=bg, highlightthickness=0, bd=0)
        self.color, self.t = "#34D399", 0
        self._tick()

    def set_state(self, live: bool):
        self.color = "#34D399" if live else "#F87171"

    def _tick(self):
        try:
            self.delete("all")
            self.t += 1
            ph = (self.t % 40) / 40
            r = 3 + 6 * ph
            self.create_oval(9 - r, 9 - r, 9 + r, 9 + r, outline=mix(self.color, self.cget("bg"), ph), width=1.5)
            self.create_oval(5.5, 5.5, 12.5, 12.5, fill=self.color, outline="")
            self.after(50, self._tick)
        except tk.TclError:
            pass


# ----------------------------------------------------------------------------
# SETUP + PIN FIELDS
# ----------------------------------------------------------------------------
class PinFields(ctk.CTkFrame):
    def __init__(self, master, width=340, fg="transparent"):
        super().__init__(master, fg_color=fg)
        self.e1 = ctk.CTkEntry(self, placeholder_text="Create access code", show="•", width=width, height=42, corner_radius=12)
        self.e2 = ctk.CTkEntry(self, placeholder_text="Confirm access code", show="•", width=width, height=42, corner_radius=12)
        self.e1.pack(pady=(0, 10))
        self.e2.pack()
        self.show = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(self, text="Show code", variable=self.show, command=self._toggle,
                        checkbox_width=18, checkbox_height=18, font=font(12)).pack(anchor="w", pady=(10, 0))

    def _toggle(self):
        ch = "" if self.show.get() else "•"
        self.e1.configure(show=ch)
        self.e2.configure(show=ch)

    def get(self):
        return self.e1.get(), self.e2.get()


class SetupView(ctk.CTkFrame):
    """First-launch screen where the user creates their own secret access code."""

    def __init__(self, app, on_done):
        super().__init__(app, fg_color="#0E1633", corner_radius=0)
        self.app, self.on_done = app, on_done
        card = ctk.CTkFrame(self, corner_radius=28, fg_color="#1A2347")
        card.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(card, text="🔐", font=font(40)).pack(pady=(30, 0), padx=50)
        ctk.CTkLabel(card, text="Secure your private space", font=font(24, "bold")).pack(pady=(6, 4))
        ctk.CTkLabel(
            card, wraplength=380, justify="center", text_color=MUTED, font=font(13),
            text="Create a secret access code. Typing it into the search bar and pressing Enter "
                 "opens your private vault. Anything else is a normal city search.",
        ).pack(padx=30)
        self.fields = PinFields(card)
        self.fields.pack(pady=(20, 6))
        ctk.CTkLabel(
            card, justify="left", text_color=MUTED, font=font(12),
            text="•  At least 6 characters\n•  Must start with a digit or symbol, e.g. #Moon4721\n"
                 "    (keeps your code from ever reaching the autocomplete service)",
        ).pack(anchor="w", padx=60, pady=(8, 0))
        self.err = ctk.CTkLabel(card, text="", text_color="#FF8A8A", font=font(12))
        self.err.pack(pady=(8, 0))
        ctk.CTkButton(card, text="Continue", width=340, height=44, corner_radius=22, font=font(15, "bold"),
                      command=self._submit).pack(pady=(6, 10))
        ctk.CTkLabel(
            card, wraplength=380, text_color="#8EA0C8", font=font(11),
            text="There is no recovery option. If you forget the code, delete the folder "
                 f"{APP_DIR} to reset (the vault will be erased).",
        ).pack(padx=30, pady=(0, 28))
        self.fields.e2.bind("<Return>", lambda e: self._submit())
        self.after(150, self.fields.e1.focus_set)

    def _submit(self):
        pin, conf = self.fields.get()
        err = validate_pin(pin, conf)
        if err:
            self.err.configure(text=err)
            return
        if VAULT_PATH.exists():  # orphaned vault from a deleted config can't be decrypted any more
            try:
                os.replace(VAULT_PATH, VAULT_PATH.with_suffix(".orphan"))
            except OSError:
                pass
        self.app.security.set_pin(pin)
        self.on_done()


# ----------------------------------------------------------------------------
# WEATHER VIEW (the decoy, but a genuinely working weather app)
# ----------------------------------------------------------------------------
class WeatherView(ctk.CTkFrame):
    def __init__(self, app):
        self.bg = THEMES[("clear", True)]
        super().__init__(app, fg_color=self.bg, corner_radius=0)
        self.app = app
        self.units = app.security.cfg.get("units", "metric")
        self.place_cur: Place | None = None
        self.data: dict | None = None
        self._cards: list = []
        self._canv_card: list = []
        self._canv_stage: list = []
        self._q: queue.Queue = queue.Queue()
        self._load_seq = self._suggest_seq = self._theme_token = self._fade_token = 0
        self._suggest_job = self._toast_job = None
        self._build()
        self._apply_theme(self.bg)
        self.after(60, self._drain)
        self.after(REFRESH_MS, self._auto_refresh)
        self._restore_last()

    # ---- thread-safe dispatch to the UI thread ---------------------------
    def _post(self, fn, *args):
        self._q.put((fn, args))

    def _drain(self):
        try:
            while True:
                fn, args = self._q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(60, self._drain)

    # ---- layout ------------------------------------------------------------
    def _card(self, parent, radius=24):
        f = ctk.CTkFrame(parent, corner_radius=radius, fg_color=mix(self.bg, WHITE, 0.14))
        self._cards.append(f)
        return f

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # top bar
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 10))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Weather", font=font(24, "bold")).grid(row=0, column=0, padx=(0, 20))
        self.entry = ctk.CTkEntry(top, placeholder_text="🔍  Search city…", height=44, corner_radius=22, border_width=0,
                                  text_color=WHITE, placeholder_text_color=MUTED, font=font(15))
        self.entry.grid(row=0, column=1, sticky="ew")
        self.entry.bind("<Return>", self._on_submit)
        self.entry.bind("<KeyRelease>", self._on_key)
        self.search_btn = ctk.CTkButton(top, text="Search", width=96, height=44, corner_radius=22,
                                        font=font(14, "bold"), text_color=WHITE, command=self._on_submit)
        self.search_btn.grid(row=0, column=2, padx=(10, 0))
        self.spinner = Spinner(top, bg=self.bg)
        self.spinner.grid(row=0, column=3, padx=10)
        self._canv_stage.append(self.spinner)
        self.location_btn = ctk.CTkButton(top, text="◎", width=40, height=40, corner_radius=20,
                                           font=font(20), command=self._use_current_location,
                                           fg_color="transparent", hover_color=mix(self.bg, WHITE, 0.25),
                                           text_color=WHITE)
        self.location_btn.grid(row=0, column=4, padx=(8, 0))
        self.unit_btn = ctk.CTkSegmentedButton(
            top, values=["°C", "°F"], width=100, height=36, command=self._on_units, text_color=WHITE,
            selected_color="#FFFFFF", selected_hover_color="#E8EEF7")
        self.unit_btn.set("°C" if self.units == "metric" else "°F")
        self.unit_btn.grid(row=0, column=5)

        # content
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=26, pady=(4, 6))
        content.grid_columnconfigure(0, weight=3, uniform="c")
        content.grid_columnconfigure(1, weight=2, uniform="c")
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=3)
        left.grid_rowconfigure(1, weight=2)
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        self._build_hero(left)
        self._build_hourly(left)
        self._build_stats(right)
        self._build_daily(right)

        # footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 14))
        foot.grid_columnconfigure(1, weight=1)
        self.dot = PulseDot(foot, bg=self.bg)
        self.dot.grid(row=0, column=0)
        self._canv_stage.append(self.dot)
        self.status = ctk.CTkLabel(foot, text="Waiting for a location", font=font(12), text_color=MUTED)
        self.status.grid(row=0, column=1, sticky="w", padx=6)
        ctk.CTkLabel(foot, text="Weather: Open-Meteo · Approx. location: IP-based", font=font(11), text_color=MUTED).grid(row=0, column=2)

        # overlays
        self.suggest = ctk.CTkFrame(self, corner_radius=16, border_width=1)
        self.suggest.pack_propagate(False)
        self.toast = ctk.CTkFrame(self, corner_radius=18, fg_color="#B3261E")
        self.toast_lbl = ctk.CTkLabel(self.toast, text="", font=font(13, "bold"), text_color=WHITE)
        self.toast_lbl.pack(padx=20, pady=9)
        self.app.bind_all("<Button-1>", self._on_click_anywhere, add="+")  # root CTk allows bind_all; CTk widgets do not

    def _build_hero(self, parent):
        hero = self._card(parent, 28)
        hero.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        hero.grid_columnconfigure((0, 1), weight=1)
        self.city_lbl = ctk.CTkLabel(hero, text="Search for a city", font=font(30, "bold"), anchor="w")
        self.city_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=28, pady=(24, 0))
        self.time_lbl = ctk.CTkLabel(hero, text="Type a location above to begin", font=font(13), text_color=MUTED, anchor="w")
        self.time_lbl.grid(row=1, column=0, columnspan=2, sticky="w", padx=28)
        self.icon = WeatherIcon(hero, 150, bg=mix(self.bg, WHITE, 0.14))
        self.icon.grid(row=2, column=0, padx=(20, 0), pady=8)
        self._canv_card.append(self.icon)
        box = ctk.CTkFrame(hero, fg_color="transparent")
        box.grid(row=2, column=1, sticky="w", padx=(0, 20))
        self.temp_lbl = ctk.CTkLabel(box, text="--°", font=font(88, "bold"), anchor="w")
        self.temp_lbl.pack(anchor="w")
        self.cond_lbl = ctk.CTkLabel(box, text="", font=font(20), anchor="w")
        self.cond_lbl.pack(anchor="w")
        self.hilo_lbl = ctk.CTkLabel(box, text="", font=font(14), text_color=MUTED, anchor="w")
        self.hilo_lbl.pack(anchor="w")
        self.sun_lbl = ctk.CTkLabel(hero, text="", font=font(12), text_color=MUTED)
        self.sun_lbl.grid(row=3, column=0, columnspan=2, pady=(0, 18))

    def _build_hourly(self, parent):
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="nsew", pady=(7, 0))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="HOURLY FORECAST", font=font(11, "bold"), text_color=MUTED).grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 4))
        self.hourly_row = ctk.CTkFrame(card, fg_color="transparent")
        self.hourly_row.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        card.grid_rowconfigure(1, weight=1)

    def _build_stats(self, parent):
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="s")
        self.stat = {}
        for i, (key, title) in enumerate([("feels", "FEELS LIKE"), ("hum", "HUMIDITY"), ("wind", "WIND"), ("uv", "UV INDEX")]):
            tile = self._card(grid, 20)
            tile.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 6, 6 if i % 2 == 0 else 0),
                      pady=(0 if i < 2 else 12, 0))
            ctk.CTkLabel(tile, text=title, font=font(11, "bold"), text_color=MUTED).pack(anchor="w", padx=16, pady=(12, 0))
            val = ctk.CTkLabel(tile, text="—", font=font(26, "bold"))
            val.pack(anchor="w", padx=16)
            sub = ctk.CTkLabel(tile, text="", font=font(12), text_color=MUTED, wraplength=150, justify="left")
            sub.pack(anchor="w", padx=16, pady=(0, 12))
            self.stat[key] = (val, sub)

    def _build_daily(self, parent):
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="nsew", pady=(7, 0))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="7-DAY FORECAST", font=font(11, "bold"), text_color=MUTED).grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 4))
        self.daily_body = ctk.CTkFrame(card, fg_color="transparent")
        self.daily_body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self.daily_body.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

    # ---- theming / animation -------------------------------------------
    def _alive(self, lst):
        lst[:] = [w for w in lst if w.winfo_exists()]
        return lst

    def _paint(self, widget, behind: str):
        """CustomTkinter only propagates bg_color one level; push it down the whole tree."""
        for ch in widget.winfo_children():
            if not is_ctk(ch):
                continue
            try:
                ch.configure(bg_color=behind)
            except Exception:
                pass
            if isinstance(ch, ctk.CTkFrame):
                fg = ch.cget("fg_color")
                self._paint(ch, behind if fg == "transparent" else fg)

    def _apply_theme(self, color: str):
        self.bg = color
        card, inp = mix(color, WHITE, 0.14), mix(color, WHITE, 0.20)
        self.configure(fg_color=color)
        for w in self._alive(self._cards):
            w.configure(fg_color=card)
        for w in self._alive(self._canv_card):
            w.configure(bg=card)
        for w in self._alive(self._canv_stage):
            w.configure(bg=color)
        self.entry.configure(fg_color=inp)
        self.search_btn.configure(fg_color=mix(color, WHITE, 0.30), hover_color=mix(color, WHITE, 0.42))
        self.unit_btn.configure(fg_color=inp, unselected_color=inp, unselected_hover_color=mix(color, WHITE, 0.28))
        self.location_btn.configure(hover_color=mix(color, WHITE, 0.25))
        self.suggest.configure(fg_color=mix(color, "#0A0F1C", 0.55), border_color=mix(color, WHITE, 0.25))
        self._paint(self, color)

    def _animate_to(self, target: str):
        start = self.bg
        if start == target:
            return
        self._theme_token += 1
        token, steps = self._theme_token, 10

        def step(i):
            if token != self._theme_token:
                return
            self._apply_theme(mix(start, target, ease(i / steps)))
            if i < steps:
                self.after(28, lambda: step(i + 1))

        step(1)

    def _fade_in(self, items):
        self._fade_token += 1
        token, steps = self._fade_token, 9

        def step(i):
            if token != self._fade_token:
                return
            base = mix(self.bg, WHITE, 0.14)
            for lbl, end in items:
                try:
                    lbl.configure(text_color=mix(base, end, ease(i / steps)))
                except tk.TclError:
                    pass
            if i < steps:
                self.after(30, lambda: step(i + 1))

        step(0)

    # ---- toast + suggestions ----------------------------------------------
    def show_toast(self, msg: str, error: bool = True):
        self.toast.configure(fg_color="#B3261E" if error else "#1F7A4D")
        self.toast_lbl.configure(text=msg)
        self.toast.place(relx=0.5, y=90, anchor="n")
        self.toast.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(4500, self.toast.place_forget)

    def _hide_suggestions(self):
        self._suggest_seq += 1
        self.suggest.place_forget()

    def _on_click_anywhere(self, event):
        w = str(event.widget)
        if self.suggest.winfo_ismapped() and not w.startswith(str(self.suggest)) and not w.startswith(str(self.entry)):
            self._hide_suggestions()

    def _on_key(self, event):
        if event.keysym == "Escape":
            self._hide_suggestions()
            return
        if event.keysym in ("Return", "Up", "Down", "Left", "Right", "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R"):
            return
        text = self.entry.get().strip()
        if self._suggest_job:
            self.after_cancel(self._suggest_job)
        # Privacy rule: only letter-initial text is ever sent to the geocoder, so a
        # secret code (which must start with a digit/symbol) can't leak while typing.
        if len(text) < 2 or not text[0].isalpha():
            self._hide_suggestions()
            return
        self._suggest_job = self.after(320, lambda t=text: self._suggest(t))

    def _suggest(self, text):
        self._suggest_seq += 1
        seq = self._suggest_seq

        def work():
            try:
                places = search_places(text, 6)
            except WeatherError:
                places = []
            self._post(self._show_suggestions, seq, text, places)

        threading.Thread(target=work, daemon=True).start()

    def _show_suggestions(self, seq, text, places):
        if seq != self._suggest_seq or self.entry.get().strip() != text or not places:
            if seq == self._suggest_seq:
                self.suggest.place_forget()
            return
        for ch in self.suggest.winfo_children():
            ch.destroy()
        for p in places:
            feature = p.feature.replace("_", " ")
            info = f"{p.label}  ·  {feature}" if feature else p.label
            ctk.CTkButton(self.suggest, text=info, anchor="w", height=38, corner_radius=10,
                          fg_color="transparent", hover_color=mix(self.bg, WHITE, 0.25), text_color=WHITE,
                          font=font(14), command=lambda p=p: self._choose(p)).pack(fill="x", padx=6, pady=(6 if p is places[0] else 0, 0))
        self.update_idletasks()
        x = self.entry.winfo_rootx() - self.winfo_rootx()
        y = self.entry.winfo_rooty() - self.winfo_rooty() + self.entry.winfo_height() + 6
        self.suggest.configure(width=self.entry.winfo_width(), height=len(places) * 38 + 14)
        self.suggest.place(x=x, y=y)
        self.suggest.lift()

    def _choose(self, place: Place):
        self._hide_suggestions()
        self.entry.delete(0, "end")
        self.entry.insert(0, place.name)
        self.focus_set()
        self._load_place(place)

    def _use_current_location(self):
        """Use network-based approximate location; no device GPS permission is available in Tk."""
        self.spinner.start()
        self.status.configure(text="Finding your approximate location…")
        self._load_seq += 1
        seq = self._load_seq

        def work():
            try:
                info = http_get_json("https://ipapi.co/json/", {})
                lat, lon = float(info["latitude"]), float(info["longitude"])
                place = Place(info.get("city") or "Your location", info.get("region") or "", info.get("country_name") or "", lat, lon)
                data = fetch_forecast(place, self.units)
                self._post(self._on_loaded, seq, place, data)
            except (WeatherError, KeyError, TypeError, ValueError) as exc:
                self._post(self._on_error, seq, str(exc) if isinstance(exc, WeatherError) else "Could not determine your approximate location.")
        threading.Thread(target=work, daemon=True).start()

    # ---- search flow ----------------------------------------------------
    def _on_submit(self, event=None):
        text = self.entry.get().strip()
        self._hide_suggestions()
        if not text:
            self.show_toast("Please enter a city name.")
            return
        # SECRET TRIGGER: runs locally, before any network call.
        if not text[0].isalpha() and self.app.try_unlock(text):
            self.entry.delete(0, "end")
            return
        self._load_query(text)

    def _load_query(self, text):
        self._load_seq += 1
        seq = self._load_seq
        self.spinner.start()

        def work():
            try:
                places = search_places(text, 1)
                if not places:
                    raise WeatherError("Location not found. Check the spelling and try again.")
                data = fetch_forecast(places[0], self.units)
                self._post(self._on_loaded, seq, places[0], data)
            except WeatherError as e:
                self._post(self._on_error, seq, str(e))
            except Exception:
                traceback.print_exc()
                self._post(self._on_error, seq, "Something went wrong. Please try again.")

        threading.Thread(target=work, daemon=True).start()

    def _load_place(self, place: Place, silent: bool = False):
        self._load_seq += 1
        seq = self._load_seq
        if not silent:
            self.spinner.start()

        def work():
            try:
                self._post(self._on_loaded, seq, place, fetch_forecast(place, self.units))
            except WeatherError as e:
                self._post(self._on_error, seq, str(e), silent)
            except Exception:
                traceback.print_exc()
                self._post(self._on_error, seq, "Something went wrong. Please try again.", silent)

        threading.Thread(target=work, daemon=True).start()

    def _on_error(self, seq, msg, silent=False):
        if seq != self._load_seq:
            return
        self.spinner.stop()
        self.dot.set_state(False)
        self.status.configure(text="Offline · showing last update" if self.data else msg)
        if not silent:
            self.show_toast(msg)

    def _on_loaded(self, seq, place, data):
        if seq != self._load_seq:
            return
        self.spinner.stop()
        self.place_cur, self.data = place, data
        self.app.security.update_prefs(
            last_place={"name": place.name, "admin": place.admin, "country": place.country,
                        "lat": place.lat, "lon": place.lon})
        self._render(place, data)

    def _on_units(self, value):
        self.units = "metric" if value == "°C" else "imperial"
        self.app.security.update_prefs(units=self.units)
        if self.place_cur:
            self._load_place(self.place_cur)

    def _restore_last(self):
        lp = self.app.security.cfg.get("last_place")
        if lp:
            try:
                self._load_place(Place(lp["name"], lp.get("admin", ""), lp.get("country", ""), lp["lat"], lp["lon"]))
            except KeyError:
                pass

    def _auto_refresh(self):
        if self.place_cur and self.app.vault_view is None:
            self._load_place(self.place_cur, silent=True)
        self.after(REFRESH_MS, self._auto_refresh)

    # ---- rendering ---------------------------------------------------------
    def _render(self, place: Place, d: dict):
        label, kind, _ = describe(d["code"], d["is_day"])
        wind_unit = "km/h" if self.units == "metric" else "mph"
        region = ", ".join(x for x in (place.admin, place.country) if x)
        self.city_lbl.configure(text=place.name)
        self.time_lbl.configure(text=f"{region}  ·  {fmt_local(d['time'])}" if region else fmt_local(d["time"]))
        self.temp_lbl.configure(text=f"{round(d['temp'])}°")
        self.cond_lbl.configure(text=label)
        self.hilo_lbl.configure(text=f"H: {round(d['hi'])}°    L: {round(d['lo'])}°")
        self.sun_lbl.configure(text=f"🌅 {fmt_clock(d['sunrise'])}      🌇 {fmt_clock(d['sunset'])}")
        self.icon.set_state(kind, d["is_day"])

        diff = d["feels"] - d["temp"]
        feels_sub = "Similar to the actual temperature" if abs(diff) <= 1 else (
            "Feels colder than the actual temperature" if diff < 0 else "Feels warmer than the actual temperature")
        hum = d["humidity"]
        hum_sub = "Dry air" if hum < 30 else "Comfortable" if hum <= 60 else "Humid"
        uv = d["uv"]
        vals = {
            "feels": (f"{round(d['feels'])}°", feels_sub),
            "hum": (f"{hum}%", hum_sub),
            "wind": (f"{round(d['wind'])} {wind_unit}", f"From the {compass(d['wind_dir'])}"),
            "uv": ("—" if uv is None else f"{uv:.0f}", uv_label(uv) + " today"),
        }
        for k, (v, s) in vals.items():
            self.stat[k][0].configure(text=v)
            self.stat[k][1].configure(text=s)

        self._build_hourly_rows(d)
        self._build_daily_rows(d)
        target = THEMES[(kind, d["is_day"])]
        self._animate_to(target)
        self._fade_in([(self.city_lbl, WHITE), (self.temp_lbl, WHITE), (self.cond_lbl, WHITE),
                       (self.time_lbl, MUTED), (self.hilo_lbl, MUTED)])
        self.dot.set_state(True)
        self.status.configure(text=f"Live · updated {datetime.now().strftime('%I:%M %p').lstrip('0')}")

    def _build_hourly_rows(self, d):
        for ch in self.hourly_row.winfo_children():
            ch.destroy()
        values = [h["temp"] for h in d["hourly"]]
        chart = tk.Canvas(self.hourly_row, height=52, bg=mix(self.bg, WHITE, 0.14), highlightthickness=0, bd=0)
        chart.grid(row=0, column=0, columnspan=max(len(values), 1), sticky="ew", padx=12, pady=(2, 0))
        self._canv_card.append(chart)
        chart.update_idletasks()
        width = max(chart.winfo_width(), 420)
        low, high = min(values), max(values)
        span = max(high - low, 1)
        pts = [(16 + i * (width - 32) / max(len(values) - 1, 1), 40 - (v - low) / span * 26) for i, v in enumerate(values)]
        for a, b in zip(pts, pts[1:]):
            chart.create_line(*a, *b, fill="#A9D5FF", width=3, smooth=True)
        for x, y in pts:
            chart.create_oval(x-3, y-3, x+3, y+3, fill=WHITE, outline="#A9D5FF", width=1)
        for i, h in enumerate(d["hourly"]):
            self.hourly_row.grid_columnconfigure(i, weight=1, uniform="h")
            col = ctk.CTkFrame(self.hourly_row, fg_color="transparent")
            col.grid(row=1, column=i, sticky="nsew")
            when = "Now" if i == 0 else datetime.fromisoformat(h["time"]).strftime("%I %p").lstrip("0")
            ctk.CTkLabel(col, text=when, font=font(12), text_color=MUTED).pack(pady=(2, 4))
            ctk.CTkLabel(col, text=describe(h["code"], h["is_day"])[2], font=font(24)).pack()
            ctk.CTkLabel(col, text=f"{round(h['temp'])}°", font=font(16, "bold")).pack(pady=(4, 0))

    def _build_daily_rows(self, d):
        for ch in self.daily_body.winfo_children():
            ch.destroy()
        week_lo = min(x["lo"] for x in d["daily"])
        week_hi = max(x["hi"] for x in d["daily"])
        span = max(week_hi - week_lo, 1)
        W_BAR = 90
        track = mix(self.bg, WHITE, 0.30)
        for i, day in enumerate(d["daily"]):
            self.daily_body.grid_rowconfigure(i, weight=1)
            row = ctk.CTkFrame(self.daily_body, fg_color="transparent")
            row.grid(row=i, column=0, sticky="nsew")
            row.grid_columnconfigure(0, minsize=64)
            row.grid_columnconfigure(3, weight=1)
            name = "Today" if i == 0 else datetime.strptime(day["date"], "%Y-%m-%d").strftime("%a")
            ctk.CTkLabel(row, text=name, font=font(14, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=(6, 0))
            ctk.CTkLabel(row, text=describe(day["code"])[2], font=font(18)).grid(row=0, column=1, padx=6)
            ctk.CTkLabel(row, text=f"{round(day['lo'])}°", font=font(14), text_color=MUTED, width=36, anchor="e").grid(row=0, column=2)
            bar = tk.Canvas(row, width=W_BAR, height=10, bg=mix(self.bg, WHITE, 0.14), highlightthickness=0, bd=0)
            bar.grid(row=0, column=3, padx=8)
            self._canv_card.append(bar)
            x1 = 5 + (day["lo"] - week_lo) / span * (W_BAR - 10)
            x2 = max(x1 + 1, 5 + (day["hi"] - week_lo) / span * (W_BAR - 10))
            bar.create_line(5, 5, W_BAR - 5, 5, fill=track, width=6, capstyle="round")
            bar.create_line(x1, 5, x2, 5, fill="#FBBF24", width=6, capstyle="round")
            ctk.CTkLabel(row, text=f"{round(day['hi'])}°", font=font(14, "bold"), width=36, anchor="w").grid(row=0, column=4, padx=(0, 6))


# ----------------------------------------------------------------------------
# VAULT VIEW (file-location bookmarks)
# ----------------------------------------------------------------------------
class ModalDialog(ctk.CTkToplevel):
    def __init__(self, master, title, size):
        super().__init__(master, fg_color=V_BG)
        self.title(title)
        self.geometry(size)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.after(100, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass


class EditDialog(ModalDialog):
    def __init__(self, master, entry, on_save):
        super().__init__(master, "Edit item", "480x470")
        self.on_save = on_save
        ctk.CTkLabel(self, text="Edit saved location", font=font(20, "bold"), text_color=V_TEXT).pack(anchor="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(self, text=entry["path"], font=font(11), text_color=V_MUTED, wraplength=430, justify="left").pack(anchor="w", padx=24)
        self.name = self._field("Display name", entry["name"])
        self.tags = self._field("Tags (comma-separated)", entry.get("tags", ""))
        ctk.CTkLabel(self, text="Notes", font=font(12), text_color=V_MUTED).pack(anchor="w", padx=24, pady=(12, 2))
        self.note = ctk.CTkTextbox(self, height=100, fg_color=V_CARD, border_width=1, border_color=V_LINE)
        self.note.pack(fill="x", padx=24)
        self.note.insert("1.0", entry.get("note", ""))
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=18)
        ctk.CTkButton(btns, text="Save", width=110, command=self._save).pack(side="right")
        ctk.CTkButton(btns, text="Cancel", width=90, fg_color="transparent", border_width=1, border_color=V_LINE,
                      command=self.destroy).pack(side="right", padx=8)

    def _field(self, label, value):
        ctk.CTkLabel(self, text=label, font=font(12), text_color=V_MUTED).pack(anchor="w", padx=24, pady=(12, 2))
        e = ctk.CTkEntry(self, height=36, fg_color=V_CARD, border_color=V_LINE)
        e.pack(fill="x", padx=24)
        e.insert(0, value)
        return e

    def _save(self):
        name = self.name.get().strip()
        if not name:
            return
        self.on_save(name=name, tags=self.tags.get().strip(), note=self.note.get("1.0", "end").strip())
        self.destroy()


class ChangePinDialog(ModalDialog):
    def __init__(self, master, on_save):
        super().__init__(master, "Change access code", "420x400")
        self.on_save = on_save
        ctk.CTkLabel(self, text="Change access code", font=font(20, "bold"), text_color=V_TEXT).pack(pady=(24, 4))
        ctk.CTkLabel(self, text="The vault is re-encrypted with your new code.", font=font(12), text_color=V_MUTED).pack()
        self.fields = PinFields(self, width=320)
        self.fields.pack(pady=18)
        self.err = ctk.CTkLabel(self, text="", text_color="#FF8A8A", font=font(12))
        self.err.pack()
        ctk.CTkButton(self, text="Update code", width=320, height=40, command=self._apply).pack(pady=10)

    def _apply(self):
        pin, conf = self.fields.get()
        err = validate_pin(pin, conf)
        if err:
            self.err.configure(text=err)
            return
        self.on_save(pin)
        self.destroy()


class VaultView(ctk.CTkFrame):
    COLS = ("name", "path", "added", "notes", "status")
    HEAD = {"name": "Name", "path": "Location", "added": "Date added", "notes": "Tags / Notes", "status": "Status"}

    def __init__(self, app, vault: Vault):
        super().__init__(app, fg_color=V_BG, corner_radius=0)
        self.app, self.vault = app, vault
        self.sort_col, self.sort_rev = "added", True

        # header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(24, 6))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="🔐  Private Vault", font=font(26, "bold"), text_color=V_TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text="Saves file and folder locations only. File contents are never copied.",
                     font=font(12), text_color=V_MUTED).pack(anchor="w")
        ctk.CTkButton(head, text="🔒  Lock & Return to Weather", height=44, width=230, corner_radius=22,
                      fg_color=V_DANGER, hover_color="#C93A3F", font=font(14, "bold"), command=app.lock_vault).pack(side="right")
        ctk.CTkButton(head, text="Erase all", width=84, height=34, fg_color="transparent", border_width=1,
                      border_color=V_LINE, text_color=V_MUTED, hover_color=V_CARD, command=self._erase_all).pack(side="right", padx=(0, 12))
        ctk.CTkButton(head, text="Change code", width=104, height=34, fg_color="transparent", border_width=1,
                      border_color=V_LINE, text_color=V_MUTED, hover_color=V_CARD, command=self._change_pin).pack(side="right", padx=(0, 8))

        # toolbar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=28, pady=(14, 10))
        for text, cmd, accent in [("＋ Add files", self._add_files, True), ("＋ Add folder", self._add_folder, True),
                                  ("Create file", self._create_file, True), ("Create folder", self._create_folder, True),
                                  ("Open", self._open_location, False), ("Edit", self._edit, False),
                                  ("Remove", self._remove, False)]:
            ctk.CTkButton(bar, text=text, height=36, width=102 if accent else 82, corner_radius=10, command=cmd,
                          fg_color=V_ACCENT if accent else V_CARD, hover_color="#3A77E0" if accent else V_LINE,
                          text_color=WHITE if accent else V_TEXT).pack(side="left", padx=(0, 8))
        self.filter = ctk.CTkEntry(bar, placeholder_text="Filter…", width=160, height=36, fg_color=V_CARD, border_color=V_LINE)
        self.filter.pack(side="right")
        self.filter.bind("<KeyRelease>", lambda e: self.refresh())

        # table
        wrap = ctk.CTkFrame(self, fg_color=V_CARD, corner_radius=16, border_width=1, border_color=V_LINE)
        wrap.pack(fill="both", expand=True, padx=28)
        self._style_tree()
        self.tree = ttk.Treeview(wrap, columns=self.COLS, show="headings", style="Vault.Treeview", selectmode="extended")
        widths = {"name": 180, "path": 380, "added": 130, "notes": 200, "status": 90}
        for c in self.COLS:
            self.tree.heading(c, text=self.HEAD[c], anchor="w", command=lambda c=c: self._set_sort(c))
            self.tree.column(c, width=widths[c], anchor="w", stretch=(c in ("path", "notes")), minwidth=70)
        sb = ctk.CTkScrollbar(wrap, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=8)
        self.tree.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        self.tree.bind("<Double-1>", self._on_double)
        self.empty = ctk.CTkLabel(wrap, text="No saved locations yet.\nUse “Add files” or “Add folder” to bookmark them.",
                                  text_color=V_MUTED, font=font(14), justify="center")

        self.msg = ctk.CTkLabel(self, text="", font=font(12), text_color=V_MUTED, anchor="w")
        self.msg.pack(fill="x", padx=30, pady=(8, 14))
        self.refresh()

    def _style_tree(self):
        fam = tkfont.nametofont("TkDefaultFont").actual()["family"]
        st = ttk.Style()
        st.theme_use("clam")
        st.layout("Vault.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Vault.Treeview", background=V_CARD, fieldbackground=V_CARD, foreground=V_TEXT,
                     rowheight=36, borderwidth=0, font=(fam, 11))
        st.configure("Vault.Treeview.Heading", background="#202636", foreground=V_MUTED, relief="flat",
                     font=(fam, 10, "bold"), padding=(8, 8))
        st.map("Vault.Treeview", background=[("selected", "#2B4A86")], foreground=[("selected", WHITE)])
        st.map("Vault.Treeview.Heading", background=[("active", "#262C3E")])

    # ---- data -> table ----------------------------------------------------
    def say(self, text):
        self.msg.configure(text=text)

    def _set_sort(self, col):
        self.sort_rev = not self.sort_rev if col == self.sort_col else False
        self.sort_col = col
        self.refresh()

    def refresh(self):
        q = self.filter.get().strip().lower()
        rows = []
        for e in self.vault.entries:
            notes = "  ".join(x for x in (e.get("tags", ""), e.get("note", "").replace("\n", " ")) if x)
            if q and q not in f"{e['name']} {e['path']} {notes}".lower():
                continue
            status = "✓ Found" if os.path.exists(e["path"]) else "⚠ Missing"
            rows.append((e["id"], (e["name"], e["path"], e["added"], notes, status)))
        idx = self.COLS.index(self.sort_col)
        rows.sort(key=lambda r: r[1][idx].lower(), reverse=self.sort_rev)
        self.tree.delete(*self.tree.get_children())
        for iid, vals in rows:
            self.tree.insert("", "end", iid=iid, values=vals)
        if self.vault.entries:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.say(f"{len(rows)} of {len(self.vault.entries)} item(s)  ·  paths and notes only, encrypted on disk")

    def _selected(self) -> list[dict]:
        return [e for e in (self.vault.get(i) for i in self.tree.selection()) if e]

    # ---- actions ------------------------------------------------------------
    def _add_files(self):
        paths = filedialog.askopenfilenames(title="Choose files to link")
        added = sum(self.vault.add_path(p) for p in paths)
        self.refresh()
        if paths:
            self.say(f"Linked {added} new file(s)." if added else "Those files are already saved.")

    def _add_folder(self):
        p = filedialog.askdirectory(title="Choose a folder to link")
        if p:
            ok = self.vault.add_path(p)
            self.refresh()
            self.say("Folder linked." if ok else "That folder is already saved.")

    def _create_item(self, kind):
        parent = filedialog.askdirectory(title=f"Choose where to create the {kind}")
        if not parent:
            return
        name = simpledialog.askstring(f"Create {kind}", f"Name for the new {kind}:", parent=self)
        if not name:
            return
        name = name.strip()
        if name in (".", "..") or Path(name).name != name or any(ch in name for ch in "\\/"):
            messagebox.showerror("Invalid name", "Enter a simple name without folder separators.", parent=self)
            return
        target = Path(parent) / name
        try:
            if kind == "folder":
                target.mkdir()
            else:
                if not target.suffix:
                    target = target.with_suffix(".txt")
                with target.open("x", encoding="utf-8") as f:
                    f.write("")
            self.vault.add_path(str(target))
            self.refresh()
            self.say(f"Created and saved {target.name}.")
        except FileExistsError:
            messagebox.showerror("Already exists", f"{target.name} already exists in that folder.", parent=self)
        except OSError as exc:
            messagebox.showerror("Could not create item", str(exc), parent=self)

    def _create_file(self):
        self._create_item("file")

    def _create_folder(self):
        self._create_item("folder")

    def _open_location(self):
        sel = self._selected()
        if not sel:
            self.say("Select an item first.")
            return
        try:
            note = reveal_in_file_manager(sel[0]["path"])
            self.say(note or "Opened in your file manager.")
        except Exception as exc:
            self.say(f"Couldn't open that location: {exc}")

    def _on_double(self, event):
        if self.tree.identify_row(event.y):
            self._open_location()

    def _edit(self):
        sel = self._selected()
        if not sel:
            self.say("Select an item first.")
            return
        e = sel[0]
        EditDialog(self, e, lambda **kw: (self.vault.update(e["id"], **kw), self.refresh(), self.say("Saved.")))

    def _remove(self):
        sel = self._selected()
        if not sel:
            self.say("Select one or more items first.")
            return
        if messagebox.askyesno("Remove", f"Remove {len(sel)} saved location(s)?\nThe files themselves are not touched.", parent=self):
            self.vault.remove([e["id"] for e in sel])
            self.refresh()

    def _change_pin(self):
        def apply(pin):
            self.vault.rekey(self.app.security.set_pin(pin))
            self.say("Access code updated and vault re-encrypted.")
        ChangePinDialog(self, apply)

    def _erase_all(self):
        if messagebox.askyesno("Erase everything",
                               "This permanently deletes the vault AND your access code, then restarts setup.\nContinue?",
                               icon="warning", parent=self):
            self.app.factory_reset()


# ----------------------------------------------------------------------------
# APP SHELL
# ----------------------------------------------------------------------------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Weather")
        self.geometry("1060x740")
        self.minsize(920, 680)
        self.configure(fg_color="#0E1633")
        self.security = Security()
        self.security.load()
        self.weather: WeatherView | None = None
        self.setup_view: SetupView | None = None
        self.vault_view: VaultView | None = None
        self.vault: Vault | None = None
        self._idle_job = None
        self._wiping = False
        self._icon_ref = None
        self.after(300, self._set_icon)
        for ev in ("<Key>", "<Button>", "<Motion>"):
            self.bind_all(ev, self._bump_idle, add="+")
        self._show_weather() if self.security.configured else self._show_setup()

    def _set_icon(self):
        if Image is None:
            return
        try:
            img = Image.new("RGBA", (64, 64), (46, 123, 224, 255))
            d = ImageDraw.Draw(img)
            d.ellipse((30, 8, 56, 34), fill=(255, 212, 59, 255))
            d.ellipse((6, 28, 30, 52), fill="white")
            d.ellipse((18, 22, 44, 50), fill="white")
            d.ellipse((34, 30, 58, 52), fill="white")
            d.rectangle((16, 40, 48, 52), fill="white")
            self._icon_ref = ImageTk.PhotoImage(img)
            self.iconphoto(True, self._icon_ref)
        except Exception:
            pass

    # ---- screens ------------------------------------------------------------
    def _show_setup(self):
        self.setup_view = SetupView(self, self._after_setup)
        self.setup_view.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _after_setup(self):
        if self.setup_view:
            self.setup_view.destroy()
            self.setup_view = None
        self._show_weather()

    def _show_weather(self):
        self.weather = WeatherView(self)
        self.weather.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.weather.entry.focus_set()

    # ---- vault lifecycle ------------------------------------------------------
    def try_unlock(self, text: str) -> bool:
        key = self.security.check_pin(text)
        if key is None:
            return False
        try:
            vault = Vault(key)
        except (InvalidToken, OSError, ValueError):
            if self.weather:
                self.weather.show_toast("Couldn't load data. Please try again.")
            return True  # it was the right code; just don't expose vault internals

        def swap():
            self.vault = vault
            self.vault_view = VaultView(self, vault)
            self.vault_view.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._bump_idle()

        self._wipe(swap, V_BG, "🔓")
        return True

    def lock_vault(self):
        def swap():
            self._teardown_vault()
            if self.weather:
                self.weather.entry.delete(0, "end")
                self.weather.focus_set()

        self._wipe(swap, self.weather.bg if self.weather else "#0E1633", "🔒")

    def _teardown_vault(self):
        if self._idle_job:
            self.after_cancel(self._idle_job)
            self._idle_job = None
        if self.vault_view:
            self.vault_view.destroy()
        self.vault_view, self.vault = None, None  # drop decrypted data + key from memory

    def factory_reset(self):
        for p in (CONFIG_PATH, VAULT_PATH):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        self.security = Security()

        def swap():
            self._teardown_vault()
            if self.weather:
                self.weather.destroy()
                self.weather = None
            self._show_setup()

        self._wipe(swap, "#0E1633", "♻️")

    def _bump_idle(self, event=None):
        if self.vault_view is None:
            return
        if self._idle_job:
            self.after_cancel(self._idle_job)
        self._idle_job = self.after(IDLE_LOCK_MS, self.lock_vault)

    # ---- transition: smooth screen wipe -------------------------------------
    def _wipe(self, middle, color: str, glyph: str = ""):
        if self._wiping:
            return
        self._wiping = True
        ov = ctk.CTkFrame(self, fg_color=color, corner_radius=0)
        ctk.CTkLabel(ov, text=glyph, font=font(54)).place(relx=0.5, rely=0.5, anchor="center")
        steps = 12

        def slide_in(i):
            ov.place(relx=1 - ease(i / steps), rely=0, relwidth=1, relheight=1)
            ov.lift()
            if i < steps:
                self.after(16, lambda: slide_in(i + 1))
            else:
                middle()
                ov.lift()
                self.after(120, lambda: slide_out(1))

        def slide_out(i):
            ov.place(relx=-ease(i / steps), rely=0, relwidth=1, relheight=1)
            ov.lift()
            if i < steps:
                self.after(16, lambda: slide_out(i + 1))
            else:
                ov.destroy()
                self._wiping = False

        slide_in(1)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()