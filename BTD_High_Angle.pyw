"""Standalone BTD High Angle mini-GUI.

Windows-only single-purpose mini-GUI with one High Angle button, an optional
Always on Top setting, and a global F9 hotkey. It does not import or update
any other BTD application.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

APP_NAME = "BTD High Angle"
HOTKEY_ID = 1
VK_F9 = 0x78
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP_CHANGE_HOTKEY = 0x8001
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
HOTKEY_MODIFIER_MASK = MOD_ALT | MOD_CONTROL | MOD_SHIFT

if os.name != "nt":
    raise SystemExit("BTD High Angle is a Windows-only application.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# DPI-aware coordinates keep the 4 cm edge inset accurate at Windows scaling
# other than 100% (including 125% scaling).
try:
    _set_dpi = user32.SetProcessDpiAwarenessContext
    _set_dpi.argtypes = [ctypes.c_void_p]
    _set_dpi.restype = wintypes.BOOL
    _set_dpi(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _configure_winapi() -> None:
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SendInput.restype = wintypes.UINT
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.restype = wintypes.HWND
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user32.AllowSetForegroundWindow.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


_configure_winapi()


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid)) or not pid.value:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value).casefold()
    finally:
        kernel32.CloseHandle(handle)


def find_roblox_window() -> int:
    """Find the visible Roblox Player client, excluding browsers and Studio."""
    candidates: list[tuple[int, int]] = []

    ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd_value, _):
        hwnd = int(hwnd_value or 0)
        try:
            if not hwnd or not user32.IsWindowVisible(wintypes.HWND(hwnd)):
                return True
            if user32.GetWindow(wintypes.HWND(hwnd), 4):  # GW_OWNER
                return True
            if _process_name(hwnd) not in {"robloxplayerbeta.exe", "robloxplayer.exe"}:
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return True
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 200 or height < 200:
                return True
            candidates.append((width * height, hwnd))
        except Exception:
            pass
        return True

    callback = ENUMPROC(visit)
    user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    if not candidates:
        raise RuntimeError("Roblox Player was not found. Open the game first.")
    candidates.sort(reverse=True)
    return candidates[0][1]


def ensure_roblox_focus(hwnd: int, timeout: float = 2.5) -> bool:
    """Focus Roblox deliberately before sending any mouse input."""
    target = wintypes.HWND(hwnd)
    if not user32.IsWindow(target):
        return False
    if user32.IsIconic(target):
        user32.ShowWindow(target, 9)  # SW_RESTORE
    if int(user32.GetForegroundWindow() or 0) == hwnd:
        return True

    foreground = int(user32.GetForegroundWindow() or 0)
    foreground_tid = 0
    if foreground:
        foreground_tid = int(user32.GetWindowThreadProcessId(wintypes.HWND(foreground), None))
    target_tid = int(user32.GetWindowThreadProcessId(target, None))
    current_tid = int(kernel32.GetCurrentThreadId())
    attached_foreground = False
    attached_target = False
    try:
        if foreground_tid and foreground_tid != current_tid:
            attached_foreground = bool(user32.AttachThreadInput(current_tid, foreground_tid, True))
        if target_tid and target_tid != current_tid:
            attached_target = bool(user32.AttachThreadInput(current_tid, target_tid, True))
        user32.AllowSetForegroundWindow(0xFFFFFFFF)
        user32.BringWindowToTop(target)
        user32.SetForegroundWindow(target)
        user32.SetActiveWindow(target)
        user32.SetFocus(target)
    finally:
        if attached_target:
            user32.AttachThreadInput(current_tid, target_tid, False)
        if attached_foreground:
            user32.AttachThreadInput(current_tid, foreground_tid, False)

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return True
        time.sleep(0.025)
    return int(user32.GetForegroundWindow() or 0) == hwnd


def client_geometry(hwnd: int) -> tuple[int, int, int, int, int]:
    if not user32.IsWindow(wintypes.HWND(hwnd)):
        raise RuntimeError("Roblox window is no longer available.")
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
    if width < 100 or height < 100:
        raise RuntimeError("Roblox client area is too small.")
    point = wintypes.POINT(int(rect.left), int(rect.top))
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())

    dpi = 96
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if get_dpi:
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT
        try:
            dpi = int(get_dpi(wintypes.HWND(hwnd))) or 96
        except Exception:
            dpi = 96
    if dpi < 48 or dpi > 960:
        dpi = 96
    return int(point.x), int(point.y), width, height, dpi


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


def send_mouse(dx: int = 0, dy: int = 0, flags: int = 0, data: int = 0) -> None:
    packet = INPUT(
        type=0,
        mi=MOUSEINPUT(int(dx), int(dy), int(data) & 0xFFFFFFFF, int(flags), 0, 0),
    )
    if user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


class HighAngleApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg="#11141B")
        self.root.resizable(False, False)
        self.root.geometry("250x148")
        self.root.minsize(250, 148)
        self.root.maxsize(250, 148)

        self.settings_path = self._get_settings_path()
        self.settings = self._load_settings()
        self.topmost_var = tk.BooleanVar(value=bool(self.settings.get("always_on_top", False)))
        self.hotkey_vk = self._setting_int("hotkey_vk", VK_F9, 1, 0xFF)
        self.hotkey_modifiers = self._setting_int("hotkey_modifiers", 0, 0, HOTKEY_MODIFIER_MASK)
        self.hotkey_label = str(self.settings.get("hotkey_label") or "F9")

        self.cancel = threading.Event()
        self.running = False
        self.state_lock = threading.Lock()
        self.hotkey_events: queue.Queue[str] = queue.Queue()
        self.hotkey_commands: queue.Queue[tuple[int, int, str]] = queue.Queue()
        self.hotkey_updates: queue.Queue[tuple[str, str, str, int, int]] = queue.Queue()
        self.hotkey_thread_id = 0
        self.hotkey_ready = threading.Event()
        self.hotkey_error = ""
        self._capturing_hotkey = False
        self._capture_binding = None
        self._ignore_hotkey_until = 0.0

        self.button = tk.Button(
            self.root,
            text="HIGH ANGLE",
            command=self.activate,
            bg="#8297FF",
            fg="#101522",
            activebackground="#7086F4",
            activeforeground="#101522",
            relief="flat",
            bd=0,
            font=("Segoe UI Semibold", 12),
            cursor="hand2",
            takefocus=True,
        )
        self.button.pack(fill="both", expand=True, padx=9, pady=(8, 2))

        self.topmost_check = tk.Checkbutton(
            self.root,
            text="Always on top",
            variable=self.topmost_var,
            command=self._on_topmost_changed,
            bg="#11141B",
            fg="#E8ECF4",
            activebackground="#11141B",
            activeforeground="#FFFFFF",
            selectcolor="#202735",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 9),
            cursor="hand2",
            anchor="w",
        )
        self.topmost_check.pack(fill="x", padx=11, pady=(0, 1))
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

        self.keybind_button = tk.Button(
            self.root,
            text=self._keybind_button_text(),
            command=self._begin_keybind_capture,
            bg="#202735",
            fg="#E8ECF4",
            activebackground="#30394A",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            font=("Segoe UI", 9),
            cursor="hand2",
            padx=8,
            pady=4,
        )
        self.keybind_button.pack(fill="x", padx=10, pady=(0, 8))

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(30, self._poll_hotkey_queue)
        self.hotkey_thread = threading.Thread(
            target=self._hotkey_loop, name="BTDHighAngleHotkey", daemon=True
        )
        self.hotkey_thread.start()
        self.root.after(1200, self._check_hotkey_startup)

    @staticmethod
    def _get_settings_path() -> Path:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "BTD_High_Angle" / "settings.json"

    def _load_settings(self) -> dict:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def _setting_int(self, name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self.settings.get(name, default))
            return value if minimum <= value <= maximum else default
        except (TypeError, ValueError):
            return default

    def _save_settings(self) -> None:
        self.settings = {
            "always_on_top": bool(self.topmost_var.get()),
            "hotkey_vk": int(self.hotkey_vk),
            "hotkey_modifiers": int(self.hotkey_modifiers),
            "hotkey_label": self.hotkey_label,
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def _keybind_button_text(self) -> str:
        return f"Hotkey: {self.hotkey_label}   •   CHANGE"

    def _on_topmost_changed(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost_var.get()))
        try:
            self._save_settings()
        except OSError as exc:
            from tkinter import messagebox
            messagebox.showwarning(
                APP_NAME,
                f"The topmost setting changed, but could not be saved:\\n{exc}",
                parent=self.root,
            )

    def _begin_keybind_capture(self) -> None:
        if self.running:
            return
        if self._capturing_hotkey:
            self._end_keybind_capture()
            self.keybind_button.configure(text=self._keybind_button_text())
            return
        self._capturing_hotkey = True
        self.keybind_button.configure(text="PRESS A KEY  •  ESC CANCEL")
        self._capture_binding = self.root.bind("<KeyPress>", self._on_keybind_key, add="+")
        self.root.focus_force()
        self.keybind_button.focus_set()

    def _end_keybind_capture(self) -> None:
        self._capturing_hotkey = False
        if self._capture_binding:
            try:
                self.root.unbind("<KeyPress>", self._capture_binding)
            except tk.TclError:
                pass
            self._capture_binding = None

    @staticmethod
    def _keysym_to_vk(keysym: str) -> int | None:
        key = str(keysym)
        lower = key.lower()
        if len(key) == 1:
            if "a" <= lower <= "z" or "0" <= key <= "9":
                return ord(key.upper())
        if key.startswith("F") and key[1:].isdigit():
            number = int(key[1:])
            if 1 <= number <= 24:
                return 0x70 + number - 1
        named = {
            "BackSpace": 0x08, "Tab": 0x09, "Return": 0x0D, "KP_Enter": 0x0D,
            "Pause": 0x13, "Caps_Lock": 0x14, "Escape": 0x1B, "space": 0x20,
            "Prior": 0x21, "Next": 0x22, "End": 0x23, "Home": 0x24,
            "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
            "Print": 0x2C, "Insert": 0x2D, "Delete": 0x2E,
            "Num_Lock": 0x90, "Scroll_Lock": 0x91,
            "KP_0": 0x60, "KP_1": 0x61, "KP_2": 0x62, "KP_3": 0x63,
            "KP_4": 0x64, "KP_5": 0x65, "KP_6": 0x66, "KP_7": 0x67,
            "KP_8": 0x68, "KP_9": 0x69, "KP_Multiply": 0x6A,
            "KP_Add": 0x6B, "KP_Subtract": 0x6D, "KP_Decimal": 0x6E,
            "KP_Divide": 0x6F,
        }
        return named.get(key)

    @staticmethod
    def _format_hotkey_label(key: str, modifiers: int) -> str:
        parts = []
        if modifiers & MOD_CONTROL:
            parts.append("Ctrl")
        if modifiers & MOD_ALT:
            parts.append("Alt")
        if modifiers & MOD_SHIFT:
            parts.append("Shift")
        parts.append(key)
        return "+".join(parts)

    def _on_keybind_key(self, event) -> str:
        keysym = str(getattr(event, "keysym", ""))
        if keysym in {"Escape", "Esc"}:
            self._end_keybind_capture()
            self.keybind_button.configure(text=self._keybind_button_text())
            return "break"

        vk = self._keysym_to_vk(keysym)
        if vk is None:
            return "break"

        state = int(getattr(event, "state", 0))
        modifiers = 0
        if state & 0x0004:
            modifiers |= MOD_CONTROL
        if state & 0x0008:
            modifiers |= MOD_ALT
        if state & 0x0001:
            modifiers |= MOD_SHIFT

        label = self._format_hotkey_label(
            keysym.upper() if len(keysym) == 1 else keysym, modifiers
        )
        self._end_keybind_capture()
        self.keybind_button.configure(text="SETTING HOTKEY...")
        self._ignore_hotkey_until = time.monotonic() + 0.35
        try:
            while True:
                self.hotkey_events.get_nowait()
        except queue.Empty:
            pass

        self.hotkey_commands.put((vk, modifiers, label))
        posted = bool(
            self.hotkey_thread_id
            and user32.PostThreadMessageW(
                self.hotkey_thread_id, WM_APP_CHANGE_HOTKEY, 0, 0
            )
        )
        if not posted:
            try:
                self.hotkey_commands.get_nowait()
            except queue.Empty:
                pass
            self.keybind_button.configure(text=self._keybind_button_text())
            from tkinter import messagebox
            messagebox.showerror(
                APP_NAME, "Could not update the global hotkey. Please try again.",
                parent=self.root,
            )
        return "break"

    def _hotkey_loop(self) -> None:
        self.hotkey_thread_id = int(kernel32.GetCurrentThreadId())
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

        active_vk = int(self.hotkey_vk)
        active_modifiers = int(self.hotkey_modifiers)
        active_label = self.hotkey_label
        registered = bool(user32.RegisterHotKey(
            None, HOTKEY_ID, active_modifiers | MOD_NOREPEAT, active_vk
        ))
        self.hotkey_ready.set()
        if registered:
            self.hotkey_updates.put(("ready", active_label, "", active_vk, active_modifiers))
        else:
            error_code = ctypes.get_last_error()
            self.hotkey_updates.put((
                "failed", active_label,
                f"Could not register {active_label} globally (Windows error {error_code}).",
                active_vk, active_modifiers
            ))

        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                message_id = int(message.message)
                if message_id == WM_HOTKEY and int(message.wParam) == HOTKEY_ID:
                    self.hotkey_events.put("trigger")
                elif message_id == WM_APP_CHANGE_HOTKEY:
                    try:
                        new_vk, new_modifiers, new_label = self.hotkey_commands.get_nowait()
                    except queue.Empty:
                        continue

                    old_vk, old_modifiers, old_label = active_vk, active_modifiers, active_label
                    if registered:
                        user32.UnregisterHotKey(None, HOTKEY_ID)
                        registered = False

                    registered = bool(user32.RegisterHotKey(
                        None, HOTKEY_ID, new_modifiers | MOD_NOREPEAT, new_vk
                    ))
                    if registered:
                        active_vk, active_modifiers, active_label = (
                            new_vk, new_modifiers, new_label
                        )
                        self.hotkey_updates.put((
                            "changed", active_label, "", active_vk, active_modifiers
                        ))
                    else:
                        error_code = ctypes.get_last_error()
                        if old_vk and old_label:
                            registered = bool(user32.RegisterHotKey(
                                None, HOTKEY_ID, old_modifiers | MOD_NOREPEAT, old_vk
                            ))
                        active_vk, active_modifiers, active_label = old_vk, old_modifiers, old_label
                        self.hotkey_updates.put((
                            "failed", old_label,
                            f"That shortcut is unavailable (Windows error {error_code}). "
                            "Your previous hotkey was kept.",
                            old_vk, old_modifiers
                        ))
        finally:
            if registered:
                user32.UnregisterHotKey(None, HOTKEY_ID)

    def _check_hotkey_startup(self) -> None:
        if self.hotkey_error:
            from tkinter import messagebox
            messagebox.showwarning(APP_NAME, self.hotkey_error, parent=self.root)

    def _poll_hotkey_queue(self) -> None:
        try:
            while True:
                self.hotkey_events.get_nowait()
                if not self._capturing_hotkey and time.monotonic() >= self._ignore_hotkey_until:
                    self.activate()
        except queue.Empty:
            pass

        try:
            while True:
                state, label, error, vk, modifiers = self.hotkey_updates.get_nowait()
                if state in {"ready", "changed"}:
                    self.hotkey_vk = int(vk)
                    self.hotkey_modifiers = int(modifiers)
                    self.hotkey_label = label
                    self.keybind_button.configure(text=self._keybind_button_text())
                    try:
                        self._save_settings()
                    except OSError as exc:
                        from tkinter import messagebox
                        messagebox.showwarning(
                            APP_NAME, f"Could not save hotkey settings:\\n{exc}",
                            parent=self.root,
                        )
                elif state == "failed":
                    self.hotkey_error = error
                    self.keybind_button.configure(text=self._keybind_button_text())
                    from tkinter import messagebox
                    messagebox.showwarning(APP_NAME, error, parent=self.root)
        except queue.Empty:
            pass

        if self.root.winfo_exists():
            self.root.after(30, self._poll_hotkey_queue)

    def activate(self) -> None:
        with self.state_lock:
            if self.running:
                self.cancel.set()
                self.button.configure(text="STOPPING...")
                return
            self.running = True
            self.cancel.clear()
            self.button.configure(text="RUNNING...")
        threading.Thread(target=self._worker, name="BTDHighAngle", daemon=True).start()

    def _worker(self) -> None:
        error = ""
        succeeded = False
        right_down = False
        hwnd = 0
        try:
            hwnd = find_roblox_window()
            if not ensure_roblox_focus(hwnd):
                raise RuntimeError("Could not bring Roblox Player into focus.")
            left, top, width, height, dpi = client_geometry(hwnd)
            retreat_px = max(1, int(round((4.0 / 2.54) * dpi)))
            safe_right_x = left + width - 1
            target_x = safe_right_x - retreat_px
            center_x = left + width // 2
            target_y = top + height // 2
            if width <= retreat_px + 1:
                raise RuntimeError("Roblox client is too narrow for the 4 cm right-edge inset.")

            def check_active() -> bool:
                if self.cancel.is_set():
                    return False
                if not user32.IsWindow(wintypes.HWND(hwnd)):
                    raise RuntimeError("Roblox closed during High Angle.")
                if int(user32.GetForegroundWindow() or 0) != hwnd:
                    raise RuntimeError("Roblox lost focus during High Angle.")
                return True

            def move_smooth(
                start_x: int, start_y: int, end_x: int, end_y: int,
                duration: float, steps: int,
            ) -> bool:
                # Smoothstep easing with a frame-like cadence. Every segment is
                # interpolated; the cursor is never teleported to a waypoint.
                steps = max(2, int(steps))
                interval = duration / steps
                for index in range(1, steps + 1):
                    if not check_active():
                        return False
                    t = index / steps
                    eased = t * t * (3.0 - 2.0 * t)
                    x = int(round(start_x + (end_x - start_x) * eased))
                    y = int(round(start_y + (end_y - start_y) * eased))
                    if not user32.SetCursorPos(x, y):
                        raise ctypes.WinError(ctypes.get_last_error())
                    time.sleep(interval)
                return True

            # Begin at the actual pointer location and glide into the game
            # center before making the rightward pass.
            cursor = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(cursor)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not move_smooth(int(cursor.x), int(cursor.y), center_x, target_y, 0.700, 48):
                return
            if not move_smooth(center_x, target_y, safe_right_x, target_y, 0.850, 54):
                return
            time.sleep(0.180)
            if not move_smooth(safe_right_x, target_y, target_x, target_y, 0.550, 36):
                return
            time.sleep(0.250)
            if not check_active():
                return

            send_mouse(flags=0x0008)  # MOUSEEVENTF_RIGHTDOWN
            right_down = True
            try:
                time.sleep(0.100)
                # Preserve the full 1,000 px downward camera drag, but
                # spread it over ~1.8 s instead of the previous ~0.4 s.
                for _ in range(100):
                    if not check_active():
                        return
                    send_mouse(dx=0, dy=10, flags=0x0001)  # MOUSEEVENTF_MOVE (relative)
                    time.sleep(0.018)
                time.sleep(0.120)
            finally:
                # Always release the injected right button, even on cancellation/error.
                if right_down:
                    if int(user32.GetForegroundWindow() or 0) != hwnd:
                        ensure_roblox_focus(hwnd, timeout=1.0)
                    send_mouse(flags=0x0010)  # MOUSEEVENTF_RIGHTUP
                    right_down = False

            # High Angle intentionally ends after the camera drag; no zoom input.
            succeeded = True
        except Exception as exc:
            error = str(exc)
        finally:
            if right_down:
                try:
                    if hwnd and int(user32.GetForegroundWindow() or 0) != hwnd:
                        ensure_roblox_focus(hwnd, timeout=1.0)
                    send_mouse(flags=0x0010)
                except Exception:
                    pass
            self.root.after(0, lambda: self._finish(succeeded, error))

    def _finish(self, succeeded: bool, error: str) -> None:
        with self.state_lock:
            self.running = False
            self.cancel.clear()
        self.button.configure(text="HIGH ANGLE")
        if error:
            from tkinter import messagebox
            messagebox.showerror(APP_NAME, error, parent=self.root)

    def close(self) -> None:
        self.cancel.set()
        if self.hotkey_thread_id:
            user32.PostThreadMessageW(self.hotkey_thread_id, WM_QUIT, 0, 0)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _relaunch_without_console() -> bool:
    """Reopen through pythonw.exe so double-clicking the .py never shows CMD."""
    try:
        current = os.path.basename(sys.executable).casefold()
        if current == "pythonw.exe":
            return False
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(pythonw):
            return False
        env = os.environ.copy()
        env["BTD_HIGH_ANGLE_NO_CONSOLE"] = "1"
        subprocess.Popen(
            [pythonw, os.path.abspath(__file__), *sys.argv[1:]],
            env=env,
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if not _relaunch_without_console():
        HighAngleApp().run()
