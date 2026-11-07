"""BTD Control Center 1.0.1 - standalone one-tab Roblox companion GUI.

This application is independent of BTD_Macro_1.0.48.py. It recreates the
window layout, Roblox docking, Global Status, and Activity Log as a separate
program. Recording, Playback, and High Angle are implemented here; Auto
Position is intentionally left for a later BTD-specific image matcher.
"""
from __future__ import annotations

import csv
import ctypes
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import textwrap
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


APP_NAME = "BTD Control Center"
APP_VERSION = "1.0.2"
APP_RELEASE_NOTES = "Moved the High Angle camera anchor to 15 cm right of the Roblox client center; kept the vertical center, DPI-aware physical offset, and existing drag/zoom sequence."
APP_TITLE = f"{APP_NAME} {APP_VERSION}"

# Familiar dark BTD Macro palette, independently defined for this application.
BG = "#11141B"
PANEL = "#191E28"
PANEL_ALT = "#202735"
BORDER = "#30394A"
TEXT = "#E8ECF4"
MUTED = "#9AA5B5"
ACCENT = "#8297FF"
ACCENT_HOVER = "#7086F4"
DANGER = "#D96C78"
SUCCESS = "#67C89B"
STATUS_PROGRESS = "#AFC0FF"
STATUS_SUCCESS = "#8DE0B4"
STATUS_WARNING = "#F2D58D"
STATUS_ERROR = "#F08E9A"
STATUS_IDLE = "#B7C0D0"

try:
    import win32con
    import win32gui
    PYWIN32_AVAILABLE = True
except ImportError:
    win32con = None
    win32gui = None
    PYWIN32_AVAILABLE = False


# Control Center releases use their own tag prefix so this app never mistakes
# an older BTD Macro release for a Control Center update.
UPDATE_REPOSITORY = "no559213-gif/BTD_Macro"
UPDATE_TAG_PREFIX = "control-center-v"
UPDATE_ASSET_NAME = "BTD_Control_Center.py"
UPDATE_CHECKSUM_ASSET_NAME = "BTD_Control_Center.py.sha256"
UPDATE_MAX_BYTES = 5 * 1024 * 1024


