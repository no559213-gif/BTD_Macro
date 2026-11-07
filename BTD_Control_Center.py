"""BTD Control Center 1.0.0 - standalone one-tab Roblox companion GUI.

This application is independent of BTD_Macro_1.0.48.py. It recreates the
window layout, Roblox docking, Global Status, and Activity Log as a separate
program. The four feature buttons are UI entry points; game-specific feature
engines are not yet connected in this initial standalone build.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import textwrap
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk


APP_NAME = "BTD Control Center"
APP_VERSION = "1.0.0"
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

    # ------------------------------------------------------------------
    # Feature entry points. The UI is real; engines need separate game logic.
    # ------------------------------------------------------------------
    def _feature_not_connected(self, feature: str) -> None:
        message = f"{feature} is not connected yet in this standalone build."
        self.set_status(message, "warning")
        self.append_log(message)

    def on_recording(self) -> None:
        self._feature_not_connected("Recording")

    def on_playback(self) -> None:
        self._feature_not_connected("Playback")

    def on_high_angle(self) -> None:
        self._feature_not_connected("High Angle")

    def on_auto_position(self) -> None:
        self._feature_not_connected("Auto Position")

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
    root.mainloop()


if __name__ == "__main__":
    main()