def _version_tuple(value: str) -> tuple[int, ...] | None:
    """Parse a simple numeric version such as 1.2.3 for safe comparisons."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _read_github_url(url: str, max_bytes: int, *, accept: str = "application/octet-stream") -> bytes:
    """Read a bounded HTTPS response from GitHub, including its asset CDN."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "api.github.com", "github.com", "release-assets.githubusercontent.com"
    }:
        raise ValueError("The update server returned an untrusted download URL.")
    request = Request(
        url,
        headers={
            "User-Agent": "BTD-Control-Center-Updater",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=20) as response:
        final_host = urlparse(response.geturl()).hostname
        if final_host not in {"api.github.com", "github.com", "release-assets.githubusercontent.com"}:
            raise ValueError("GitHub redirected the update request to an untrusted host.")
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("The update download is larger than the allowed size.")
    return payload


def _find_latest_control_center_release() -> dict | None:
    """Find the latest stable Control Center release using its dedicated tag prefix."""
    tags_url = (
        f"https://api.github.com/repos/{UPDATE_REPOSITORY}"
        f"/git/matching-refs/tags/{UPDATE_TAG_PREFIX}"
    )
    payload = _read_github_url(tags_url, 2 * 1024 * 1024, accept="application/vnd.github+json")
    refs = json.loads(payload.decode("utf-8"))
    versions = []
    for item in refs if isinstance(refs, list) else []:
        ref = str(item.get("ref", ""))
        tag = ref.rsplit("/", 1)[-1]
        if not tag.startswith(UPDATE_TAG_PREFIX):
            continue
        version = tag[len(UPDATE_TAG_PREFIX):]
        parsed_version = _version_tuple(version)
        if parsed_version is not None:
            versions.append((parsed_version, tag, version))

    # Fetch only matching Control Center releases; unrelated BTD Macro release
    # tags do not affect discovery, even after the repository has hundreds of them.
    for _parsed, tag, version in sorted(versions, reverse=True):
        release_url = (
            f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/tags/"
            f"{quote(tag, safe='')}"
        )
        try:
            raw = _read_github_url(release_url, 512 * 1024, accept="application/vnd.github+json")
        except HTTPError as exc:
            if exc.code == 404:
                continue  # Tag exists but its release may still be publishing.
            raise
        release = json.loads(raw.decode("utf-8"))
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = {str(item.get("name", "")): item for item in release.get("assets", [])}
        script_asset = assets.get(UPDATE_ASSET_NAME)
        checksum_asset = assets.get(UPDATE_CHECKSUM_ASSET_NAME)
        if not script_asset or not checksum_asset:
            continue
        return {
            "version": version,
            "tag": tag,
            "notes": str(release.get("body", "")).strip(),
            "script_url": script_asset.get("browser_download_url"),
            "checksum_url": checksum_asset.get("browser_download_url"),
            "digest": str(script_asset.get("digest", "") or ""),
        }
    return None


def _download_and_validate_update(release: dict, current_version: str, app_path: Path) -> Path:
    """Download, checksum-verify, syntax-check, and stage an update beside the app."""
    source_url = str(release.get("script_url", ""))
    checksum_url = str(release.get("checksum_url", ""))
    for asset_url in (source_url, checksum_url):
        parsed_url = urlparse(asset_url)
        if (parsed_url.scheme != "https" or parsed_url.hostname != "github.com"
                or not parsed_url.path.startswith(f"/{UPDATE_REPOSITORY}/releases/download/")):
            raise ValueError("The release contains an unexpected asset download URL.")
    source = _read_github_url(source_url, UPDATE_MAX_BYTES)
    checksum_text = _read_github_url(checksum_url, 4096).decode("utf-8", errors="replace").strip()
    expected = checksum_text.split()[0].lower() if checksum_text else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("The release did not provide a valid SHA-256 checksum.")
    actual = hashlib.sha256(source).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("The downloaded update failed its SHA-256 integrity check.")
    asset_digest = str(release.get("digest", "") or "")
    if asset_digest.startswith("sha256:") and not hmac.compare_digest(actual, asset_digest.split(":", 1)[1].lower()):
        raise ValueError("The update checksum did not match GitHub's release asset digest.")

    text = source.decode("utf-8")
    version_match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not version_match or version_match.group(1) != str(release.get("version", "")):
        raise ValueError("The downloaded file version does not match the GitHub release.")
    new_version = _version_tuple(version_match.group(1))
    old_version = _version_tuple(current_version)
    if new_version is None or old_version is None or new_version <= old_version:
        raise ValueError("The downloaded version is not newer than the installed version.")
    compile(text, str(app_path), "exec")

    staged = app_path.with_name(app_path.name + ".download")
    staged.write_bytes(source)
    return staged


def relaunch_without_console() -> None:
    """Relaunch with pythonw.exe when started from a console on Windows."""
    if os.name != "nt" or os.environ.get("BTD_CC_BOOTSTRAPPED") == "1":
        return
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        return
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        return
    try:
        env = os.environ.copy()
        env["BTD_CC_BOOTSTRAPPED"] = "1"
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        subprocess.Popen(
            [str(pythonw), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=str(Path(__file__).resolve().parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            startupinfo=startup,
        )
        os._exit(0)
    except Exception:
        # Keep running in the current interpreter if the optional relaunch fails.
        return


def configure_dpi_awareness() -> None:
    """Ask Windows for per-monitor DPI awareness before creating Tk windows."""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        set_context(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except Exception:
        pass


class WindowsInputCapture:
    """Capture physical global keyboard/mouse events while Roblox is foreground.

    Low-level hooks capture key/button/wheel/absolute pointer events. Raw Input
    relative mouse deltas are supplied by the Tk window subclass for right-drag
    camera movement when Roblox locks the cursor.
    """

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    HC_ACTION = 0
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    WM_MBUTTONDOWN = 0x0207
    WM_MBUTTONUP = 0x0208
    WM_MOUSEWHEEL = 0x020A
    WM_XBUTTONDOWN = 0x020B
    WM_XBUTTONUP = 0x020C
    WM_MOUSEHWHEEL = 0x020E
    WM_QUIT = 0x0012
    LLKHF_EXTENDED = 0x01
    LLKHF_INJECTED = 0x10
    LLKHF_UP = 0x80
    LLMHF_INJECTED = 0x01
    MOUSE_MOVE_ABSOLUTE = 0x01

    def __init__(self, hwnd_provider, event_sink) -> None:
        self._hwnd_provider = hwnd_provider
        self._event_sink = event_sink
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._recording_enabled = False
        self._high_angle_active = False
        self._right_button_held = False
        self._raw_input_available = False
        self._last_screen_point: tuple[int, int] | None = None
        self._last_normal_move_ns = 0
        self._captured_keys: dict[tuple[int, int], str] = {}
        self._captured_buttons: dict[str, tuple[int, int]] = {}
        self._state_lock = threading.RLock()
        self._keyboard_hook = None
        self._mouse_hook = None
        self._keyboard_proc = None
        self._mouse_proc = None
        self._hook_error = ""
        self._f8_down = False

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def right_button_held(self) -> bool:
        with self._state_lock:
            return self._right_button_held

    @property
    def raw_input_available(self) -> bool:
        return self._raw_input_available

    @property
    def hook_error(self) -> str:
        return self._hook_error

    def set_recording(self, enabled: bool) -> None:
        with self._state_lock:
            self._recording_enabled = bool(enabled)
            if not enabled:
                self._last_screen_point = None
                self._last_normal_move_ns = 0

    def set_high_angle(self, enabled: bool) -> None:
        with self._state_lock:
            self._high_angle_active = bool(enabled)

    def _is_target_foreground(self) -> bool:
        hwnd = self._hwnd_provider()
        if not hwnd or os.name != "nt":
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            return int(user32.GetForegroundWindow() or 0) == int(hwnd)
        except Exception:
            return False

    def _enqueue(self, kind: str, value: str = "", x: int = 0, y: int = 0) -> None:
        self._event_sink({
            "timestamp_ns": time.perf_counter_ns(),
            "type": kind,
            "value": value,
            "x": int(x),
            "y": int(y),
        })

    @staticmethod
    def _client_point(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int] | None:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
            user32.ScreenToClient.restype = wintypes.BOOL
            user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.GetClientRect.restype = wintypes.BOOL
            point = wintypes.POINT(int(screen_x), int(screen_y))
            if not user32.ScreenToClient(wintypes.HWND(hwnd), ctypes.byref(point)):
                return None
            rect = wintypes.RECT()
            if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return None
            if not (rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom):
                return None
            return int(point.x), int(point.y)
        except Exception:
            return None

    @staticmethod
    def _key_name(vk: int, scan: int, flags: int) -> str:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_name = user32.GetKeyNameTextW
            get_name.argtypes = [ctypes.c_long, wintypes.LPWSTR, ctypes.c_int]
            get_name.restype = ctypes.c_int
            lparam = (int(scan) & 0xFF) << 16
            if flags & WindowsInputCapture.LLKHF_EXTENDED:
                lparam |= 1 << 24
            buffer = ctypes.create_unicode_buffer(128)
            if get_name(lparam, buffer, len(buffer)) > 0 and buffer.value:
                return buffer.value
        except Exception:
            pass
        return f"VK_{int(vk):02X}"

    def start(self, *, recording: bool = False) -> bool:
        if os.name != "nt":
            self._hook_error = "Global input capture is supported on Windows only."
            return False
        if self.active:
            self.set_recording(recording)
            return True
        self._stop.clear()
        self._ready.clear()
        self._hook_error = ""
        self.set_recording(recording)
        self._thread = threading.Thread(
            target=self._hook_thread_main,
            name="BTDInputCapture",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(3.0):
            self._hook_error = "Windows input hooks did not initialize in time."
            self.stop()
            return False
        if self._hook_error:
            self.stop()
            return False
        return True

    def stop(self) -> None:
        self.set_recording(False)
        self._stop.set()
        thread_id = self._thread_id
        if thread_id and os.name == "nt":
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW(thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0
        with self._state_lock:
            self._right_button_held = False
            self._captured_keys.clear()
            self._captured_buttons.clear()

    def _hook_thread_main(self) -> None:
        try:
            from ctypes import wintypes

            LRESULT = ctypes.c_ssize_t
            HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            class MSLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("pt", wintypes.POINT),
                    ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
            user32.CallNextHookEx.restype = LRESULT
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = ctypes.c_int
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            user32.PostThreadMessageW.restype = wintypes.BOOL
            self._thread_id = int(kernel32.GetCurrentThreadId())
            # Create the message queue before the main thread attempts WM_QUIT.
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

            def keyboard_callback(code, wparam, lparam):
                try:
                    if code == self.HC_ACTION and not self._stop.is_set():
                        info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        vk = int(info.vkCode)
                        scan = int(info.scanCode)
                        flags = int(info.flags)
                        is_up = bool(flags & self.LLKHF_UP) or int(wparam) in (self.WM_KEYUP, self.WM_SYSKEYUP)
                        is_down = int(wparam) in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
                        injected = bool(flags & self.LLKHF_INJECTED)
                        if vk == 0x77 and not injected:
                            # F8 remains the global Emergency Stop key while an action runs.
                            if is_down and not is_up and not self._f8_down:
                                self._f8_down = True
                                self._enqueue("emergency", "F8")
                            elif is_up:
                                self._f8_down = False
                        elif not injected:
                            key_id = (vk, scan)
                            if is_down:
                                with self._state_lock:
                                    enabled = self._recording_enabled and not self._high_angle_active
                                    already = key_id in self._captured_keys
                                if enabled and not already and self._is_target_foreground():
                                    name = self._key_name(vk, scan, flags)
                                    if name.upper() != "F8":
                                        with self._state_lock:
                                            self._captured_keys[key_id] = name
                                        self._enqueue("keyDown", name)
                            elif is_up:
                                with self._state_lock:
                                    name = self._captured_keys.pop(key_id, None)
                                    if name is None:
                                        # Some extended keys report a slightly different scan code on key-up.
                                        for existing_id, existing_name in list(self._captured_keys.items()):
                                            if existing_id[0] == vk:
                                                name = self._captured_keys.pop(existing_id)
                                                break
                                if name is not None:
                                    self._enqueue("keyUp", name)
                except Exception:
                    pass
                return user32.CallNextHookEx(self._keyboard_hook, code, wparam, lparam)

            def mouse_callback(code, wparam, lparam):
                try:
                    if code == self.HC_ACTION and not self._stop.is_set():
                        info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        msg = int(wparam)
                        flags = int(info.flags)
                        if not (flags & self.LLMHF_INJECTED):
                            hwnd = self._hwnd_provider()
                            if hwnd:
                                x_screen, y_screen = int(info.pt.x), int(info.pt.y)
                                point = self._client_point(hwnd, x_screen, y_screen)
                                with self._state_lock:
                                    enabled = self._recording_enabled and not self._high_angle_active
                                    right_held = self._right_button_held
                                    raw_ready = self._raw_input_available
                                if msg == self.WM_MOUSEMOVE and enabled and self._is_target_foreground():
                                    if right_held:
                                        if not raw_ready:
                                            previous = self._last_screen_point
                                            self._last_screen_point = (x_screen, y_screen)
                                            if previous:
                                                dx, dy = x_screen - previous[0], y_screen - previous[1]
                                                if dx or dy:
                                                    self._enqueue("moveRel", "", dx, dy)
                                    elif point is not None:
                                        current = point
                                        now_ns = time.perf_counter_ns()
                                        if (current != self._last_screen_point
                                            and now_ns - self._last_normal_move_ns >= 4_000_000):
                                            self._last_screen_point = current
                                            self._last_normal_move_ns = now_ns
                                            self._enqueue("move", "", current[0], current[1])
                                elif msg in (self.WM_LBUTTONDOWN, self.WM_LBUTTONUP,
                                             self.WM_RBUTTONDOWN, self.WM_RBUTTONUP,
                                             self.WM_MBUTTONDOWN, self.WM_MBUTTONUP,
                                             self.WM_XBUTTONDOWN, self.WM_XBUTTONUP):
                                    down = msg in (self.WM_LBUTTONDOWN, self.WM_RBUTTONDOWN,
                                                   self.WM_MBUTTONDOWN, self.WM_XBUTTONDOWN)
                                    if msg in (self.WM_LBUTTONDOWN, self.WM_LBUTTONUP):
                                        button = "Left"
                                    elif msg in (self.WM_RBUTTONDOWN, self.WM_RBUTTONUP):
                                        button = "Right"
                                    elif msg in (self.WM_MBUTTONDOWN, self.WM_MBUTTONUP):
                                        button = "Middle"
                                    else:
                                        xbutton = (int(info.mouseData) >> 16) & 0xFFFF
                                        button = "X1" if xbutton == 1 else "X2"
                                    if down and enabled and point is not None and self._is_target_foreground():
                                        with self._state_lock:
                                            if button not in self._captured_buttons:
                                                self._captured_buttons[button] = point
                                                if button == "Right":
                                                    self._right_button_held = True
                                                    self._last_screen_point = (x_screen, y_screen)
                                                capture_point = True
                                            else:
                                                capture_point = False
                                        if capture_point:
                                            self._enqueue("buttonDown", button, point[0], point[1])
                                    elif not down:
                                        with self._state_lock:
                                            saved_point = self._captured_buttons.pop(button, None)
                                            if button == "Right":
                                                self._right_button_held = False
                                                self._last_screen_point = point
                                        if saved_point is not None:
                                            release_point = point or saved_point
                                            self._enqueue("buttonUp", button, release_point[0], release_point[1])
                                elif msg in (self.WM_MOUSEWHEEL, self.WM_MOUSEHWHEEL) and enabled and point is not None and self._is_target_foreground():
                                    signed_delta = ctypes.c_short((int(info.mouseData) >> 16) & 0xFFFF).value
                                    amount = max(1, min(120, abs(int(signed_delta))))
                                    if msg == self.WM_MOUSEWHEEL:
                                        direction = "WheelUp" if signed_delta > 0 else "WheelDown"
                                    else:
                                        direction = "WheelRight" if signed_delta > 0 else "WheelLeft"
                                    self._enqueue("wheel", f"{direction}@{amount}", point[0], point[1])
                except Exception:
                    pass
                return user32.CallNextHookEx(self._mouse_hook, code, wparam, lparam)

            self._keyboard_proc = HOOKPROC(keyboard_callback)
            self._mouse_proc = HOOKPROC(mouse_callback)
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            module = kernel32.GetModuleHandleW(None)
            self._keyboard_hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._keyboard_proc, module, 0)
            self._mouse_hook = user32.SetWindowsHookExW(self.WH_MOUSE_LL, self._mouse_proc, module, 0)
            if not self._keyboard_hook or not self._mouse_hook:
                err = ctypes.get_last_error()
                self._hook_error = f"Could not install Windows input hooks (error {err})."
                self._ready.set()
                return
            self._ready.set()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._hook_error = f"Windows input hooks failed: {exc}"
            self._ready.set()
        finally:
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                if self._keyboard_hook:
                    user32.UnhookWindowsHookEx(self._keyboard_hook)
                if self._mouse_hook:
                    user32.UnhookWindowsHookEx(self._mouse_hook)
            except Exception:
                pass
            self._keyboard_hook = None
            self._mouse_hook = None
            self._ready.set()

    def record_raw_mouse_delta(self, dx: int, dy: int) -> None:
        with self._state_lock:
            allowed = self._recording_enabled and not self._high_angle_active and self._right_button_held
        if allowed and (dx or dy) and self._is_target_foreground():
            self._enqueue("moveRel", "", dx, dy)


class BTDControlCenter:
    """Fresh standalone GUI with a single four-button tab and separate status."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1320x800")
        self.root.minsize(1000, 640)
        self.root.configure(bg=BG)

        self.docked_hwnd: int | None = None
        self.original_window_state: dict | None = None
        self._arranging_windows = False
        self._overlay_position_pending = False
        self._maximizing_window = False

        # Recording/playback state is kept in this application, independently of
        # the old BTD Macro. The .tsv rows remain compatible with the TDX format.
        self.recordings_dir = Path(__file__).resolve().parent / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._capture_events: queue.SimpleQueue = queue.SimpleQueue()
        self._input_capture = WindowsInputCapture(
            lambda: self.docked_hwnd, self._capture_event_sink
        )
        self._recording_actions: list[dict] = []
        self._recording_active = False
        self._recording_pending = False
        self._recording_finalize = False
        self._recording_stop_after_high_angle = False
        self._recording_last_action_ns = 0
        self._recording_held_keys: dict[str, str] = {}
        self._recording_held_buttons: dict[str, tuple[int, int]] = {}
        self._recording_file: Path | None = None
        self._recording_countdown_id = None
        self._runtime_poll_id = None
        self._playback_active = False
        self._playback_stop = threading.Event()
        self._playback_thread: threading.Thread | None = None
        self._playback_held_keys: set[str] = set()
        self._playback_held_buttons: set[str] = set()
        self._high_angle_running = False
        self._high_angle_cancel = threading.Event()
        self._high_angle_thread: threading.Thread | None = None
        self._injected_right_button_down = False
        self._raw_input_subclass_installed = False
        self._raw_input_subclass_proc = None
        self._raw_input_registered = False
        self._update_check_thread: threading.Thread | None = None
        self._update_download_thread: threading.Thread | None = None
        self._update_installing = False

        self._configure_styles()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.game_host.bind("<Configure>", self._on_game_host_configure, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.bind("<Map>", self._on_root_map, add="+")

        self.append_log("BTD Control Center initialized.")
        if not PYWIN32_AVAILABLE:
            self.append_log("pywin32 is missing; Roblox docking is unavailable on this computer.")
        self.set_status("Idle — ready.", "idle")
        self.root.after_idle(self._maximize_and_lock_window)
        self.root.after_idle(self._install_raw_input_receiver)
        # Network work runs on a worker so app startup and the GUI never freeze.
        self.root.after(2200, self._start_update_check)

    # ------------------------------------------------------------------
    # Theme and layout
    # ------------------------------------------------------------------
    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "TNotebook.Tab", background=PANEL_ALT, foreground=MUTED,
            padding=(18, 10), font=("Segoe UI Semibold", 10), borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL)],
            foreground=[("selected", TEXT)],
        )

    def _build_layout(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=65, minsize=580, uniform="main")
        outer.grid_columnconfigure(1, weight=35, minsize=360, uniform="main")

        # Left: dedicated Roblox host panel, matching the old app's overall layout.
        self.game_panel = tk.Frame(
            outer, bg="#070A10", highlightthickness=1, highlightbackground=BORDER
        )
        self.game_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.game_host = tk.Frame(self.game_panel, bg="#05070B")
        self.game_host.pack(fill="both", expand=True)
        self.game_placeholder = tk.Label(
            self.game_host,
            text="ROBLOX NOT DOCKED\n\nOpen Roblox, then click DOCK ROBLOX\nto place the game inside this panel.",
            bg="#05070B", fg=MUTED, justify="center",
            font=("Segoe UI Semibold", 12),
        )
        self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # Right: title/docking action, exactly one tab, then global status/activity.
        self.side_panel = tk.Frame(outer, bg=BG)
        self.side_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.side_panel.grid_columnconfigure(0, weight=1)
        self.side_panel.grid_rowconfigure(1, weight=1)

        header = tk.Frame(self.side_panel, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(3, 8))
        header.grid_columnconfigure(0, weight=1)
        title_area = tk.Frame(header, bg=BG)
        title_area.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_area, text=APP_NAME.upper(), bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).pack(anchor="w")
        tk.Label(
            title_area, text="Ball Tower Defense", bg=BG, fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(1, 0))

        self.dock_button = tk.Button(
            header, text="DOCK ROBLOX", command=self.toggle_dock,
            bg=ACCENT, fg="#101522", activebackground=ACCENT_HOVER,
            activeforeground="#101522", relief="flat", bd=0,
            font=("Segoe UI Semibold", 9), padx=12, pady=10, cursor="hand2",
        )
        self.dock_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.notebook = ttk.Notebook(self.side_panel)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.control_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.control_tab, text="  CONTROL CENTER  ")
        self._build_control_tab()

        separator = tk.Frame(self.side_panel, bg=BORDER, height=1)
        separator.grid(row=2, column=0, sticky="ew", pady=(8, 7))

        # Global Status lives outside the tab, as requested.
        self.status_frame = tk.Frame(self.side_panel, bg=PANEL, height=62)
        self.status_frame.grid(row=3, column=0, sticky="ew")
        self.status_frame.grid_propagate(False)
        tk.Label(
            self.status_frame, text="GLOBAL STATUS", bg=PANEL, fg=ACCENT,
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", padx=10, pady=(7, 0))
        self.status_var = tk.StringVar(value="Idle — ready.")
        self.status_label = tk.Label(
            self.status_frame, textvariable=self.status_var, bg=PANEL, fg=STATUS_IDLE,
            anchor="w", justify="left", wraplength=340,
            font=("Segoe UI Semibold", 11),
        )
        self.status_label.pack(fill="both", expand=True, padx=10, pady=(0, 7))
        self.status_frame.bind("<Configure>", self._resize_status_wrap, add="+")

        activity_header = tk.Frame(self.side_panel, bg=BG)
        activity_header.grid(row=4, column=0, sticky="ew", pady=(8, 4))
        tk.Label(
            activity_header, text="ACTIVITY", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        clear_button = tk.Button(
            activity_header, text="CLEAR", command=self.clear_log,
            bg=PANEL_ALT, fg=MUTED, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", bd=0, font=("Segoe UI Semibold", 8), padx=8, pady=3,
            cursor="hand2",
        )
        clear_button.pack(side="right")

        log_frame = tk.Frame(self.side_panel, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        log_frame.grid(row=5, column=0, sticky="nsew")
        self.side_panel.grid_rowconfigure(5, weight=0, minsize=110)
        self.log_text = tk.Text(
            log_frame, height=6, wrap="word", state="disabled",
            bg=PANEL, fg=MUTED, insertbackground=TEXT,
            relief="flat", bd=0, padx=9, pady=7,
            font=("Consolas", 8),
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _build_control_tab(self) -> None:
        """The only tab contains exactly the four requested feature buttons."""
        tab = self.control_tab
        for index in (0, 1):
            tab.grid_rowconfigure(index, weight=1)
            tab.grid_columnconfigure(index, weight=1)
        buttons = (
            ("RECORDING", self.on_recording, ACCENT, "#101522", ACCENT_HOVER),
            ("PLAYBACK", self.on_playback, PANEL_ALT, TEXT, BORDER),
            ("HIGH ANGLE", self.on_high_angle, PANEL_ALT, TEXT, BORDER),
            ("AUTO POSITION", self.on_auto_position, PANEL_ALT, TEXT, BORDER),
        )
        placements = ((0, 0), (0, 1), (1, 0), (1, 1))
        for (label, callback, bg, fg, active_bg), (row, column) in zip(buttons, placements):
            button = tk.Button(
                tab, text=label, command=callback,
                bg=bg, fg=fg, activebackground=active_bg, activeforeground=fg,
                relief="flat", bd=0, highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=ACCENT,
                font=("Segoe UI Semibold", 12), cursor="hand2",
                padx=12, pady=18,
            )
            button.grid(
                row=row, column=column, sticky="nsew",
                padx=(14 if column == 0 else 7, 7 if column == 0 else 14),
                pady=(14 if row == 0 else 7, 7 if row == 0 else 14),
            )
            if label == "RECORDING":
                self.record_button = button
            elif label == "PLAYBACK":
                self.playback_button = button
            elif label == "HIGH ANGLE":
                self.high_angle_button = button
            elif label == "AUTO POSITION":
                self.auto_position_button = button

    # ------------------------------------------------------------------
    # Recording, playback, and High Angle. Auto Position remains intentionally on hold.
    # ------------------------------------------------------------------
    def _is_roblox_available(self) -> bool:
        if not self.docked_hwnd:
            self.set_status("Dock Roblox first.", "warning")
            self.append_log("Feature requested while Roblox was not docked.")
            return False
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.set_status("This feature requires Windows and pywin32.", "error")
            self.append_log("Feature unavailable: Windows/pywin32 support is missing.")
            return False
        if not win32gui.IsWindow(self.docked_hwnd):
            self._handle_lost_roblox_window()
            return False
        return True

    def _ensure_roblox_focus(self, hwnd: int, timeout_ms: int = 3000) -> bool:
        """Restore the intended Roblox foreground window without sending input elsewhere."""
        if os.name != "nt" or not hwnd:
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        if not user32.IsWindow(wintypes.HWND(hwnd)):
            return False
        try:
            if user32.IsIconic(wintypes.HWND(hwnd)):
                user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
                self.root.after_idle(lambda: self._schedule_overlay_position(show=True))
        except Exception:
            pass

        foreground_value = user32.GetForegroundWindow()
        foreground = int(foreground_value or 0)
        if foreground == hwnd:
            return True
        foreground_pid = wintypes.DWORD()
        target_pid = wintypes.DWORD()
        foreground_tid = int(user32.GetWindowThreadProcessId(wintypes.HWND(foreground), ctypes.byref(foreground_pid))) if foreground else 0
        target_tid = int(user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(target_pid)))
        current_tid = int(kernel32.GetCurrentThreadId())
        attached_foreground = False
        attached_target = False
        try:
            if foreground_tid and foreground_tid != current_tid:
                attached_foreground = bool(user32.AttachThreadInput(current_tid, foreground_tid, True))
            if target_tid and target_tid != current_tid:
                attached_target = bool(user32.AttachThreadInput(current_tid, target_tid, True))
            try:
                user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
            except Exception:
                pass
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
            user32.SetActiveWindow(wintypes.HWND(hwnd))
            user32.SetFocus(wintypes.HWND(hwnd))
        finally:
            if attached_target:
                user32.AttachThreadInput(current_tid, target_tid, False)
            if attached_foreground:
                user32.AttachThreadInput(current_tid, foreground_tid, False)

        deadline = time.perf_counter() + max(0, timeout_ms) / 1000.0
        while time.perf_counter() < deadline:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                return True
            time.sleep(0.025)
        return int(user32.GetForegroundWindow() or 0) == hwnd

    def _capture_event_sink(self, event: dict) -> None:
        if event.get("type") == "emergency":
            # Interrupt long-running workers immediately; UI state is finalized on the Tk thread.
            self._playback_stop.set()
            self._high_angle_cancel.set()
        self._capture_events.put(event)

    def _start_capture_hooks(self, *, recording: bool = False) -> bool:
        if recording:
            self._register_raw_mouse_input()
        if not self._input_capture.start(recording=recording):
            self.set_status(self._input_capture.hook_error or "Could not start input capture.", "error")
            self.append_log(self._input_capture.hook_error or "Input capture failed to start.")
            return False
        self._schedule_runtime_poll()
        return True

    def _stop_capture_hooks_if_idle(self) -> None:
        if not self._recording_active and not self._recording_pending and not self._playback_active and not self._high_angle_running:
            self._input_capture.stop()
            if self._runtime_poll_id is not None:
                try:
                    self.root.after_cancel(self._runtime_poll_id)
                except tk.TclError:
                    pass
                self._runtime_poll_id = None

    def _schedule_runtime_poll(self) -> None:
        if self._runtime_poll_id is not None:
            return
        delay = 4 if self._recording_active or self._recording_finalize else 20
        self._runtime_poll_id = self.root.after(delay, self._poll_runtime_events)

    def _poll_runtime_events(self) -> None:
        self._runtime_poll_id = None
        captured = []
        while True:
            try:
                captured.append(self._capture_events.get_nowait())
            except queue.Empty:
                break
        if captured:
            captured.sort(key=lambda item: int(item.get("timestamp_ns", 0)))
            for event in captured:
                if event.get("type") == "emergency":
                    self._emergency_stop()
                elif self._recording_active or self._recording_finalize:
                    self._process_recording_event(event)
        if self._recording_active or self._recording_finalize or self._playback_active or self._high_angle_running:
            self._schedule_runtime_poll()

    def _append_recording_action(
        self, action_type: str, value: str = "", x: int = 0, y: int = 0,
        *, timestamp_ns: int | None = None,
    ) -> None:
        stamp = timestamp_ns if timestamp_ns is not None else time.perf_counter_ns()
        if self._recording_last_action_ns <= 0:
            delay_ms = 0.0
        else:
            delay_ms = max(0.0, (stamp - self._recording_last_action_ns) / 1_000_000.0)
        self._recording_last_action_ns = max(stamp, self._recording_last_action_ns)
        self._recording_actions.append({
            "type": action_type,
            "value": str(value).replace("\t", " "),
            "x": int(x),
            "y": int(y),
            "delay": delay_ms,
        })

    def _process_recording_event(self, event: dict) -> None:
        kind = str(event.get("type", ""))
        value = str(event.get("value", ""))
        x, y = int(event.get("x", 0)), int(event.get("y", 0))
        stamp = int(event.get("timestamp_ns", time.perf_counter_ns()))
        if kind == "keyDown":
            if value not in self._recording_held_keys:
                self._recording_held_keys[value] = value
                self._append_recording_action(kind, value, timestamp_ns=stamp)
        elif kind == "keyUp":
            held = self._recording_held_keys.pop(value, None)
            if held is not None:
                self._append_recording_action(kind, value, timestamp_ns=stamp)
        elif kind == "buttonDown":
            if value not in self._recording_held_buttons:
                self._recording_held_buttons[value] = (x, y)
                self._append_recording_action(kind, value, x, y, timestamp_ns=stamp)
        elif kind == "buttonUp":
            if value in self._recording_held_buttons:
                self._recording_held_buttons.pop(value, None)
                self._append_recording_action(kind, value, x, y, timestamp_ns=stamp)
        elif kind in ("move", "moveRel", "wheel"):
            if kind != "move" or not self._recording_actions or (
                self._recording_actions[-1]["type"] != "move"
                or self._recording_actions[-1]["x"] != x
                or self._recording_actions[-1]["y"] != y
            ):
                self._append_recording_action(kind, value, x, y, timestamp_ns=stamp)

    def on_recording(self) -> None:
        if self._recording_active and self._high_angle_running:
            self._recording_stop_after_high_angle = True
            self.record_button.configure(text="STOP AFTER HIGH ANGLE", bg=DANGER, fg="#FFFFFF")
            self.set_status("Recording will stop after the High Angle sequence finishes.", "warning")
            return
        if self._recording_active or self._recording_pending:
            self.stop_recording()
            return
        if self._playback_active or self._high_angle_running:
            self.set_status("Stop Playback or High Angle before starting a recording.", "warning")
            return
        if not self._is_roblox_available():
            return
        if not self._ensure_roblox_focus(self.docked_hwnd, 3000):
            self.set_status("Recording cancelled: Roblox focus could not be confirmed.", "error")
            self.append_log("Recording was not started because Roblox did not receive foreground focus.")
            return
        self._recording_pending = True
        self.record_button.configure(text="CANCEL RECORDING", bg=DANGER, fg="#FFFFFF")
        self.set_status("Recording starts in 3 seconds. Prepare the Roblox actions.", "progress")
        self.append_log("Roblox focus confirmed; recording countdown started (3 seconds).")
        self._recording_countdown_id = self.root.after(3000, self._begin_recording)

    def _begin_recording(self) -> None:
        self._recording_countdown_id = None
        if not self._recording_pending:
            return
        self._recording_pending = False
        if not self._is_roblox_available() or not self._ensure_roblox_focus(self.docked_hwnd, 1800):
            self.record_button.configure(text="RECORDING", bg=ACCENT, fg="#101522")
            self.set_status("Recording cancelled: Roblox focus was not confirmed.", "error")
            return
        self._recording_actions = []
        self._recording_held_keys.clear()
        self._recording_held_buttons.clear()
        self._recording_last_action_ns = time.perf_counter_ns()
        if not self._start_capture_hooks(recording=True):
            self._unregister_raw_mouse_input()
            self.record_button.configure(text="RECORDING", bg=ACCENT, fg="#101522")
            return
        self._recording_active = True
        self._recording_finalize = False
        self.record_button.configure(text="STOP RECORDING", bg=DANGER, fg="#FFFFFF")
        if not self._input_capture.raw_input_available:
            self.set_status("Recording. Relative mouse camera movement fallback is limited.", "warning")
            self.append_log("Raw Input registration is unavailable; right-drag camera motion may not be captured precisely.")
        else:
            self.set_status("Recording Roblox keyboard and mouse actions.", "progress")
        self.append_log("Recording started. Keyboard, mouse buttons, wheel, and cursor movement are being captured.")
        self._schedule_runtime_poll()

    def stop_recording(self) -> None:
        if self._recording_pending:
            self._recording_pending = False
            if self._recording_countdown_id is not None:
                try:
                    self.root.after_cancel(self._recording_countdown_id)
                except tk.TclError:
                    pass
                self._recording_countdown_id = None
            self.record_button.configure(text="RECORDING", bg=ACCENT, fg="#101522")
            self.set_status("Recording countdown cancelled.", "warning")
            self.append_log("Recording cancelled before capture began.")
            self._unregister_raw_mouse_input()
            self._stop_capture_hooks_if_idle()
            return
        if not self._recording_active:
            return
        self._recording_active = False
        self._recording_finalize = True
        self._input_capture.set_recording(False)
        if not self._high_angle_running and not self._playback_active:
            self._input_capture.stop()
        self._poll_runtime_events()
        now = time.perf_counter_ns()
        # Close physically held states so a recording never leaves an input held.
        for key_name in list(self._recording_held_keys.values()):
            self._append_recording_action("keyUp", key_name, timestamp_ns=now)
        self._recording_held_keys.clear()
        for button_name, point in list(self._recording_held_buttons.items()):
            self._append_recording_action("buttonUp", button_name, point[0], point[1], timestamp_ns=now)
        self._recording_held_buttons.clear()
        if self._recording_actions:
            self._append_recording_action("wait", "", timestamp_ns=time.perf_counter_ns())
        self._recording_finalize = False
        self._unregister_raw_mouse_input()
        self.record_button.configure(text="RECORDING", bg=ACCENT, fg="#101522")
        try:
            saved_path = self._save_recording()
        except Exception as exc:
            self.set_status(f"Recording save failed: {exc}", "error")
            self.append_log(f"Recording save failed: {exc}")
            self._stop_capture_hooks_if_idle()
            return
        if saved_path:
            self._recording_file = saved_path
            self.set_status(f"Recording saved: {saved_path.name} ({len(self._recording_actions)} actions).", "success")
            self.append_log(f"Recording saved to {saved_path}; actions={len(self._recording_actions)}.")
        else:
            self.set_status("No Roblox inputs were captured; no recording was saved.", "warning")
            self.append_log("Recording stopped without captured actions; no file was created.")
        self._stop_capture_hooks_if_idle()

    def _save_recording(self) -> Path | None:
        if not self._recording_actions:
            return None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.recordings_dir / f"BTD_Recording_{stamp}.tsv"
        serial = 2
        while output_path.exists():
            output_path = self.recordings_dir / f"BTD_Recording_{stamp}_{serial}.tsv"
            serial += 1
        with output_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\r\n")
            for action in self._recording_actions:
                writer.writerow((
                    action["type"], action["value"], action["x"], action["y"],
                    f"{action['delay']:.3f}",
                ))
        return output_path

    @staticmethod
    def _load_recording_file(path: Path) -> list[dict]:
        allowed = {"move", "moveRel", "keyDown", "keyUp", "buttonDown", "buttonUp", "wheel", "highAngle", "wait"}
        actions: list[dict] = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            for row in reader:
                if len(row) < 5:
                    continue
                action_type = row[0].replace("\ufeff", "").strip()
                if action_type not in allowed:
                    continue
                try:
                    x = int(float(row[2]))
                    y = int(float(row[3]))
                    delay = max(0.0, float(row[4]))
                except (ValueError, OverflowError):
                    continue
                actions.append({
                    "type": action_type,
                    "value": row[1],
                    "x": x,
                    "y": y,
                    "delay": delay,
                })
        return actions

    def on_playback(self) -> None:
        if self._playback_active:
            self.stop_playback()
            return
        if self._recording_active or self._recording_pending or self._high_angle_running:
            self.set_status("Stop Recording and High Angle before starting Playback.", "warning")
            return
        if not self._is_roblox_available():
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a BTD recording to play",
            initialdir=str(self.recordings_dir),
            filetypes=(("BTD recordings", "*.tsv"), ("All files", "*.*")),
        )
        if not selected:
            return
        path = Path(selected)
        try:
            actions = self._load_recording_file(path)
        except Exception as exc:
            messagebox.showerror("Recording could not be loaded", str(exc), parent=self.root)
            return
        if not actions:
            messagebox.showwarning("Empty recording", "The selected file contains no supported actions.", parent=self.root)
            return
        if not self._ensure_roblox_focus(self.docked_hwnd, 3000):
            self.set_status("Playback cancelled: Roblox focus could not be confirmed.", "error")
            return
        if not self._start_capture_hooks(recording=False):
            return
        self._playback_stop.clear()
        self._high_angle_cancel.clear()
        self._playback_held_keys.clear()
        self._playback_held_buttons.clear()
        self._playback_active = True
        self.playback_button.configure(text="STOP PLAYBACK", bg=DANGER, fg="#FFFFFF")
        self.set_status(f"Playing {path.name} ({len(actions)} actions).", "progress")
        self.append_log(f"Playback started from {path}; action count={len(actions)}.")
        self._playback_thread = threading.Thread(
            target=self._playback_worker,
            args=(self.docked_hwnd, actions, path),
            name="BTDPlayback",
            daemon=True,
        )
        self._playback_thread.start()

    def stop_playback(self) -> None:
        if not self._playback_active:
            return
        self._playback_stop.set()
        self.set_status("Stopping Playback and releasing held inputs safely...", "warning")

    @staticmethod
    def _key_name_to_vk(name: str) -> int:
        value = name.strip()
        special = {
            "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Return": 0x0D,
            "Shift": 0x10, "Ctrl": 0x11, "Control": 0x11, "Alt": 0x12,
            "Pause": 0x13, "Caps Lock": 0x14, "CapsLock": 0x14,
            "Esc": 0x1B, "Escape": 0x1B, "Space": 0x20,
            "Page Up": 0x21, "PageUp": 0x21, "Page Down": 0x22, "PageDown": 0x22,
            "End": 0x23,
            "Home": 0x24, "Left": 0x25, "Up": 0x26, "Right": 0x27,
            "Down": 0x28, "Print Screen": 0x2C, "Insert": 0x2D, "Delete": 0x2E,
            "Num Lock": 0x90, "NumLock": 0x90, "Scroll Lock": 0x91, "ScrollLock": 0x91,
            "LWin": 0x5B, "RWin": 0x5C, "AppsKey": 0x5D,
            "Left Shift": 0xA0, "Right Shift": 0xA1,
            "LShift": 0xA0, "RShift": 0xA1,
            "Left Ctrl": 0xA2, "Right Ctrl": 0xA3,
            "LCtrl": 0xA2, "RCtrl": 0xA3,
            "Left Control": 0xA2, "Right Control": 0xA3,
            "Left Alt": 0xA4, "Right Alt": 0xA5,
            "LAlt": 0xA4, "RAlt": 0xA5,
            "Back": 0x08,
        }
        if value in special:
            return special[value]
        upper = value.upper()
        if len(upper) == 1 and ("A" <= upper <= "Z" or "0" <= upper <= "9"):
            return ord(upper)
        if upper.startswith("F") and upper[1:].isdigit():
            number = int(upper[1:])
            if 1 <= number <= 24:
                return 0x70 + number - 1
        if upper.startswith("VK_"):
            try:
                return int(upper[3:], 16)
            except ValueError:
                pass
        if value.lower().startswith("num ") and value[-1:].isdigit():
            return 0x60 + int(value[-1])
        if upper.startswith("NUMPAD") and upper[-1:].isdigit():
            return 0x60 + int(upper[-1])
        numpad_ops = {
            "NUMPADMULTIPLY": 0x6A, "NUMPADADD": 0x6B,
            "NUMPADSUBTRACT": 0x6D, "NUMPADDECIMAL": 0x6E,
            "NUMPADDIVIDE": 0x6F, "NUMPADENTER": 0x0D,
        }
        if upper in numpad_ops:
            return numpad_ops[upper]
        if len(value) == 1:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            result = int(user32.VkKeyScanW(ord(value)))
            if result != -1:
                return result & 0xFF
        raise ValueError(f"Unsupported recorded keyboard key: {name}")

    @staticmethod
    def _send_key_input(name: str, key_up: bool) -> None:
        vk = BTDControlCenter._key_name_to_vk(name)
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]
        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]
        flags = 0x0002 if key_up else 0
        packet = INPUT(type=1, ki=KEYBDINPUT(vk, 0, flags, 0, 0))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        if user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _send_mouse_input(dx: int = 0, dy: int = 0, flags: int = 0, data: int = 0) -> None:
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]
        class INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]
        packet = INPUT(type=0, mi=MOUSEINPUT(int(dx), int(dy), int(data) & 0xFFFFFFFF, int(flags), 0, 0))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        if user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def _send_mouse_button(self, button: str, down: bool) -> None:
        flags = {
            "Left": (0x0002, 0x0004, 0),
            "Right": (0x0008, 0x0010, 0),
            "Middle": (0x0020, 0x0040, 0),
            "X1": (0x0080, 0x0100, 0x0001),
            "X2": (0x0080, 0x0100, 0x0002),
        }
        if button not in flags:
            raise ValueError(f"Unsupported mouse button: {button}")
        down_flag, up_flag, data = flags[button]
        self._send_mouse_input(flags=down_flag if down else up_flag, data=data)

    def _playback_focus(self, hwnd: int) -> bool:
        if not win32gui or not win32gui.IsWindow(hwnd):
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)

    def _wait_playback_target(self, target_time: float, hwnd: int) -> tuple[bool, float]:
        """Wait to an absolute timeline target while pausing for focus recovery."""
        recovery_total = 0.0
        while not self._playback_stop.is_set():
            if not win32gui or not win32gui.IsWindow(hwnd):
                return False, recovery_total
            if not self._playback_focus(hwnd):
                recovery_start = time.perf_counter()
                self._ui_status("Roblox focus lost; attempting safe focus recovery...", "warning")
                if not self._ensure_roblox_focus(hwnd, 5000):
                    return False, recovery_total
                recovery = max(0.0, time.perf_counter() - recovery_start)
                recovery_total += recovery
                target_time += recovery
                # Release held inputs only after Roblox is confirmed foreground.
                self._release_playback_inputs(hwnd)
                self._ui_log(f"Roblox focus restored during playback; timeline paused for {recovery:.3f}s.")
            remaining = target_time - time.perf_counter()
            if remaining <= 0:
                return True, recovery_total
            time.sleep(min(0.04, max(0.001, remaining - 0.002)))
        return False, recovery_total

    def _client_geometry(self, hwnd: int) -> tuple[int, int, int, int]:
        if not win32gui or not win32gui.IsWindow(hwnd):
            raise RuntimeError("Roblox window is not available.")
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        client_rect = win32gui.GetClientRect(hwnd)
        width, height = client_rect[2] - client_rect[0], client_rect[3] - client_rect[1]
        if width < 100 or height < 100:
            raise RuntimeError("Roblox client area is too small.")
        return int(left), int(top), int(width), int(height)

    def _move_playback_cursor(self, hwnd: int, x: int, y: int) -> None:
        if not self._playback_focus(hwnd):
            raise RuntimeError("Roblox lost foreground focus immediately before mouse input.")
        left, top, width, height = self._client_geometry(hwnd)
        px = max(left, min(left + width - 1, left + int(x)))
        py = max(top, min(top + height - 1, top + int(y)))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if not user32.SetCursorPos(px, py):
            raise ctypes.WinError(ctypes.get_last_error())

    def _playback_action(self, hwnd: int, action: dict) -> None:
        kind, value = action["type"], action["value"]
        x, y = action["x"], action["y"]
        if kind == "wait":
            return
        if kind == "highAngle":
            if not self._run_high_angle_sequence(hwnd, playback_mode=True):
                raise RuntimeError("Playback was stopped during High Angle.")
            return
        if not self._playback_focus(hwnd):
            raise RuntimeError("Roblox is not foreground; no recorded input was sent.")
        if kind == "move":
            self._move_playback_cursor(hwnd, x, y)
        elif kind == "moveRel":
            self._send_mouse_input(dx=x, dy=y, flags=0x0001)  # MOUSEEVENTF_MOVE
        elif kind in ("buttonDown", "buttonUp"):
            if value != "Right":
                self._move_playback_cursor(hwnd, x, y)
            down = kind == "buttonDown"
            self._send_mouse_button(value, down)
            if down:
                self._playback_held_buttons.add(value)
            else:
                self._playback_held_buttons.discard(value)
        elif kind in ("keyDown", "keyUp"):
            down = kind == "keyDown"
            self._send_key_input(value, not down)
            if down:
                self._playback_held_keys.add(value)
            else:
                self._playback_held_keys.discard(value)
        elif kind == "wheel":
            self._move_playback_cursor(hwnd, x, y)
            direction, _, amount_text = value.partition("@")
            amount = max(1, min(120, abs(int(amount_text or "120"))))
            if direction == "WheelUp":
                self._send_mouse_input(flags=0x0800, data=amount)
            elif direction == "WheelDown":
                self._send_mouse_input(flags=0x0800, data=-amount)
            elif direction == "WheelRight":
                self._send_mouse_input(flags=0x1000, data=amount)
            elif direction == "WheelLeft":
                self._send_mouse_input(flags=0x1000, data=-amount)
            else:
                raise ValueError(f"Unsupported recorded wheel direction: {direction}")

    def _release_playback_inputs(self, hwnd: int) -> None:
        if not self._playback_held_keys and not self._playback_held_buttons:
            return
        if not self._ensure_roblox_focus(hwnd, 1500):
            self._ui_log("Could not confirm Roblox focus to release playback inputs; no key-up was sent to another app.")
            return
        for key_name in list(self._playback_held_keys):
            try:
                self._send_key_input(key_name, True)
            except Exception as exc:
                self._ui_log(f"Key release failed for {key_name}: {exc}")
        for button_name in list(self._playback_held_buttons):
            try:
                self._send_mouse_button(button_name, False)
            except Exception as exc:
                self._ui_log(f"Mouse release failed for {button_name}: {exc}")
        self._playback_held_keys.clear()
        self._playback_held_buttons.clear()

    def _playback_worker(self, hwnd: int, actions: list[dict], path: Path) -> None:
        started = time.perf_counter()
        timeline_ms = 0.0
        error = ""
        try:
            for index, action in enumerate(actions, start=1):
                if self._playback_stop.is_set():
                    break
                timeline_ms += max(0.0, float(action["delay"]))
                reached, recovery_seconds = self._wait_playback_target(started + timeline_ms / 1000.0, hwnd)
                started += recovery_seconds
                if not reached or self._playback_stop.is_set():
                    break
                if not self._playback_focus(hwnd):
                    recovery_start = time.perf_counter()
                    if not self._ensure_roblox_focus(hwnd, 3000):
                        raise RuntimeError("Roblox focus could not be restored. Playback stopped before the next input.")
                    started += max(0.0, time.perf_counter() - recovery_start)
                    self._release_playback_inputs(hwnd)
                if self._playback_stop.is_set():
                    break
                self._playback_action(hwnd, action)
                if index % 100 == 0:
                    self._ui_status(f"Playback in progress: {index}/{len(actions)} actions.", "progress")
        except Exception as exc:
            error = str(exc)
        finally:
            self._release_playback_inputs(hwnd)
            self._ui_call(self._finish_playback, path, len(actions), error, self._playback_stop.is_set())

    def _finish_playback(self, path: Path, action_count: int, error: str, stopped: bool) -> None:
        self._playback_active = False
        self.playback_button.configure(text="PLAYBACK", bg=PANEL_ALT, fg=TEXT)
        if error:
            self.set_status(f"Playback stopped: {error}", "error")
            self.append_log(f"Playback failed for {path.name}: {error}")
        elif stopped:
            self.set_status("Playback stopped by user / Emergency Stop.", "warning")
            self.append_log(f"Playback stopped before completion: {path.name}.")
        else:
            self.set_status(f"Playback completed: {path.name}.", "success")
            self.append_log(f"Playback completed: {path.name}; actions={action_count}.")
        self._playback_thread = None
        self._playback_stop.clear()
        self._high_angle_cancel.clear()
        self._stop_capture_hooks_if_idle()

    def on_high_angle(self) -> None:
        if self._high_angle_running:
            self._high_angle_cancel.set()
            self.set_status("Stopping High Angle safely...", "warning")
            return
        if self._playback_active or self._recording_pending:
            self.set_status("Stop Playback / wait for Recording countdown before High Angle.", "warning")
            return
        if not self._is_roblox_available():
            return
        if not self._ensure_roblox_focus(self.docked_hwnd, 3000):
            self.set_status("High Angle cancelled: Roblox focus could not be confirmed.", "error")
            return
        if self._recording_active:
            self._append_recording_action("highAngle", "")
        if not self._start_capture_hooks(recording=self._recording_active):
            return
        self._high_angle_cancel.clear()
        self._high_angle_running = True
        self.high_angle_button.configure(text="STOP HIGH ANGLE", bg=DANGER, fg="#FFFFFF")
        self._input_capture.set_high_angle(True)
        self.set_status("Applying High Angle from 15 cm right of the Roblox center...", "progress")
        self.append_log("High Angle started at the center-right camera anchor (15 cm offset).")
        self._high_angle_thread = threading.Thread(
            target=self._high_angle_worker,
            args=(self.docked_hwnd,),
            name="BTDHighAngle",
            daemon=True,
        )
        self._high_angle_thread.start()

    def _high_angle_worker(self, hwnd: int) -> None:
        error = ""
        succeeded = False
        try:
            succeeded = self._run_high_angle_sequence(hwnd, playback_mode=False)
        except Exception as exc:
            error = str(exc)
        finally:
            self._ui_call(self._finish_high_angle, succeeded, error)

    def _run_high_angle_sequence(self, hwnd: int, *, playback_mode: bool) -> bool:
        """TDX camera movement, starting 15 cm right of the client-area center."""
        if not win32gui or not win32gui.IsWindow(hwnd):
            raise RuntimeError("Roblox window is no longer available.")
        if playback_mode:
            if not self._playback_focus(hwnd):
                raise RuntimeError("Roblox lost focus before High Angle started.")
        elif not self._ensure_roblox_focus(hwnd, 3000):
            raise RuntimeError("Could not confirm Roblox focus before High Angle input.")

        left, top, width, height = self._client_geometry(hwnd)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        if get_dpi:
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            dpi = int(get_dpi(wintypes.HWND(hwnd)))
        else:
            dpi = 96
        if dpi < 48 or dpi > 960:
            dpi = 96
        right_offset_px = int(round((15.0 / 2.54) * dpi))
        target_x = left + width // 2 + right_offset_px
        target_y = top + height // 2
        if target_x >= left + width:
            raise RuntimeError(
                f"Roblox client is too narrow for a 15 cm rightward offset at {dpi} DPI."
            )
        if self._high_angle_cancel.is_set() or (playback_mode and self._playback_stop.is_set()):
            return False
        anchor_focus_ok = (
            self._playback_focus(hwnd)
            if playback_mode
            else self._ensure_roblox_focus(hwnd, 1200)
        )
        if not anchor_focus_ok:
            raise RuntimeError("Roblox lost focus before the camera anchor move.")
        if not user32.SetCursorPos(int(target_x), int(target_y)):
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.250)
        if self._high_angle_cancel.is_set() or (playback_mode and self._playback_stop.is_set()):
            return False
        if playback_mode:
            if not self._playback_focus(hwnd):
                raise RuntimeError("Roblox lost focus before camera drag.")
        elif not self._ensure_roblox_focus(hwnd, 1200):
            raise RuntimeError("Roblox lost focus before camera drag.")

        self._send_mouse_input(flags=0x0008)  # MOUSEEVENTF_RIGHTDOWN
        self._injected_right_button_down = True
        try:
            time.sleep(0.100)
            for step in range(50):
                if self._high_angle_cancel.is_set() or (playback_mode and self._playback_stop.is_set()):
                    return False
                if playback_mode:
                    focused = self._playback_focus(hwnd)
                else:
                    focused = self._playback_focus(hwnd)
                if not focused:
                    raise RuntimeError("Roblox lost focus during the High Angle camera drag.")
                self._send_mouse_input(dx=0, dy=20, flags=0x0001)  # relative raw movement
                time.sleep(0.008)
            time.sleep(0.120)
        finally:
            # Do not send a release to another application if Roblox lost focus.
            if self._playback_focus(hwnd):
                try:
                    self._send_mouse_input(flags=0x0010)  # MOUSEEVENTF_RIGHTUP
                    self._injected_right_button_down = False
                except Exception:
                    pass
            else:
                if self._ensure_roblox_focus(hwnd, 1200):
                    try:
                        self._send_mouse_input(flags=0x0010)
                        self._injected_right_button_down = False
                    except Exception:
                        pass

        if self._high_angle_cancel.is_set() or (playback_mode and self._playback_stop.is_set()):
            return False
        for step in range(15):
            if self._high_angle_cancel.is_set() or (playback_mode and self._playback_stop.is_set()):
                return False
            if not self._playback_focus(hwnd):
                raise RuntimeError("Roblox lost focus during High Angle zoom-out.")
            self._send_mouse_input(flags=0x0800, data=-120)  # MOUSEEVENTF_WHEEL, one notch down
            time.sleep(0.045)
        return True

    def _finish_high_angle(self, succeeded: bool, error: str) -> None:
        self._high_angle_running = False
        self._input_capture.set_high_angle(False)
        self.high_angle_button.configure(text="HIGH ANGLE", bg=PANEL_ALT, fg=TEXT)
        self._high_angle_thread = None
        should_stop_recording = self._recording_stop_after_high_angle and self._recording_active
        self._recording_stop_after_high_angle = False
        if self._recording_active:
            # The AHK recorder resets its action clock after the synthetic camera sequence.
            self._recording_last_action_ns = time.perf_counter_ns()
        if error:
            self.set_status(f"High Angle failed: {error}", "error")
            self.append_log(f"High Angle failed: {error}")
        elif succeeded:
            self.set_status("High Angle completed (15 cm rightward anchor).", "success")
            self.append_log("High Angle finished: right-drag and 15 zoom-out notches completed.")
        else:
            self.set_status("High Angle stopped.", "warning")
            self.append_log("High Angle was cancelled before completion.")
        self._high_angle_cancel.clear()
        if should_stop_recording and self._recording_active:
            self.stop_recording()
        self._stop_capture_hooks_if_idle()

    def _emergency_stop(self) -> None:
        actions = []
        if self._recording_active and self._high_angle_running:
            self._recording_stop_after_high_angle = True
            self.record_button.configure(text="STOP AFTER HIGH ANGLE", bg=DANGER, fg="#FFFFFF")
            actions.append("recording will stop")
        elif self._recording_active or self._recording_pending:
            self.stop_recording()
            actions.append("recording stopped")
        if self._playback_active:
            self._playback_stop.set()
            actions.append("playback stopping")
        if self._high_angle_running:
            self._high_angle_cancel.set()
            actions.append("High Angle stopping")
        if not actions:
            return
        self.set_status("Emergency Stop: " + ", ".join(actions) + ".", "warning")
        self.append_log("F8 Emergency Stop: " + ", ".join(actions) + ".")

    def on_auto_position(self) -> None:
        self.set_status("Auto Position is intentionally not implemented yet.", "warning")
        self.append_log("Auto Position is on hold; only Recording, Playback, and High Angle are being implemented.")

    def _ui_call(self, callback, *args) -> None:
        try:
            self.root.after(0, lambda: callback(*args))
        except tk.TclError:
            pass

    def _ui_status(self, message: str, state: str = "idle") -> None:
        self._ui_call(self.set_status, message, state)

    def _ui_log(self, message: str) -> None:
        self._ui_call(self.append_log, message)

    # ------------------------------------------------------------------
    # Raw mouse input receiver used to capture right-drag camera deltas.
    # ------------------------------------------------------------------
    def _install_raw_input_receiver(self) -> None:
        if os.name != "nt" or self._raw_input_subclass_installed:
            return
        try:
            from ctypes import wintypes
            LRESULT = ctypes.c_ssize_t
            SUBCLASSPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT, ctypes.c_size_t,
                ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t,
            )
            comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            subclass_id = 0x42544443

            def wndproc(hwnd, msg, wparam, lparam, _subclass_id, _ref_data):
                if int(msg) == 0x00FF:  # WM_INPUT
                    try:
                        self._handle_raw_input(int(lparam))
                    except Exception:
                        pass
                result = comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)
                if int(msg) == 0x0082:  # WM_NCDESTROY
                    try:
                        comctl32.RemoveWindowSubclass(hwnd, self._raw_input_subclass_proc, subclass_id)
                    except Exception:
                        pass
                return result

            self._raw_input_subclass_proc = SUBCLASSPROC(wndproc)
            comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
            comctl32.SetWindowSubclass.restype = wintypes.BOOL
            comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            comctl32.DefSubclassProc.restype = LRESULT
            comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t]
            hwnd = wintypes.HWND(self.root.winfo_id())
            if not comctl32.SetWindowSubclass(hwnd, self._raw_input_subclass_proc, subclass_id, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self._raw_input_subclass_installed = True
        except Exception as exc:
            self.append_log(f"Raw mouse input receiver unavailable: {exc}")

    def _register_raw_mouse_input(self) -> bool:
        if os.name != "nt" or not self._raw_input_subclass_installed:
            return False
        try:
            from ctypes import wintypes

            class RAWINPUTDEVICE(ctypes.Structure):
                _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                            ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]
            device = RAWINPUTDEVICE(1, 2, 0x00000100, wintypes.HWND(self.root.winfo_id()))  # RIDEV_INPUTSINK
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
            user32.RegisterRawInputDevices.restype = wintypes.BOOL
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._raw_input_registered = True
            self._input_capture._raw_input_available = True
            return True
        except Exception as exc:
            self._raw_input_registered = False
            self._input_capture._raw_input_available = False
            self.append_log(f"Raw mouse capture not available; cursor-delta fallback will be used. {exc}")
            return False

    def _unregister_raw_mouse_input(self) -> None:
        if os.name != "nt" or not self._raw_input_registered:
            return
        try:
            from ctypes import wintypes
            class RAWINPUTDEVICE(ctypes.Structure):
                _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                            ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]
            device = RAWINPUTDEVICE(1, 2, 0x00000001, wintypes.HWND())  # RIDEV_REMOVE
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
            user32.RegisterRawInputDevices.restype = wintypes.BOOL
            user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE))
        except Exception:
            pass
        self._raw_input_registered = False
        self._input_capture._raw_input_available = False

    def _handle_raw_input(self, raw_handle: int) -> None:
        if not self._recording_active or not self._input_capture.right_button_held:
            return
        if not self.docked_hwnd or not self._input_capture._is_target_foreground():
            return
        from ctypes import wintypes

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                        ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]
        class BUTTONS_STRUCT(ctypes.Structure):
            _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]
        class BUTTONS_UNION(ctypes.Union):
            _fields_ = [("ulButtons", wintypes.ULONG), ("buttonStruct", BUTTONS_STRUCT)]
        class RAWMOUSE(ctypes.Structure):
            _anonymous_ = ("buttons",)
            _fields_ = [("usFlags", wintypes.USHORT), ("buttons", BUTTONS_UNION),
                        ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                        ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]
        class RAWINPUTDATA(ctypes.Union):
            _fields_ = [("mouse", RAWMOUSE)]
        class RAWINPUT(ctypes.Structure):
            _anonymous_ = ("data",)
            _fields_ = [("header", RAWINPUTHEADER), ("data", RAWINPUTDATA)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                           ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        user32.GetRawInputData.restype = wintypes.UINT
        result = user32.GetRawInputData(wintypes.HANDLE(raw_handle), 0x10000003, None, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF or size.value < ctypes.sizeof(RAWINPUT):
            return
        buffer = (ctypes.c_byte * size.value)()
        read_size = wintypes.UINT(size.value)
        result = user32.GetRawInputData(wintypes.HANDLE(raw_handle), 0x10000003, ctypes.byref(buffer), ctypes.byref(read_size), header_size)
        if result == 0xFFFFFFFF:
            return
        raw = ctypes.cast(ctypes.byref(buffer), ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType != 0:
            return
        if raw.mouse.usFlags & WindowsInputCapture.MOUSE_MOVE_ABSOLUTE:
            return
        self._input_capture.record_raw_mouse_delta(int(raw.mouse.lLastX), int(raw.mouse.lLastY))

    # ------------------------------------------------------------------
    # GitHub updater. Control Center releases are isolated from BTD Macro.
    # ------------------------------------------------------------------
    def _start_update_check(self) -> None:
        if self._update_check_thread and self._update_check_thread.is_alive():
            return
        self.append_log(f"Checking for Control Center updates (current: {APP_VERSION})...")
        self._update_check_thread = threading.Thread(
            target=self._update_check_worker, name="BTDUpdateCheck", daemon=True
        )
        self._update_check_thread.start()

    def _update_check_worker(self) -> None:
        try:
            release = _find_latest_control_center_release()
            current = _version_tuple(APP_VERSION)
            latest = _version_tuple(release["version"]) if release else None
            if release and current and latest and latest > current:
                self._ui_call(self._prompt_update, release)
            else:
                self._ui_call(self._update_check_finished, bool(release))
        except Exception as exc:
            self._ui_call(self._update_check_failed, str(exc))

    def _update_check_finished(self, found_release: bool) -> None:
        if found_release:
            self.append_log(f"Control Center is up to date ({APP_VERSION}).")
        else:
            self.append_log("No published Control Center update was found.")

    def _update_check_failed(self, error: str) -> None:
        # A failed network check is non-fatal; the app remains fully usable offline.
        self.append_log(f"Update check unavailable: {error}")

    def _prompt_update(self, release: dict) -> None:
        if self._update_installing:
            return
        notes = release.get("notes") or "No release notes were provided."
        notes = str(notes).strip()[:1800]
        prompt = (
            f"A new BTD Control Center version is available.\n\n"
            f"Installed: {APP_VERSION}\nAvailable: {release['version']}\n\n"
            f"Release notes:\n{notes}\n\n"
            "Download and install this update now?"
        )
        self.append_log(f"Update available: {release['version']}.")
        if not messagebox.askyesno("BTD Control Center update", prompt, parent=self.root):
            self.append_log("Update postponed by user.")
            return
        if self._recording_active or self._recording_pending or self._playback_active or self._high_angle_running:
            messagebox.showwarning(
                "Finish current action first",
                "The update was not started because Recording, Playback, or High Angle is active. "
                "Stop the current action and restart the app to check again.",
                parent=self.root,
            )
            return
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Manual update required",
                "This updater currently updates the Python .py build. Packaged executable updates must be installed manually.",
                parent=self.root,
            )
            return
        self._update_installing = True
        self.set_status(f"Downloading update {release['version']}...", "progress")
        self.append_log(f"Downloading Control Center {release['version']} from GitHub.")
        self._update_download_thread = threading.Thread(
            target=self._update_download_worker, args=(release,),
            name="BTDUpdateDownload", daemon=True,
        )
        self._update_download_thread.start()

    def _update_download_worker(self, release: dict) -> None:
        try:
            app_path = Path(__file__).resolve()
            staged = _download_and_validate_update(release, APP_VERSION, app_path)
            self._ui_call(self._install_staged_update, staged, release)
        except Exception as exc:
            self._ui_call(self._update_download_failed, str(exc))

    def _update_download_failed(self, error: str) -> None:
        self._update_installing = False
        self.set_status("Update failed; current version is unchanged.", "error")
        self.append_log(f"Update download/verification failed: {error}")
        messagebox.showerror(
            "Update failed",
            f"The update could not be verified or downloaded. Your current version was not changed.\n\n{error}",
            parent=self.root,
        )

    def _install_staged_update(self, staged_path: Path, release: dict) -> None:
        app_path = Path(__file__).resolve()
        backup_path: Path | None = None
        try:
            if self._recording_active or self._recording_pending or self._playback_active or self._high_angle_running:
                raise RuntimeError("A feature became active while the update was downloading. Stop it and retry.")
            if self.docked_hwnd is not None and not self.undock_roblox():
                raise RuntimeError("Roblox could not be safely undocked. The update was not installed.")

            backup_dir = app_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"BTD_Control_Center_{APP_VERSION}_{stamp}.py.bak"
            shutil.copy2(app_path, backup_path)

            # Stage and live file share a directory, so os.replace is atomic.
            os.replace(staged_path, app_path)
            marker_path = app_path.with_name(app_path.name + ".startup-ok")
            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                pass
            child = None
            try:
                env = os.environ.copy()
                env["BTD_CC_BOOTSTRAPPED"] = "1"
                env["BTD_CC_UPDATE_SUCCESS_FILE"] = str(marker_path)
                startupinfo = None
                creationflags = 0
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                child = subprocess.Popen(
                    [sys.executable, str(app_path), *sys.argv[1:]],
                    cwd=str(app_path.parent), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, creationflags=creationflags, startupinfo=startupinfo,
                )

                # A successful Popen is not enough: the new app confirms that
                # its GUI initialized and entered the event loop before exit.
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    if marker_path.is_file():
                        break
                    if child.poll() is not None:
                        raise RuntimeError("The updated app exited before confirming startup.")
                    time.sleep(0.10)
                else:
                    raise RuntimeError("The updated app did not confirm startup within 10 seconds.")
            except Exception:
                if child is not None and child.poll() is None:
                    try:
                        child.terminate()
                        child.wait(timeout=2.0)
                    except Exception:
                        try:
                            child.kill()
                        except Exception:
                            pass
                shutil.copy2(backup_path, app_path)
                try:
                    marker_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.append_log(f"Updated to Control Center {release['version']}; startup confirmed; backup saved at {backup_path}.")
            self.set_status(f"Updated to version {release['version']}. Restarting...", "success")
            self._input_capture.stop()
            self._unregister_raw_mouse_input()
            self.root.after(250, self.root.destroy)
        except Exception as exc:
            self._update_installing = False
            try:
                if staged_path.exists():
                    staged_path.unlink()
            except OSError:
                pass
            self.set_status("Update failed; current version was retained.", "error")
            self.append_log(f"Update install failed: {exc}")
            messagebox.showerror(
                "Update failed",
                f"The update was not completed. Your previous version was kept.\n\n{exc}",
                parent=self.root,
            )

    # ------------------------------------------------------------------
    # Global status and activity log
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_status(message: str) -> str:
        value = message.casefold()
        if any(term in value for term in ("error", "failed", "could not", "not found")):
            return "error"
        if any(term in value for term in ("warning", "unavailable", "not connected", "cancelled")):
            return "warning"
        if any(term in value for term in ("starting", "checking", "docking", "restoring", "in progress")):
            return "progress"
        if any(term in value for term in ("saved", "loaded", "docked", "undocked", "restored", "closed")):
            return "success"
        return "idle"

    def _resize_status_wrap(self, event=None) -> None:
        try:
            width = event.width if event is not None else self.status_frame.winfo_width()
            self.status_label.configure(wraplength=max(160, width - 22))
        except (AttributeError, tk.TclError):
            pass

    def set_status(self, message: str, state: str | None = None) -> None:
        full = " ".join(str(message).split())
        display = textwrap.shorten(full, width=66, placeholder="…") or "Idle — ready."
        self.status_var.set(display)
        category = state or self._classify_status(full)
        colors = {
            "progress": STATUS_PROGRESS,
            "success": STATUS_SUCCESS,
            "warning": STATUS_WARNING,
            "error": STATUS_ERROR,
            "idle": STATUS_IDLE,
        }
        self.status_label.configure(fg=colors.get(category, STATUS_IDLE))
        if display != full:
            self.append_log(f"Status detail: {full}")

    def append_log(self, message: str) -> None:
        if not hasattr(self, "log_text"):
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message.rstrip()}\n")
        # Keep the visible activity area light and responsive on lower-spec PCs.
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 300:
            self.log_text.delete("1.0", "51.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.set_status("Activity log cleared.", "success")

    # ------------------------------------------------------------------
    # Window sizing: normal maximized work area; taskbar remains visible.
    # ------------------------------------------------------------------
    def _maximize_and_lock_window(self) -> None:
        if self._maximizing_window:
            return
        self._maximizing_window = True
        try:
            self.root.attributes("-fullscreen", False)
            if os.name == "nt":
                self.root.state("zoomed")
                self.root.update_idletasks()
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
                set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
                get_long.argtypes = [wintypes.HWND, ctypes.c_int]
                get_long.restype = ctypes.c_ssize_t
                set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
                set_long.restype = ctypes.c_ssize_t
                hwnd = wintypes.HWND(self.root.winfo_id())
                style = int(get_long(hwnd, -16))
                locked = style & ~(0x00040000 | 0x00010000)  # no resize/maximize box
                if locked != style:
                    ctypes.set_last_error(0)
                    set_long(hwnd, -16, locked)
                user32.SetWindowPos(
                    hwnd, wintypes.HWND(0), 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020,
                )
                user32.ShowWindow(hwnd, 3)  # maximize in the monitor work area
            else:
                try:
                    self.root.state("zoomed")
                except tk.TclError:
                    pass
                self.root.resizable(False, False)
        except Exception as exc:
            self.append_log(f"Window sizing fallback: {exc}")
        finally:
            self._maximizing_window = False

    def _on_root_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self._maximizing_window:
            return
        try:
            if self.root.state() == "normal":
                self.root.after_idle(self._maximize_and_lock_window)
            if self.docked_hwnd is not None:
                self._schedule_overlay_position()
        except tk.TclError:
            pass

    def _on_root_map(self, event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self.docked_hwnd is not None:
            self._schedule_overlay_position(show=True)

    # ------------------------------------------------------------------
    # Roblox docking / undocking (Windows + pywin32)
    # ------------------------------------------------------------------
    @staticmethod
    def _get_process_name(hwnd: int) -> str | None:
        if os.name != "nt":
            return None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_pid = user32.GetWindowThreadProcessId
            get_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            get_pid.restype = wintypes.DWORD
            pid = wintypes.DWORD()
            if not get_pid(wintypes.HWND(hwnd), ctypes.byref(pid)):
                return None
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            query_path = kernel32.QueryFullProcessImageNameW
            query_path.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            query_path.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, pid.value)
            if not handle:
                return None
            try:
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buffer))
                if not query_path(handle, 0, buffer, ctypes.byref(size)):
                    return None
                return os.path.basename(buffer.value).casefold()
            finally:
                close_handle(handle)
        except Exception:
            return None

    def _find_roblox_window(self) -> int | None:
        if not PYWIN32_AVAILABLE or os.name != "nt":
            return None
        own_hwnd = int(self.root.winfo_id())
        candidates: list[tuple[int, int, int, str, str]] = []
        browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
        preferred = {"robloxplayerbeta.exe", "robloxplayer.exe", "roblox.exe"}

        def visit(hwnd: int, _extra) -> bool:
            try:
                if hwnd == own_hwnd or not win32gui.IsWindowVisible(hwnd):
                    return True
                if win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4)):
                    return True
                title = win32gui.GetWindowText(hwnd).strip()
                process = self._get_process_name(hwnd) or ""
                by_title = "roblox" in title.casefold()
                by_process = "roblox" in process
                if not by_title and not by_process:
                    return True
                if process in browsers or "studio" in process or "crash" in process:
                    return True
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width, height = right - left, bottom - top
                if width < 200 or height < 200:
                    return True
                rank = 5 if process in preferred else (3 if by_process else 1)
                candidates.append((rank, width * height, hwnd, title, process or "unknown process"))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(visit, None)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        rank, area, hwnd, title, process = candidates[0]
        self.append_log(f"Selected Roblox window: {title or '(untitled)'} / {process} / {area}px².")
        return hwnd

    @staticmethod
    def _assert_compatible_dpi_contexts(child_hwnd: int, parent_hwnd: int) -> None:
        if os.name != "nt":
            raise RuntimeError("Roblox docking is only supported on Windows.")
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_context = user32.GetWindowDpiAwarenessContext
            get_context.argtypes = [ctypes.c_void_p]
            get_context.restype = ctypes.c_void_p
            compare = user32.AreDpiAwarenessContextsEqual
            compare.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            compare.restype = ctypes.c_int
            child = get_context(child_hwnd)
            parent = get_context(parent_hwnd)
            if child and parent and not compare(child, parent):
                raise RuntimeError(
                    "Roblox and this app use different Windows DPI-awareness modes. "
                    "Docking was cancelled to avoid changing Roblox scaling or mouse coordinates."
                )
        except AttributeError:
            return

    @staticmethod
    def _set_owner(hwnd: int, owner_hwnd: int) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        setter.restype = ctypes.c_ssize_t
        ctypes.set_last_error(0)
        setter(wintypes.HWND(hwnd), -8, ctypes.c_ssize_t(owner_hwnd))  # GWLP_HWNDPARENT
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)

    def toggle_dock(self) -> None:
        if self._recording_active or self._recording_pending or self._playback_active or self._high_angle_running:
            self.set_status("Stop active automation before docking or undocking Roblox.", "warning")
            return
        if self.docked_hwnd is not None:
            self.undock_roblox()
        else:
            self.dock_roblox()

    def dock_roblox(self) -> None:
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            message = "Roblox docking requires Windows and the pywin32 package."
            self.set_status(message, "warning")
            self.append_log(message)
            return
        hwnd = self._find_roblox_window()
        if hwnd is None:
            message = "Roblox window not found. Open Roblox, then try again."
            self.set_status(message, "warning")
            self.append_log(message)
            return

        def step(name: str, action):
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError(f"{name}: Roblox window handle is no longer valid.")
            self.append_log(f"Dock step: {name}")
            result = action()
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError(f"{name}: Roblox window closed during docking.")
            return result

        try:
            self.root.update_idletasks()
            self.game_host.update_idletasks()
            style = int(step("read window style", lambda: win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)))
            if style & 0x40000000:  # WS_CHILD
                raise RuntimeError("The selected Roblox window is already a child window; it was left unchanged.")
            owner = int(step("read owner", lambda: win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4))) or 0)
            ex_style = int(step("read extended style", lambda: win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)))
            rect = step("read window rectangle", lambda: win32gui.GetWindowRect(hwnd))
            placement = step("read window placement", lambda: win32gui.GetWindowPlacement(hwnd))
            visible = bool(step("read visibility", lambda: win32gui.IsWindowVisible(hwnd)))
            title = step("read title", lambda: win32gui.GetWindowText(hwnd))
            iconic = bool(step("read minimized state", lambda: win32gui.IsIconic(hwnd)))
            self.original_window_state = {
                "owner": owner, "style": style, "ex_style": ex_style, "rect": rect,
                "placement": placement, "visible": visible, "title": title, "was_iconic": iconic,
            }

            root_hwnd = int(self.root.winfo_id())
            if root_hwnd == hwnd:
                raise RuntimeError("Roblox and the Control Center resolved to the same window.")
            self._assert_compatible_dpi_contexts(hwnd, root_hwnd)
            self.docked_hwnd = hwnd
            self._arranging_windows = True

            # Keep Roblox a top-level window owned by the app (not SetParent/child).
            ex_style &= ~0x00040000  # WS_EX_APPWINDOW
            step("remove separate taskbar button", lambda: win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style))
            overlay_style = (style & ~(0x40000000 | 0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000)) | 0x80000000 | win32con.WS_VISIBLE
            step("apply top-level overlay style", lambda: win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, overlay_style))
            step("set Control Center as Roblox owner", lambda: self._set_owner(hwnd, root_hwnd))
            step(
                "refresh Roblox window styles",
                lambda: win32gui.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                    | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED,
                ),
            )
            actual_owner = int(step("verify window owner", lambda: win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4))) or 0)
            if actual_owner != root_hwnd:
                raise RuntimeError("Windows did not apply the expected Roblox owner relationship.")
            actual_ex = int(step("verify taskbar style", lambda: win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)))
            if actual_ex & 0x00040000:
                raise RuntimeError("Windows retained the separate taskbar-button style.")
            if visible:
                step("show Roblox", lambda: win32gui.ShowWindow(hwnd, win32con.SW_SHOW))
            step("place Roblox in game panel", lambda: self._position_roblox_overlay(show=True, force=True))

            self.game_placeholder.place_forget()
            self.dock_button.configure(text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF")
            self.set_status("Roblox docked inside the game panel.", "success")
            self.append_log("Roblox is docked as an owned top-level window. Original window settings were saved for undocking.")
        except Exception as exc:
            restore_error = None
            try:
                if self.original_window_state is not None:
                    self._restore_roblox_window(hwnd, self.original_window_state)
            except Exception as restore_exc:
                restore_error = str(restore_exc)
            if restore_error:
                self.dock_button.configure(text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF")
                self.set_status("Docking failed; Roblox restoration needs attention.", "error")
                self.append_log(f"Restoration after docking failure also failed: {restore_error}")
            else:
                self.docked_hwnd = None
                self.original_window_state = None
                self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
                self.dock_button.configure(text="DOCK ROBLOX", bg=ACCENT, fg="#101522")
                self.set_status(f"Roblox was not docked: {exc}", "error")
                self.append_log(f"Docking failed: {exc}")
        finally:
            self._arranging_windows = False

    def _on_game_host_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.game_host:
            return
        if self.docked_hwnd is not None and not self._arranging_windows:
            self._schedule_overlay_position()

    def _schedule_overlay_position(self, *, show: bool = False) -> None:
        if self._overlay_position_pending:
            return
        self._overlay_position_pending = True

        def run() -> None:
            self._overlay_position_pending = False
            self._position_roblox_overlay(show=show)

        try:
            self.root.after_idle(run)
        except tk.TclError:
            self._overlay_position_pending = False

    def _position_roblox_overlay(self, *, show: bool = False, force: bool = False) -> None:
        hwnd = self.docked_hwnd
        if hwnd is None or not PYWIN32_AVAILABLE:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                self._handle_lost_roblox_window()
                return
            if self.root.state() == "iconic":
                return
            self.root.update_idletasks()
            self.game_host.update_idletasks()
            host = int(self.game_host.winfo_id())
            left, top, right, bottom = win32gui.GetClientRect(host)
            width, height = max(1, right - left), max(1, bottom - top)
            x, y = win32gui.ClientToScreen(host, (0, 0))
            flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
            if force:
                flags |= win32con.SWP_FRAMECHANGED
            win32gui.SetWindowPos(hwnd, 0, int(x), int(y), width, height, flags)
            if show and win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, getattr(win32con, "SW_SHOWNOACTIVATE", 4))
        except Exception as exc:
            self.append_log(f"Window positioning warning: {exc}")

    def _handle_lost_roblox_window(self) -> None:
        self.docked_hwnd = None
        self.original_window_state = None
        self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.dock_button.configure(text="DOCK ROBLOX", bg=ACCENT, fg="#101522")
        self.set_status("Roblox closed; game panel is empty.", "warning")
        self.append_log("Roblox closed independently; docking state was cleared.")

    def _restore_roblox_window(self, hwnd: int, state: dict) -> None:
        if not PYWIN32_AVAILABLE or not win32gui.IsWindow(hwnd):
            raise RuntimeError("Cannot restore Roblox because its window is unavailable.")
        errors: list[str] = []
        original_owner = int(state.get("owner", 0) or 0)
        root_hwnd = int(self.root.winfo_id())

        def attempt(label: str, action) -> None:
            try:
                if not win32gui.IsWindow(hwnd):
                    raise RuntimeError("Roblox window handle became invalid.")
                action()
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                self.append_log(f"Restore step failed ({label}): {exc}")

        current_owner = int(win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4)) or 0)
        if current_owner != original_owner:
            if current_owner in (0, root_hwnd):
                attempt("restore owner", lambda: self._set_owner(hwnd, original_owner))
            else:
                errors.append("Roblox owner changed unexpectedly; left it unchanged.")

        saved_style = int(state["style"])
        if saved_style & 0x40000000:
            errors.append("Saved window style is WS_CHILD; refusing unsafe restoration.")
        else:
            attempt("restore window style", lambda: win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, saved_style))
        attempt("restore extended style", lambda: win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, int(state["ex_style"])))
        attempt(
            "refresh restored styles",
            lambda: win32gui.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED,
            ),
        )
        attempt("restore title", lambda: win32gui.SetWindowText(hwnd, state.get("title") or "Roblox"))
        if state.get("placement") is not None:
            attempt("restore placement", lambda: win32gui.SetWindowPlacement(hwnd, state["placement"]))
        elif state.get("rect") is not None:
            left, top, right, bottom = state["rect"]
            attempt("restore rectangle", lambda: win32gui.SetWindowPos(
                hwnd, 0, left, top, right - left, bottom - top,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED,
            ))

        # Restore visibility last so earlier failures do not skip the show attempt.
        if state.get("visible", True):
            command = win32con.SW_SHOWMINNOACTIVE if state.get("was_iconic") else win32con.SW_SHOW
            attempt("restore visibility", lambda: win32gui.ShowWindow(hwnd, command))
        else:
            attempt("restore hidden state", lambda: win32gui.ShowWindow(hwnd, win32con.SW_HIDE))

        if win32gui.IsWindow(hwnd):
            owner_now = int(win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4)) or 0)
            if owner_now != original_owner:
                errors.append(f"Final owner check failed (expected {original_owner}, got {owner_now}).")
            if state.get("visible", True) and not win32gui.IsWindowVisible(hwnd):
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                except Exception as exc:
                    errors.append(f"Last-chance show failed: {exc}")
                if not win32gui.IsWindowVisible(hwnd):
                    errors.append("Roblox is still hidden after the final show attempt.")
        if errors:
            raise RuntimeError(" | ".join(errors))

    def undock_roblox(self) -> bool:
        if self.docked_hwnd is None:
            return True
        hwnd = self.docked_hwnd
        try:
            if win32gui and win32gui.IsWindow(hwnd):
                if self.original_window_state is None:
                    raise RuntimeError("Original Roblox window state is missing.")
                self._restore_roblox_window(hwnd, self.original_window_state)
            self.docked_hwnd = None
            self.original_window_state = None
            self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
            self.dock_button.configure(text="DOCK ROBLOX", bg=ACCENT, fg="#101522")
            self.set_status("Roblox undocked and original window settings restored.", "success")
            self.append_log("Roblox is back as a separate window; saved styles, owner, placement, title, and visibility were restored.")
            return True
        except Exception as exc:
            self.set_status(f"Could not safely undock Roblox: {exc}", "error")
            self.append_log(f"Undocking failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Exit safety
    # ------------------------------------------------------------------
    def close_app(self) -> None:
        if self._recording_active and self._high_angle_running:
            self._recording_stop_after_high_angle = True
            self.record_button.configure(text="STOP AFTER HIGH ANGLE", bg=DANGER, fg="#FFFFFF")
        elif self._recording_active or self._recording_pending:
            self.stop_recording()
        if self._playback_active or self._high_angle_running:
            self._playback_stop.set()
            self._high_angle_cancel.set()
            self.set_status("Stopping active actions. Press Close again after they stop.", "warning")
            self.append_log("Close requested during automation; safe stop requested before exit.")
            return
        self._input_capture.stop()
        self._unregister_raw_mouse_input()
        hwnd = self.docked_hwnd
        if hwnd is not None and not self.undock_roblox():
            messagebox.showerror(
                "Roblox could not be restored",
                "The Control Center will stay open because Roblox could not be safely detached. "
                "Use UNDOCK ROBLOX and try closing again.",
                parent=self.root,
            )
            return
        if hwnd is not None and PYWIN32_AVAILABLE and win32gui.IsWindow(hwnd):
            owner = int(win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4)) or 0)
            style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE))
            if owner == int(self.root.winfo_id()) or (style & 0x40000000):
                messagebox.showerror(
                    "Roblox is still attached",
                    "The Control Center will stay open because Roblox is still associated with it.",
                    parent=self.root,
                )
                return
        self.root.destroy()


def main() -> None:
    relaunch_without_console()
    configure_dpi_awareness()
    root = tk.Tk()
    BTDControlCenter(root)
    success_marker = os.environ.pop("BTD_CC_UPDATE_SUCCESS_FILE", "")
    if success_marker:
        def confirm_startup() -> None:
            try:
                Path(success_marker).write_text(APP_VERSION, encoding="ascii")
            except OSError:
                pass
        # Wait until Tk has processed its first layout/update callbacks.
        root.after(1200, confirm_startup)
    root.mainloop()


if __name__ == "__main__":
    main()