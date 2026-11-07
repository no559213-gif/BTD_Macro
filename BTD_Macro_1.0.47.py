"""BTD Macro 1.0.47.

Dark four-tab GUI with coordinate-driven Ball Tower Defense lobby automation,
a GitHub release updater, and a Windows Roblox child-window host. In-match
recording and playback remain separate features.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _relaunch_with_pythonw_before_gui_imports() -> None:
    """Restart console-launched Windows Python scripts with pythonw.exe."""
    if os.name != "nt":
        return

    current_executable = Path(sys.executable)
    if current_executable.name.lower() == "pythonw.exe":
        return
    if not current_executable.name.lower().startswith("python"):
        return

    pythonw_executable = current_executable.with_name("pythonw.exe")
    if not pythonw_executable.is_file():
        versioned_candidate = current_executable.with_name(
            current_executable.stem + "w.exe"
        )
        if versioned_candidate.is_file():
            pythonw_executable = versioned_candidate
        else:
            return

    try:
        launch_environment = os.environ.copy()
        launch_environment["BTD_MACRO_GUI_BOOTSTRAPPED"] = "1"

        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = 0

        subprocess.Popen(
            [
                str(pythonw_executable),
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
            cwd=os.getcwd(),
            env=launch_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            startupinfo=startup_info,
        )
    except Exception:
        # Keep the later FreeConsole fallback if this Python install has issues.
        return

    # The GUI-subsystem child is responsible for the application from this point.
    os._exit(0)


_relaunch_with_pythonw_before_gui_imports()


import ast
import ctypes
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import textwrap
import tkinter as tk
import urllib.error
import urllib.request
import zipfile
from tkinter import filedialog, messagebox, ttk

try:
    import win32api
    import win32con
    import win32gui
    PYWIN32_AVAILABLE = True
except ImportError:  # Allows the GUI to open even if pywin32 is unavailable.
    win32api = None
    win32con = None
    win32gui = None
    PYWIN32_AVAILABLE = False


APP_VERSION = "1.0.47"
APP_TITLE = "BTD Macro 1.0.47"
GITHUB_REPOSITORY = "no559213-gif/BTD_Macro"
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/"
    f"{GITHUB_REPOSITORY}/releases/latest"
)
MAX_UPDATE_SIZE = 10 * 1024 * 1024
MAP_OPTIONS = [
    "Doodle",
    "IsleOfBalls",
    "MountainsMap",
    "ForestMap",
    "TheHallows",
    "Tundra",
    "InfernoAbyss",
    "HoloPlains",
    "Golden Castle",
    "Toxic Wasteland",
    "Railroad",
    "HallowManor",
    "Skybound Isles",
    "SkyCastle",
    "Museum",
]


DIFFICULTY_OPTIONS = [
    "Easy",
    "Medium",
    "Hard",
    "Expert",
    "Endless",
    "Boss Rush",
]

# Dark palette
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

# TEST ROBLOX cursor motion and timing. These are intentionally easy to tune.
TEST_CURSOR_MOVE_DURATION_MS = 180
TEST_CURSOR_MOVE_STEPS = 12
TEST_CURSOR_ARRIVAL_DELAY_MS = 450
TEST_CURSOR_POSITION_TOLERANCE_PX = 2
TEST_SCROLL_NOTCH_INTERVAL_MS = 180
# Reserved for future multi-target automation; zero until a later cursor move exists.
POST_ACTION_CURSOR_DELAY_MS = 0
TEST_HOVER_NUDGE_PX = 2
TEST_HOVER_NUDGE_OUT_DELAY_MS = 35
TEST_HOVER_SETTLE_DELAY_MS = 120

LOBBY_ACTION_DELAY_MS = 520
MAP_PICKER_OPEN_DELAY_MS = 650
MAP_AFTER_SCROLL_DELAY_MS = 300
LOBBY_CLICK_HOLD_MS = 55
KICK_MAX_ATTEMPTS = 10

MAP_DETECTION_THRESHOLD = 0.82
MAP_DETECTION_MIN_MARGIN = 0.025
MAP_DETECTION_RESET_NOTCHES = 10
MAP_DETECTION_MAX_DOWN_NOTCHES = 9
MAP_DETECTION_SCAN_MAX_DOWN_NOTCHES = 18
MAP_DETECTION_SCAN_SCREEN_SCALE = 0.25
MAP_DETECTION_SCAN_TITLE_THRESHOLD = 0.50
MAP_DETECTION_SCAN_ART_THRESHOLD = 0.55
MAP_DETECTION_SCAN_COMBINED_THRESHOLD = 0.68
MAP_DETECTION_SCROLL_INTERVAL_MS = 80
MAP_DETECTION_SETTLE_MS = 260
MAP_DETECTION_EXPAND_SETTLE_MS = 500
GLOBAL_HOTKEY_POLL_MS = 35

# Soft status colors for the dark interface.
STATUS_PROGRESS = "#AFC0FF"
STATUS_SUCCESS = "#8DE0B4"
STATUS_WARNING = "#F2D58D"
STATUS_ERROR = "#F08E9A"
STATUS_IDLE = "#B7C0D0"


def get_gui_python_executable() -> str:
    """Prefer pythonw.exe so GUI restarts do not open a console window."""
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return sys.executable


def get_gui_python_launch_settings():
    """Return a GUI interpreter and Windows flags suppressing console windows."""
    executable = get_gui_python_executable()
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        if Path(executable).name.lower() == "pythonw.exe":
            return executable, 0, startup
        return executable, getattr(subprocess, "CREATE_NO_WINDOW", 0), startup
    return executable, 0, None


class BTDGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1320x800")
        self.root.minsize(1000, 620)
        self.root.configure(bg=BG)

        self.recordings_dir = Path(__file__).resolve().parent / "recordings"
        self.recordings_dir.mkdir(exist_ok=True)

        self.docked_hwnd: int | None = None
        self.original_window_state: dict | None = None
        self._arranging_windows = False
        self._overlay_position_pending = False
        self.recording_names: list[str] = []
        self.currently_loaded_recording: str | None = None

        self.button_positions_path = (
            Path(__file__).resolve().parent / "button_positions.json"
        )
        self.button_positions = self._load_button_positions()
        self.map_reference_dir = Path(__file__).resolve().parent / "map_references"
        self.map_reference_dir.mkdir(exist_ok=True)
        self._map_reference_bundle_installed = self._install_map_reference_bundle()
        self._position_capture_armed = False
        self._position_f3_was_down = False
        self._global_hotkey_after_id = None
        self._global_get_async_key_state = None
        self._global_f8_was_down = False
        self._roblox_scroll_test_active = False
        self._map_preview_original_rect = None
        self._map_preview_hwnd = None
        self._big_screen_mode = False
        self._big_screen_state: dict | None = None
        self._big_screen_compact_frame: tk.Frame | None = None
        self._big_screen_compact_window: tk.Toplevel | None = None
        self._map_detection_test_active = False
        self._map_detection_after_id = None
        self._map_detection_test_state = None
        self._map_detection_preview_photo = None
        self._macro_held_keys = set()
        self._lobby_sequence_active = False
        self._lobby_sequence_after_id = None
        self._lobby_sequence_mode = None
        self._lobby_sequence_hwnd = None
        self._lobby_sequence_map = None
        self._lobby_sequence_kick_count = 0
        self._lobby_mouse_down = False

        self._update_check_running = False
        self._update_download_running = False
        self._update_release: dict | None = None
        self._update_dialog: tk.Toplevel | None = None

        self._configure_styles()
        self._build_layout()
        self._refresh_map_reference_status()
        if self._map_reference_bundle_installed:
            self.append_log(
                f"Installed {self._map_reference_bundle_installed} bundled map reference image(s)."
            )
        self.refresh_recordings()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.game_host.bind("<Configure>", self._on_game_host_configure, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.bind("<Map>", self._on_root_map, add="+")
        self.root.bind("<Unmap>", self._on_root_unmap, add="+")

        self.append_log("BTD Macro 1.0.47 initialized. F3 capture, F8 universal stop, and lobby automation are ready.")
        if self.button_positions:
            self.append_log(
                f"Loaded {len(self.button_positions)} saved Roblox button position(s)."
            )
        if not PYWIN32_AVAILABLE:
            self.append_log("pywin32 was not found. Roblox embedding will be unavailable.")
        self.root.after_idle(self._maximize_and_lock_window)
        self._start_global_hotkey_polling()
        self.root.after(900, self.check_for_updates)

    def _on_root_configure(self, event=None) -> None:
        """Re-maximize if Windows restores the app out of its fixed desktop layout."""
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, "_maximizing_window", False):
            return
        try:
            if self.root.state() == "normal" and not self._big_screen_mode:
                self.root.after_idle(self._maximize_and_lock_window)
            if self.docked_hwnd is not None and not self._big_screen_mode:
                self._schedule_overlay_position()
        except tk.TclError:
            pass

    def _on_root_map(self, event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self.docked_hwnd is not None and not self._big_screen_mode:
            self._schedule_overlay_position(show=True)

    def _on_root_unmap(self, event=None) -> None:
        """Owned Roblox is minimized/restored automatically with its Macro owner."""
        if event is not None and event.widget is not self.root:
            return
        # Native owner behavior handles minimization; no timer or extra ShowWindow call.

    def _maximize_and_lock_window(self) -> None:
        """Keep the app maximized in the normal desktop work area, not fullscreen."""
        if getattr(self, "_maximizing_window", False):
            return
        self._maximizing_window = True
        try:
            self.root.attributes("-fullscreen", False)
            if os.name == "nt":
                from ctypes import wintypes

                # Maximize first so Windows uses the monitor work area and leaves
                # the taskbar visible, then remove the styles that permit resizing.
                self.root.state("zoomed")
                self.root.update_idletasks()

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                get_long = getattr(user32, "GetWindowLongPtrW", None)
                if get_long is None:
                    get_long = user32.GetWindowLongW
                set_long = getattr(user32, "SetWindowLongPtrW", None)
                if set_long is None:
                    set_long = user32.SetWindowLongW

                get_long.argtypes = [wintypes.HWND, ctypes.c_int]
                get_long.restype = ctypes.c_ssize_t
                set_long.argtypes = [
                    wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t
                ]
                set_long.restype = ctypes.c_ssize_t

                hwnd = wintypes.HWND(self.root.winfo_id())
                GWL_STYLE = -16
                WS_THICKFRAME = 0x00040000
                WS_MAXIMIZEBOX = 0x00010000
                style = int(get_long(hwnd, GWL_STYLE))
                locked_style = style & ~(WS_THICKFRAME | WS_MAXIMIZEBOX)
                if locked_style != style:
                    ctypes.set_last_error(0)
                    set_long(hwnd, GWL_STYLE, locked_style)
                    error_code = ctypes.get_last_error()
                    if error_code:
                        raise ctypes.WinError(error_code)

                set_window_pos = user32.SetWindowPos
                set_window_pos.argtypes = [
                    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT
                ]
                set_window_pos.restype = wintypes.BOOL
                flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
                if not set_window_pos(hwnd, wintypes.HWND(0), 0, 0, 0, 0, flags):
                    raise ctypes.WinError(ctypes.get_last_error())

                show_window = user32.ShowWindow
                show_window.argtypes = [wintypes.HWND, ctypes.c_int]
                show_window.restype = wintypes.BOOL
                show_window(hwnd, 3)  # SW_SHOWMAXIMIZED
            else:
                try:
                    self.root.state("zoomed")
                except tk.TclError:
                    pass
                self.root.resizable(False, False)
        except Exception as exc:
            self.append_log(f"WARNING locking the window maximized: {exc}")
            if os.name != "nt":
                try:
                    self.root.resizable(False, False)
                except tk.TclError:
                    pass
        finally:
            self._maximizing_window = False

    # ------------------------------------------------------------------
    # Styling and layout
    # ------------------------------------------------------------------
    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("PanelAlt.TFrame", background=PANEL_ALT)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI Semibold", 18))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT,
                        font=("Segoe UI Semibold", 11))
        style.configure(
            "TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0)
        )
        style.configure(
            "TNotebook.Tab", background=PANEL_ALT, foreground=MUTED,
            padding=(18, 10), font=("Segoe UI Semibold", 10), borderwidth=0
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL)],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "TCombobox", fieldbackground=PANEL_ALT, background=PANEL_ALT,
            foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
            lightcolor=BORDER, darkcolor=BORDER, padding=8
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL_ALT)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL_ALT)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "TEntry", fieldbackground=PANEL_ALT, foreground=TEXT,
            insertcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER,
            darkcolor=BORDER, padding=8
        )
        style.configure(
            "Treeview", background=PANEL_ALT, fieldbackground=PANEL_ALT,
            foreground=TEXT, rowheight=28, borderwidth=0,
            font=("Segoe UI", 9)
        )
        style.configure(
            "Treeview.Heading", background=PANEL, foreground=MUTED,
            font=("Segoe UI Semibold", 9), relief="flat"
        )
        style.map("Treeview", background=[("selected", "#344263")])
        style.configure(
            "Vertical.TScrollbar", background=PANEL_ALT,
            troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL
        )

    def _build_layout(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        self.main_layout = outer
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=65, minsize=610, uniform="main")
        outer.grid_columnconfigure(1, weight=35, minsize=370, uniform="main")

        # Roblox remains a native top-level window and is visually aligned over this panel.
        self.game_panel = tk.Frame(
            outer, bg="#070A10", highlightthickness=1,
            highlightbackground=BORDER
        )
        self.game_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.game_host = tk.Frame(self.game_panel, bg="#05070B")
        self.game_host.pack(fill="both", expand=True)
        self.game_placeholder = tk.Label(
            self.game_host,
            text="ROBLOX NOT DOCKED\n\nOpen Roblox, then click DOCK ROBLOX\n"
                 "to place the game inside this panel.",
            bg="#05070B", fg=MUTED, justify="center",
            font=("Segoe UI Semibold", 12)
        )
        self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # All BTD Macro controls, the title, and global status stay on the right.
        self.macro_panel = tk.Frame(outer, bg=BG)
        self.macro_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.macro_panel.grid_rowconfigure(1, weight=1)
        self.macro_panel.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self.macro_panel, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(3, 8))
        header.grid_columnconfigure(0, weight=1)
        title_area = tk.Frame(header, bg=BG)
        title_area.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_area, text=f"BTD MACRO {APP_VERSION}", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 17)
        ).pack(anchor="w")
        tk.Label(
            title_area, text="Ball Tower Defense", bg=BG, fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(1, 0))

        header_buttons = tk.Frame(header, bg=BG)
        header_buttons.grid(row=0, column=1, sticky="e", padx=(4, 0))
        self.dock_button = tk.Button(
            header_buttons, text="DOCK ROBLOX", command=self.toggle_dock,
            bg=ACCENT, fg="#101522", activebackground=ACCENT_HOVER,
            activeforeground="#101522", relief="flat", bd=0,
            font=("Segoe UI Semibold", 8), padx=9, pady=9,
            cursor="hand2"
        )
        self.dock_button.pack(anchor="e", pady=(0, 5))
        self.update_button = tk.Button(
            header_buttons, text="CHECK UPDATES", command=self.check_for_updates,
            bg=PANEL_ALT, fg=TEXT, activebackground=BORDER,
            activeforeground=TEXT, relief="flat", bd=0,
            font=("Segoe UI Semibold", 8), padx=9, pady=7,
            cursor="hand2"
        )
        self.update_button.pack(anchor="e")

        self.notebook = ttk.Notebook(self.macro_panel)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.macro_tab = tk.Frame(self.notebook, bg=BG)
        self.recording_tab = tk.Frame(self.notebook, bg=BG)
        self.settings_tab = tk.Frame(self.notebook, bg=BG)
        self.debug_tab = tk.Frame(self.notebook, bg=BG)
        self.map_detection_tab = tk.Frame(self.notebook, bg=BG)

        self.notebook.add(self.macro_tab, text="  Macro  ")
        self.notebook.add(self.recording_tab, text="  Recording  ")
        self.notebook.add(self.settings_tab, text="  Settings  ")
        self.notebook.add(self.debug_tab, text="  Debug  ")
        self.notebook.add(self.map_detection_tab, text="  Map Detection  ")

        self._build_macro_tab()
        self._build_recording_tab()
        self._build_settings_tab()
        self._build_debug_tab()
        self._build_map_detection_tab()

        separator = tk.Frame(self.macro_panel, bg=BORDER, height=1)
        separator.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        status_frame = tk.Frame(self.macro_panel, bg=PANEL, height=102)
        self.status_frame = status_frame
        status_frame.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        status_frame.grid_propagate(False)
        tk.Label(
            status_frame, text="STATUS:", bg=PANEL, fg=ACCENT,
            font=("Segoe UI Semibold", 15)
        ).pack(side="left", padx=(9, 7))
        self.status_var = tk.StringVar(value="Idle — ready.")
        self.status_label = tk.Label(
            status_frame, textvariable=self.status_var, bg=PANEL,
            fg=STATUS_IDLE, anchor="w", justify="left", wraplength=220,
            font=("Segoe UI Semibold", 15)
        )
        self.status_label.pack(side="left", fill="both", expand=True, padx=(0, 8))
        status_frame.bind("<Configure>", self._resize_status_wrap, add="+")

    # ------------------------------------------------------------------
    # Macro tab
    # ------------------------------------------------------------------
    def _build_macro_tab(self) -> None:
        content = tk.Frame(self.macro_tab, bg=BG)
        content.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(
            content, text="Macro setup", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 16)
        ).pack(anchor="w")
        tk.Label(
            content, text="Choose the match settings and a saved recording.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(3, 18))

        form = tk.Frame(
            content, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        form.pack(fill="x", anchor="n")
        form.grid_columnconfigure(0, weight=1)

        self.map_var = tk.StringVar(value="Choose a Map")
        self.difficulty_var = tk.StringVar(value="Choose a Difficulty")
        self.macro_recording_var = tk.StringVar(value="Choose a Recording")

        self._add_dropdown(
            form, 0, "1. CHOOSE A MAP", self.map_var,
            ["Choose a Map"] + MAP_OPTIONS
        )
        self.map_var.trace_add("write", lambda *_args: self._refresh_map_reference_status())
        self._add_dropdown(
            form, 1, "2. CHOOSE A DIFFICULTY", self.difficulty_var,
            ["Choose a Difficulty"] + DIFFICULTY_OPTIONS
        )
        self.recording_combo = self._add_dropdown(
            form, 2, "3. CHOOSE YOUR RECORDING", self.macro_recording_var,
            ["Choose a Recording"]
        )

        # Main macro controls stay separate from diagnostic test tools.
        macro_controls = tk.Frame(content, bg=BG)
        macro_controls.pack(fill="x", pady=(18, 0))
        tk.Label(
            macro_controls, text="MACRO CONTROLS", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 9)
        ).pack(anchor="w", pady=(0, 6))
        macro_button_row = tk.Frame(macro_controls, bg=BG)
        macro_button_row.pack(fill="x")
        self.start_macro_button = self._make_button(
            macro_button_row, "START MACRO", self.start_macro,
            bg=ACCENT, fg="#101522", small=True
        )
        self.start_macro_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_macro_button = self._make_button(
            macro_button_row, "STOP ALL (F8)", self.universal_stop,
            bg="#4A2931", fg="#F4DDE0", active_bg="#62333D", small=True
        )
        self.stop_macro_button.pack(side="left", fill="x", expand=True, padx=(4, 0))

        test_tools_panel = tk.Frame(
            content, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        test_tools_panel.pack(fill="x", pady=(12, 0))
        tk.Label(
            test_tools_panel, text="TEST TOOLS", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 9)
        ).pack(anchor="w", padx=10, pady=(9, 5))
        test_tools_row = tk.Frame(test_tools_panel, bg=PANEL)
        test_tools_row.pack(fill="x", padx=10, pady=(0, 9))
        self.test_roblox_button = self._make_button(
            test_tools_row, "TEST ROBLOX", self.test_roblox_focus_and_scroll,
            bg=PANEL_ALT, fg=TEXT, active_bg=BORDER, small=True
        )
        self.test_roblox_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.test_choose_map_button = self._make_button(
            test_tools_row, "TEST CHOOSE MAP", self.test_choose_map,
            bg=PANEL_ALT, fg=TEXT, active_bg=BORDER, small=True
        )
        self.test_choose_map_button.pack(side="left", fill="x", expand=True, padx=(4, 0))

        tk.Label(
            content,
            text="Record lobby controls with F3. Re-record MAP SCROLL AREA at expanded size; import map-art crops in Map Detection.",
            bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=480,
            justify="left"
        ).pack(anchor="w", pady=(12, 0))

        position_panel = tk.Frame(
            content, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        position_panel.pack(fill="x", pady=(13, 0))

        tk.Label(
            position_panel, text="BUTTON POSITION SETUP", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 9)
        ).pack(anchor="w", padx=12, pady=(11, 2))
        tk.Label(
            position_panel,
            text="Select a lobby control, hover it in Roblox, then press F3.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
            wraplength=315, justify="left"
        ).pack(anchor="w", padx=12, pady=(0, 8))

        self.position_target_var = tk.StringVar(value="PLAY")
        self.position_target_combo = ttk.Combobox(
            position_panel,
            textvariable=self.position_target_var,
            values=self._position_target_options(),
            state="readonly",
            font=("Segoe UI", 9)
        )
        self.position_target_combo.pack(fill="x", padx=12)
        self._bind_dropdown_wheel_guard(self.position_target_combo)
        self.position_target_combo.bind(
            "<<ComboboxSelected>>", self._update_position_target_status
        )

        position_controls = tk.Frame(position_panel, bg=PANEL)
        position_controls.pack(fill="x", padx=12, pady=(9, 5))
        self.position_capture_button = self._make_button(
            position_controls, "ARM F3 CAPTURE",
            self.toggle_position_capture,
            bg=ACCENT, fg="#101522"
        )
        self.position_capture_button.pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self.position_status_var = tk.StringVar(value="")
        tk.Label(
            position_panel,
            textvariable=self.position_status_var,
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
            wraplength=315, justify="left"
        ).pack(anchor="w", padx=12, pady=(2, 10))
        self._update_position_target_status()

    def _bind_dropdown_wheel_guard(self, combo: ttk.Combobox) -> None:
        """Prevent accidental selection changes when the closed field is hovered."""
        def ignore_closed_field_wheel(_event=None):
            # The native popdown list receives its own wheel events while open.
            # Events over the closed combobox are swallowed here.
            return "break"

        combo.bind("<MouseWheel>", ignore_closed_field_wheel, add="+")
        combo.bind("<Button-4>", ignore_closed_field_wheel, add="+")
        combo.bind("<Button-5>", ignore_closed_field_wheel, add="+")

    def _add_dropdown(
        self, parent: tk.Widget, row: int, label: str,
        variable: tk.StringVar, values: list[str]
    ) -> ttk.Combobox:
        field = tk.Frame(parent, bg=PANEL)
        field.grid(row=row, column=0, sticky="ew", padx=18, pady=(16, 0))
        tk.Label(
            field, text=label, bg=PANEL, fg=MUTED,
            font=("Segoe UI Semibold", 8)
        ).pack(anchor="w", pady=(0, 7))
        combo = ttk.Combobox(
            field, textvariable=variable, values=values,
            state="readonly", font=("Segoe UI", 10)
        )
        self._bind_dropdown_wheel_guard(combo)
        combo.pack(fill="x")
        combo.current(0)
        return combo

    # ------------------------------------------------------------------
    # Recording tab
    # ------------------------------------------------------------------
    def _build_recording_tab(self) -> None:
        content = tk.Frame(self.recording_tab, bg=BG)
        content.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(
            content, text="Recording studio", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 16)
        ).pack(anchor="w")
        tk.Label(
            content, text="Create, manage, and play back saved recordings.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(3, 16))

        body = tk.Frame(content, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        controls_panel = tk.Frame(
            body, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        controls_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        saved_panel = tk.Frame(
            body, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        saved_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        tk.Label(
            controls_panel, text="RECORDING CONTROLS", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 10)
        ).pack(anchor="w", padx=16, pady=(16, 12))
        tk.Label(
            controls_panel, text="RECORDING NAME", bg=PANEL, fg=MUTED,
            font=("Segoe UI Semibold", 8)
        ).pack(anchor="w", padx=16, pady=(0, 6))
        self.recording_name_var = tk.StringVar()
        self.recording_name_entry = ttk.Entry(
            controls_panel, textvariable=self.recording_name_var
        )
        self.recording_name_entry.pack(fill="x", padx=16)

        row1 = tk.Frame(controls_panel, bg=PANEL)
        row1.pack(fill="x", padx=16, pady=(15, 7))
        self._make_button(
            row1, "START RECORDING", self.start_recording,
            bg=ACCENT, fg="#101522"
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self._make_button(
            row1, "END RECORDING", self.end_recording,
            bg="#4A2931", fg="#F4DDE0", active_bg="#62333D"
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        row2 = tk.Frame(controls_panel, bg=PANEL)
        row2.pack(fill="x", padx=16, pady=7)
        self._make_button(row2, "SAVE RECORDING", self.save_recording).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._make_button(row2, "DELETE RECORDING", self.delete_recording,
                          bg="#4A2931", fg="#F4DDE0", active_bg="#62333D").pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        row3 = tk.Frame(controls_panel, bg=PANEL)
        row3.pack(fill="x", padx=16, pady=7)
        self._make_button(row3, "LOAD RECORDING", self.load_recording).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._make_button(row3, "PLAYBACK", self.playback_recording,
                          bg="#293B34", fg="#DDF4E8", active_bg="#355446").pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )
        self._make_button(
            controls_panel, "STOP PLAYBACK", self.stop_playback,
            bg="#4A2931", fg="#F4DDE0", active_bg="#62333D"
        ).pack(fill="x", padx=16, pady=(7, 14))

        tk.Label(
            controls_panel,
            text="Recording and playback engines are not connected yet.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=440,
            justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 14))

        saved_header = tk.Frame(saved_panel, bg=PANEL)
        saved_header.pack(fill="x", padx=15, pady=(16, 10))
        tk.Label(
            saved_header, text="SAVED RECORDINGS", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 10)
        ).pack(side="left")
        self._make_button(
            saved_header, "REFRESH", self.refresh_recordings,
            small=True, bg=PANEL_ALT, fg=TEXT
        ).pack(side="right")

        tree_frame = tk.Frame(saved_panel, bg=PANEL)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.recordings_tree = ttk.Treeview(
            tree_frame, columns=("name",), show="headings", selectmode="browse"
        )
        self.recordings_tree.heading("name", text="Recording name")
        self.recordings_tree.column("name", anchor="w", stretch=True, width=180)
        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.recordings_tree.yview
        )
        self.recordings_tree.configure(yscrollcommand=scrollbar.set)
        self.recordings_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.recordings_tree.bind("<<TreeviewSelect>>", self._on_recording_selected)

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> None:
        content = tk.Frame(self.settings_tab, bg=BG)
        content.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(
            content, text="Settings", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 16)
        ).pack(anchor="w")
        tk.Label(
            content, text="Keybinds can be configured later.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(3, 18))

        panel = tk.Frame(
            content, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        panel.pack(fill="x", anchor="n")
        tk.Label(
            panel, text="KEYBINDS", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 10)
        ).pack(anchor="w", padx=16, pady=(15, 10))

        actions = [
            "Start macro",
            "Stop macro",
            "Start recording",
            "End recording",
            "Playback recording",
            "Stop playback",
            "Dock / Undock Roblox",
        ]
        for index, action in enumerate(actions):
            line = tk.Frame(panel, bg=PANEL)
            line.pack(fill="x", padx=16, pady=6)
            tk.Label(
                line, text=action, bg=PANEL, fg=TEXT,
                font=("Segoe UI", 9)
            ).pack(side="left")
            tk.Label(
                line, text="Not set", bg=PANEL_ALT, fg=MUTED,
                font=("Segoe UI", 8), padx=12, pady=5
            ).pack(side="right")
            if index < len(actions) - 1:
                tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(3, 0))
        tk.Label(
            panel, text="No keys are assigned by default.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8)
        ).pack(anchor="w", padx=16, pady=(12, 16))

    # ------------------------------------------------------------------
    # Debug tab
    # ------------------------------------------------------------------
    def _build_map_detection_tab(self) -> None:
        """Build the screen-size control; map detection remains intentionally absent."""
        content = tk.Frame(self.map_detection_tab, bg=BG)
        content.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(
            content, text="SCREEN SIZE", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 15)
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            content,
            text=(
                "Undock Roblox, move it into a larger window that keeps its "
                "title bar and Windows taskbar visible, then dock it beside "
                "a small BTD Macro control."
            ),
            bg=BG, fg=MUTED, font=("Segoe UI", 9),
            justify="left", wraplength=560
        ).pack(anchor="w", pady=(0, 14))

        self.map_detection_size_var = tk.StringVar(
            value="The larger layout uses the monitor work area and current DPI."
        )
        self.map_detection_result_var = tk.StringVar(
            value="Ready. Open Roblox and dock it normally before switching layouts."
        )

        self.map_expand_button = self._make_button(
            content, "CHANGE TO A BIGGER SCREEN",
            self.enter_big_screen_mode,
            bg=ACCENT, fg="#101522", active_bg=ACCENT_HOVER
        )
        self.map_expand_button.pack(anchor="w", fill="x", pady=(0, 10))

        tk.Label(
            content, textvariable=self.map_detection_result_var,
            bg=BG, fg=MUTED, font=("Segoe UI", 9),
            justify="left", wraplength=560
        ).pack(anchor="w")
    @staticmethod
    def _map_reference_slug(map_name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", map_name.casefold()).strip("_")

    def _map_reference_path(self, map_name: str) -> Path | None:
        slug = self._map_reference_slug(map_name)
        for extension in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            candidate = self.map_reference_dir / (slug + extension)
            if candidate.is_file():
                return candidate
        return None

    def _install_map_reference_bundle(self) -> int:
        """Install bundled map crops beside the script without overwriting user imports."""
        bundle_path = (
            Path(__file__).resolve().parent / "BTD_Macro_1.0.32_map_references.zip"
        )
        if not bundle_path.is_file():
            return 0

        installed = 0
        try:
            with zipfile.ZipFile(bundle_path, "r") as archive:
                available_members = set(archive.namelist())
                for map_name in MAP_OPTIONS:
                    if self._map_reference_path(map_name) is not None:
                        continue
                    member = self._map_reference_slug(map_name) + ".jpg"
                    if member not in available_members:
                        continue
                    info = archive.getinfo(member)
                    if info.file_size <= 0 or info.file_size > 2 * 1024 * 1024:
                        continue
                    payload = archive.read(member)
                    if not payload.startswith(b"\xff\xd8"):
                        continue
                    destination = self.map_reference_dir / member
                    temporary = destination.with_suffix(".jpg.tmp")
                    temporary.write_bytes(payload)
                    os.replace(temporary, destination)
                    installed += 1
        except Exception as exc:
            self._map_reference_bundle_error = str(exc)
        return installed

    def _refresh_map_reference_status(self) -> None:
        if not hasattr(self, "map_detection_reference_var"):
            return
        ready = sum(
            1 for map_name in MAP_OPTIONS
            if self._map_reference_path(map_name) is not None
        )
        self.map_detection_reference_var.set(
            f"Map references ready: {ready}/15. Map names and artwork are used; all tags and event borders are ignored."
        )
        if hasattr(self, "map_detection_count_var"):
            detected = sum(
                1 for map_name in MAP_OPTIONS
                if self.map_detection_tree.exists(self._map_reference_slug(map_name))
                and self.map_detection_tree.set(
                    self._map_reference_slug(map_name), "status"
                ) == "Detected"
            )
            self.map_detection_count_var.set(f"{detected} / 15 detected")

    def import_selected_map_reference(self) -> None:
        map_name = self.map_var.get().strip()
        if map_name not in MAP_OPTIONS:
            self.map_detection_result_var.set("Choose a map in the Macro tab before importing its reference.")
            return
        selected_file = filedialog.askopenfilename(
            parent=self.root,
            title=f"Choose a cropped {map_name} card image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("All files", "*.*")
            ]
        )
        if not selected_file:
            return
        source_path = Path(selected_file)
        extension = source_path.suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            messagebox.showerror(
                "Unsupported image", "Choose a PNG, JPG, JPEG, BMP, or WEBP image.",
                parent=self.root
            )
            return
        destination = self.map_reference_dir / (
            self._map_reference_slug(map_name) + extension
        )
        try:
            shutil.copy2(source_path, destination)
            for other in self.map_reference_dir.glob(self._map_reference_slug(map_name) + ".*"):
                if other != destination:
                    try:
                        other.unlink()
                    except OSError:
                        pass
            self._refresh_map_reference_status()
            self.map_detection_result_var.set(
                f"Imported reference for {map_name}. Press TEST MAP DETECTION to check it."
            )
            self.append_log(f"Imported map reference for {map_name}: {destination.name}.")
        except OSError as exc:
            self.map_detection_result_var.set(f"Could not import reference: {exc}")
            self.append_log(f"ERROR importing map reference for {map_name}: {exc}")

    def open_map_reference_folder(self) -> None:
        try:
            self.map_reference_dir.mkdir(exist_ok=True)
            if os.name == "nt":
                os.startfile(str(self.map_reference_dir))
            else:
                raise OSError("The reference-folder shortcut is supported on Windows.")
        except Exception as exc:
            self.map_detection_result_var.set(f"Could not open the reference folder: {exc}")

    def _require_map_capture_libraries(self):
        try:
            from PIL import Image, ImageDraw, ImageGrab, ImageTk
        except ImportError as exc:
            raise RuntimeError(
                "Roblox screenshot preview needs Pillow. Install with: "
                "python -m pip install pillow"
            ) from exc
        return Image, ImageDraw, ImageGrab, ImageTk

    def _require_map_detection_libraries(self):
        Image, ImageDraw, ImageGrab, ImageTk = self._require_map_capture_libraries()
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(
                "Map matching needs OpenCV. Install with: "
                "python -m pip install opencv-python"
            ) from exc
        return Image, ImageDraw, ImageGrab, ImageTk, cv2, np

    def _get_roblox_hwnd_for_detection(self) -> int:
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            raise RuntimeError("Map preview/detection requires Windows and pywin32.")
        if self.docked_hwnd is not None and win32gui.IsWindow(self.docked_hwnd):
            return int(self.docked_hwnd)
        hwnd = self._find_roblox_window()
        if hwnd is None or not win32gui.IsWindow(hwnd):
            raise RuntimeError("Roblox window not found. Open Roblox first.")
        return int(hwnd)

    def _get_roblox_client_metrics(self, hwnd: int) -> dict:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
        client_rect = win32gui.GetClientRect(hwnd)
        client_width = int(client_rect[2] - client_rect[0])
        client_height = int(client_rect[3] - client_rect[1])
        monitor = win32api.MonitorFromWindow(
            hwnd, getattr(win32con, "MONITOR_DEFAULTTONEAREST", 2)
        )
        monitor_info = win32api.GetMonitorInfo(monitor)
        work_left, work_top, work_right, work_bottom = monitor_info["Work"]
        return {
            "window_rect": (left, top, right, bottom),
            "client_origin": (client_left, client_top),
            "client_width": client_width,
            "client_height": client_height,
            "window_width": int(right - left),
            "window_height": int(bottom - top),
            "work_area": (work_left, work_top, work_right, work_bottom),
            "work_width": int(work_right - work_left),
            "work_height": int(work_bottom - work_top)
        }

    def _map_detection_scroll_point(self, hwnd: int) -> tuple[int, int]:
        """Require MAP SCROLL AREA to be captured in the expanded client layout."""
        record = self.button_positions.get("MAP SCROLL AREA")
        if not isinstance(record, dict):
            raise RuntimeError(
                "MAP SCROLL AREA is missing. Select it in Macro, arm F3, expand Roblox, "
                "hover the open map list, and press F3."
            )
        metrics = self._get_roblox_client_metrics(hwnd)
        current_width = metrics["client_width"]
        current_height = metrics["client_height"]
        saved_width = int(record.get("client_width", 0))
        saved_height = int(record.get("client_height", 0))
        tolerance_x = max(24, int(current_width * 0.03))
        tolerance_y = max(24, int(current_height * 0.03))
        if (
            saved_width <= 0 or saved_height <= 0
            or abs(saved_width - current_width) > tolerance_x
            or abs(saved_height - current_height) > tolerance_y
        ):
            raise RuntimeError(
                "MAP SCROLL AREA was not captured at the current expanded Roblox size. "
                f"Saved client={saved_width}x{saved_height}; current client="
                f"{current_width}x{current_height}. Re-record MAP SCROLL AREA with F3 "
                "while Roblox is expanded and the map selector is open."
            )
        return self._lobby_screen_point(hwnd, "MAP SCROLL AREA")

    def _validate_map_detection_scroll_record(self, hwnd: int) -> None:
        """Check the saved scroll point was captured at approximately expanded size."""
        record = self.button_positions.get("MAP SCROLL AREA")
        if not isinstance(record, dict):
            raise RuntimeError(
                "MAP SCROLL AREA is missing. Capture it with F3 while Roblox is expanded."
            )
        metrics = self._get_roblox_client_metrics(hwnd)
        saved_width = int(record.get("client_width", 0))
        saved_height = int(record.get("client_height", 0))
        expected_width = int(metrics["work_width"])
        expected_height = int(metrics["work_height"])
        # Allow window borders/title-bar differences while rejecting old docked-size points.
        tolerance_x = max(48, int(expected_width * 0.12))
        tolerance_y = max(48, int(expected_height * 0.12))
        if (
            saved_width <= 0 or saved_height <= 0
            or abs(saved_width - expected_width) > tolerance_x
            or abs(saved_height - expected_height) > tolerance_y
        ):
            raise RuntimeError(
                "MAP SCROLL AREA was not captured at expanded Roblox size. "
                f"Saved client={saved_width}x{saved_height}; monitor work area="
                f"{expected_width}x{expected_height}. Open the map selector, arm F3, "
                "expand Roblox in Map Detection, hover the map list, and press F3. "
                "The lobby sequence was not started."
            )

    def _update_map_detection_size(self, metrics: dict) -> None:
        self.map_detection_size_var.set(
            f"Roblox client: {metrics['client_width']} × {metrics['client_height']} px\n"
            f"Window: {metrics['window_width']} × {metrics['window_height']} px\n"
            f"Desktop work area: {metrics['work_width']} × {metrics['work_height']} px"
        )

    def _expand_roblox_for_map_detection(self, hwnd: int) -> None:
        """Temporarily enlarge owned Roblox to the monitor work area, not fullscreen."""
        if self._map_preview_original_rect is not None:
            if self._map_preview_hwnd != hwnd:
                raise RuntimeError("A different Roblox window is already in preview mode.")
            return
        metrics = self._get_roblox_client_metrics(hwnd)
        original_rect = metrics["window_rect"]
        work_left, work_top, work_right, work_bottom = metrics["work_area"]
        self._map_preview_original_rect = tuple(int(value) for value in original_rect)
        self._map_preview_hwnd = int(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(
            hwnd, 0, work_left, work_top,
            work_right - work_left, work_bottom - work_top,
            getattr(win32con, "SWP_SHOWWINDOW", 0x0040)
        )
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        self.append_log(
            f"Temporarily expanded Roblox in windowed mode to "
            f"{work_right - work_left}x{work_bottom - work_top}; saved original rectangle."
        )

    def _restore_roblox_preview_window(self) -> None:
        """Restore the exact Roblox rectangle saved before a map preview or scan."""
        rect = self._map_preview_original_rect
        hwnd = self._map_preview_hwnd
        self._map_preview_original_rect = None
        self._map_preview_hwnd = None
        if rect is None or hwnd is None:
            return
        try:
            if win32gui.IsWindow(hwnd):
                left, top, right, bottom = rect
                win32gui.SetWindowPos(
                    hwnd, 0, left, top, right - left, bottom - top,
                    getattr(win32con, "SWP_SHOWWINDOW", 0x0040)
                )
                win32gui.BringWindowToTop(hwnd)
                self._schedule_overlay_position(show=True)
                self.append_log(
                    f"Restored Roblox to its pre-detection rectangle "
                    f"({left}, {top}, {right - left}, {bottom - top})."
                )
        except Exception as exc:
            self.append_log(f"WARNING restoring Roblox after map detection: {exc}")

    def _capture_roblox_client_image(self, hwnd: int):
        Image, ImageDraw, ImageGrab, ImageTk = self._require_map_capture_libraries()
        metrics = self._get_roblox_client_metrics(hwnd)
        width = metrics["client_width"]
        height = metrics["client_height"]
        if width <= 0 or height <= 0:
            raise RuntimeError("Roblox has an invalid client area.")
        left, top = metrics["client_origin"]
        screenshot = ImageGrab.grab(
            bbox=(left, top, left + width, top + height)
        ).convert("RGB")
        return screenshot, (int(left), int(top)), metrics

    def _match_map_reference(self, map_name: str, screenshot):
        """Match map title/artwork while masking out tags and the card border."""
        reference_path = self._map_reference_path(map_name)
        if reference_path is None:
            raise RuntimeError(
                f"No reference image is installed for {map_name}. Import its map-card crop."
            )
        Image, ImageDraw, ImageGrab, ImageTk, cv2, np = self._require_map_detection_libraries()
        template = cv2.imread(str(reference_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise RuntimeError(f"OpenCV could not read map reference: {reference_path.name}.")
        height, width = template.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        # The top strip contains the map title. The lower region contains the artwork.
        # The middle tag band (difficulty, drops, tower restrictions, +15% event)
        # and the outer yellow/selection border are deliberately excluded.
        mask[int(height * 0.025):int(height * 0.235),
             int(width * 0.02):int(width * 0.98)] = 255
        mask[int(height * 0.42):int(height * 0.97),
             int(width * 0.02):int(width * 0.98)] = 255

        screen_rgb = np.asarray(screenshot.convert("RGB"))
        screen_gray = cv2.cvtColor(screen_rgb, cv2.COLOR_RGB2GRAY)
        screen_gray = cv2.resize(
            screen_gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA
        )
        if template.shape[0] > screenshot.height or template.shape[1] > screenshot.width:
            raise RuntimeError(
                "The selected reference is larger than the Roblox client. Use its map-card crop."
            )

        best_score = -1.0
        best_second_score = -1.0
        best_box = None
        for scale in (0.90, 1.0, 1.10):
            scaled = cv2.resize(
                template, None, fx=scale * 0.5, fy=scale * 0.5,
                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
            )
            scaled_mask = cv2.resize(
                mask, (scaled.shape[1], scaled.shape[0]),
                interpolation=cv2.INTER_NEAREST
            )
            if (
                scaled.shape[0] < 8 or scaled.shape[1] < 8
                or scaled.shape[0] > screen_gray.shape[0]
                or scaled.shape[1] > screen_gray.shape[1]
            ):
                continue

            result = cv2.matchTemplate(
                screen_gray, scaled, cv2.TM_CCORR_NORMED, mask=scaled_mask
            )
            _minimum, maximum, _min_location, max_location = cv2.minMaxLoc(result)
            score = float(maximum)

            runner_up_map = result.copy()
            peak_x, peak_y = max_location
            pad_x = max(1, int(scaled.shape[1] * 0.75))
            pad_y = max(1, int(scaled.shape[0] * 0.75))
            left = max(0, peak_x - pad_x)
            top = max(0, peak_y - pad_y)
            right = min(runner_up_map.shape[1], peak_x + pad_x + 1)
            bottom = min(runner_up_map.shape[0], peak_y + pad_y + 1)
            runner_up_map[top:bottom, left:right] = -1.0
            _runner_min, runner_up, _runner_loc, _runner_point = cv2.minMaxLoc(runner_up_map)

            if score > best_score:
                best_score = score
                best_second_score = float(runner_up)
                best_box = (
                    int(max_location[0] * 2), int(max_location[1] * 2),
                    int(scaled.shape[1] * 2), int(scaled.shape[0] * 2)
                )

        if best_box is None:
            return {
                "score": 0.0, "second_score": 0.0,
                "margin": 0.0, "box": None, "center": None
            }
        x, y, box_width, box_height = best_box
        return {
            "score": best_score,
            "second_score": best_second_score,
            "margin": best_score - best_second_score,
            "box": best_box,
            "center": (x + box_width // 2, y + box_height // 2)
        }

    def _match_map_reference_for_detector(self, map_name: str, screenshot):
        """Find one map using its title strip, then verify with artwork below the tag band."""
        reference_path = self._map_reference_path(map_name)
        if reference_path is None:
            return None
        Image, ImageDraw, ImageGrab, ImageTk, cv2, np = self._require_map_detection_libraries()
        reference = cv2.imread(str(reference_path), cv2.IMREAD_GRAYSCALE)
        if reference is None:
            return None
        ref_height, ref_width = reference.shape[:2]
        title_x0 = int(ref_width * 0.02)
        title_x1 = int(ref_width * 0.75)
        title_y0 = int(ref_height * 0.025)
        title_y1 = int(ref_height * 0.235)
        title_template = reference[title_y0:title_y1, title_x0:title_x1]
        if title_template.size == 0 or float(np.std(title_template)) < 2.0:
            return None

        screen_rgb = np.asarray(screenshot.convert("RGB"))
        screen_gray = cv2.cvtColor(screen_rgb, cv2.COLOR_RGB2GRAY)
        search_scale = MAP_DETECTION_SCAN_SCREEN_SCALE
        screen_small = cv2.resize(
            screen_gray, None, fx=search_scale, fy=search_scale,
            interpolation=cv2.INTER_AREA
        )

        best_title_score = -1.0
        best_location = None
        best_card_scale = 1.0
        for card_scale in (0.90, 1.0, 1.10):
            scaled_title = cv2.resize(
                title_template, None,
                fx=search_scale * card_scale, fy=search_scale * card_scale,
                interpolation=cv2.INTER_AREA
            )
            if (
                scaled_title.shape[0] < 5 or scaled_title.shape[1] < 12
                or scaled_title.shape[0] > screen_small.shape[0]
                or scaled_title.shape[1] > screen_small.shape[1]
            ):
                continue
            result = cv2.matchTemplate(
                screen_small, scaled_title, cv2.TM_CCOEFF_NORMED
            )
            _minimum, score, _min_location, location = cv2.minMaxLoc(result)
            if float(score) > best_title_score:
                best_title_score = float(score)
                best_location = location
                best_card_scale = card_scale

        if best_location is None or best_title_score < 0.35:
            return {
                "matched": False, "score": max(0.0, best_title_score),
                "title_score": max(0.0, best_title_score), "art_score": None,
                "box": None, "center": None
            }

        card_left = int(round(best_location[0] / search_scale - title_x0 * best_card_scale))
        card_top = int(round(best_location[1] / search_scale - title_y0 * best_card_scale))
        card_width = int(round(ref_width * best_card_scale))
        card_height = int(round(ref_height * best_card_scale))
        art_x0 = int(ref_width * 0.02)
        art_x1 = int(ref_width * 0.98)
        art_y0 = int(ref_height * 0.42)
        art_y1 = int(ref_height * 0.97)

        expected_art = reference[art_y0:art_y1, art_x0:art_x1]
        art_left = card_left + int(round(art_x0 * best_card_scale))
        art_top = card_top + int(round(art_y0 * best_card_scale))
        art_right = card_left + int(round(art_x1 * best_card_scale))
        art_bottom = card_top + int(round(art_y1 * best_card_scale))
        art_score = None

        if (
            art_left >= 0 and art_top >= 0
            and art_right <= screenshot.width and art_bottom <= screenshot.height
        ):
            candidate_art = screen_gray[art_top:art_bottom, art_left:art_right]
            if candidate_art.size:
                candidate_art = cv2.resize(
                    candidate_art, (expected_art.shape[1], expected_art.shape[0]),
                    interpolation=cv2.INTER_AREA
                )
                art_score = float(
                    cv2.matchTemplate(
                        candidate_art, expected_art, cv2.TM_CCOEFF_NORMED
                    )[0, 0]
                )

        if art_score is None:
            combined_score = best_title_score
            matched = best_title_score >= 0.89
        else:
            combined_score = 0.55 * best_title_score + 0.45 * art_score
            matched = (
                best_title_score >= MAP_DETECTION_SCAN_TITLE_THRESHOLD
                and art_score >= MAP_DETECTION_SCAN_ART_THRESHOLD
                and combined_score >= MAP_DETECTION_SCAN_COMBINED_THRESHOLD
            )

        box = (card_left, card_top, card_width, card_height)
        return {
            "matched": bool(matched),
            "score": float(combined_score),
            "title_score": float(best_title_score),
            "art_score": art_score,
            "box": box,
            "center": (card_left + card_width // 2, card_top + card_height // 2)
        }

    def _show_map_detection_preview(self, screenshot, matches=None) -> None:
        try:
            Image, ImageDraw, ImageGrab, ImageTk = self._require_map_capture_libraries()
            preview = screenshot.copy()
            if matches:
                draw = ImageDraw.Draw(preview)
                for match in matches:
                    if not match or not match.get("box"):
                        continue
                    x, y, width, height = match["box"]
                    draw.rectangle(
                        (x, y, x + width, y + height),
                        outline=(90, 245, 165), width=max(3, preview.width // 500)
                    )
                    label = match.get("map_name", "Map")
                    draw.text((x + 5, max(2, y + 3)), label, fill=(120, 255, 185))
            resampling = getattr(Image, "Resampling", Image)
            preview.thumbnail((520, 220), resampling.LANCZOS)
            photo = ImageTk.PhotoImage(preview)
            self._map_detection_preview_photo = photo
            self.map_detection_preview_label.configure(image=photo, text="")
        except Exception as exc:
            self.map_detection_preview_label.configure(
                image="", text=f"Preview unavailable: {exc}"
            )

    def _capture_roblox_window_state(self, hwnd: int) -> dict:
        """Capture the native top-level state needed to restore Roblox exactly."""
        owner_index = getattr(win32con, "GW_OWNER", 4)
        return {
            "owner": int(win32gui.GetWindow(hwnd, owner_index) or 0),
            "style": int(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)),
            "ex_style": int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)),
            "rect": tuple(int(value) for value in win32gui.GetWindowRect(hwnd)),
            "placement": win32gui.GetWindowPlacement(hwnd),
            "visible": bool(win32gui.IsWindowVisible(hwnd)),
            "title": win32gui.GetWindowText(hwnd),
            "was_iconic": bool(win32gui.IsIconic(hwnd)),
        }

    def _get_window_dpi_scale(self, hwnd: int) -> float:
        """Return the target window's DPI scale; 1.25 at 125% Windows scaling."""
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_dpi = user32.GetDpiForWindow
            get_dpi.argtypes = [ctypes.c_void_p]
            get_dpi.restype = ctypes.c_uint
            dpi = int(get_dpi(ctypes.c_void_p(int(hwnd))) or 96)
            return max(1.0, min(3.0, dpi / 96.0))
        except Exception:
            return 1.0


    def _dock_roblox_big_screen(
        self, hwnd: int, saved_state: dict, work_area: tuple,
        mini_width: int, mini_height: int, gap: int
    ) -> tuple:
        """Use v1.0.44's native dock sequence, with Roblox filling the work area."""
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError("Roblox closed before bigger-screen docking.")

        root_hwnd = int(self.root.winfo_id())
        if not win32gui.IsWindow(root_hwnd) or root_hwnd == int(hwnd):
            raise RuntimeError("The BTD Macro owner window is unavailable.")

        work_left, work_top, work_right, work_bottom = (
            int(value) for value in work_area
        )
        work_width = work_right - work_left
        work_height = work_bottom - work_top
        gap = max(6, int(gap))
        if work_width < 900 or work_height < 500:
            raise RuntimeError(
                "The monitor work area is too small for the larger Roblox layout."
            )

        # Only the monitor work area is used. The compact control is overlaid
        # separately instead of taking width away from Roblox.
        roblox_rect = (work_left, work_top, work_right, work_bottom)

        WS_OVERLAPPEDWINDOW = 0x00CF0000
        WS_CHILD = 0x40000000
        WS_POPUP = 0x80000000
        WS_VISIBLE = 0x10000000
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        owner_index = getattr(win32con, "GW_OWNER", 4)

        # Keep v1.0.44 bookkeeping and native style/owner transition order.
        self.docked_hwnd = int(hwnd)
        self.original_window_state = dict(saved_state)
        self._arranging_windows = True
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

            big_style = (
                int(saved_state["style"]) | WS_OVERLAPPEDWINDOW | WS_VISIBLE
            ) & ~(WS_CHILD | WS_POPUP)
            big_ex_style = (
                int(saved_state["ex_style"]) | WS_EX_APPWINDOW
            ) & ~(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

            # Preserve the working v1.0.44 native owner relationship and style order.
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, big_style)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, big_ex_style)
            self._set_top_level_owner(hwnd, root_hwnd)
            win32gui.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED
            )

            left, top, right, bottom = roblox_rect
            win32gui.SetWindowPos(
                hwnd, 0, left, top, right - left, bottom - top,
                win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW
            )
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

            actual_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
            actual_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE))
            actual_ex_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))
            actual_rect = tuple(int(value) for value in win32gui.GetWindowRect(hwnd))

            if actual_owner != root_hwnd:
                raise RuntimeError(
                    f"Roblox did not dock to BTD Macro (expected owner {root_hwnd}, got {actual_owner})."
                )
            if (actual_style & WS_OVERLAPPEDWINDOW) != WS_OVERLAPPEDWINDOW:
                raise RuntimeError(
                    "Roblox's native title bar, minimize, maximize, or close styles are missing."
                )
            if not (actual_ex_style & WS_EX_APPWINDOW):
                raise RuntimeError(
                    "Windows did not retain WS_EX_APPWINDOW for taskbar/Alt+Tab eligibility."
                )
            if actual_ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
                raise RuntimeError("Roblox still has a tool-window or no-activate style.")
            if not win32gui.IsWindowVisible(hwnd):
                raise RuntimeError("Roblox did not become visible after bigger-screen docking.")

            actual_left, actual_top, actual_right, actual_bottom = actual_rect
            if (
                actual_left < work_left - 2 or actual_top < work_top - 2
                or actual_right > work_right + 2 or actual_bottom > work_bottom + 2
            ):
                raise RuntimeError(
                    "Roblox's outer frame does not fit in the complete monitor work area."
                )

            # Place the return control under the Roblox caption. This leaves
            # the title bar and its minimize/maximize/close buttons uncovered.
            try:
                client_origin = win32gui.ClientToScreen(hwnd, (0, 0))
                caption_bottom = max(work_top, int(client_origin[1]))
            except Exception:
                dpi_scale = self._get_window_dpi_scale(hwnd)
                caption_bottom = work_top + max(28, int(round(32 * dpi_scale)))

            mini_left = work_right - int(mini_width) - gap
            mini_top = max(work_top + gap, caption_bottom + gap)
            mini_top = min(mini_top, work_bottom - int(mini_height) - gap)
            if mini_top < work_top or mini_left < work_left:
                raise RuntimeError("The compact Macro overlay does not fit on this monitor.")
            mini_rect = (
                mini_left, mini_top,
                mini_left + int(mini_width), mini_top + int(mini_height)
            )

            self.game_placeholder.place_forget()
            self.dock_button.configure(
                text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
            )
            return roblox_rect, mini_rect
        finally:
            self._arranging_windows = False


    def _create_big_screen_compact_overlay(self, mini_rect: tuple) -> None:
        """Float the single return button using the real outer Tk toplevel HWND."""
        if self._big_screen_compact_window is not None:
            try:
                self._big_screen_compact_window.destroy()
            except tk.TclError:
                pass
            self._big_screen_compact_window = None

        left, top, right, bottom = (int(value) for value in mini_rect)
        width = max(1, right - left)
        height = max(1, bottom - top)

        overlay = tk.Toplevel(self.root)
        self._big_screen_compact_window = overlay
        overlay.withdraw()
        overlay.overrideredirect(True)
        overlay.configure(bg=BG)
        overlay.geometry(f"{width}x{height}+{left}+{top}")

        compact = tk.Frame(
            overlay, bg=BG, highlightthickness=1,
            highlightbackground=BORDER
        )
        compact.pack(fill="both", expand=True, padx=1, pady=1)
        self._make_button(
            compact, "CHANGE BACK TO NORMAL",
            self.change_back_to_normal_screen,
            bg=ACCENT, fg="#101522", active_bg=ACCENT_HOVER
        ).pack(fill="both", expand=True, padx=6, pady=6)

        # Map the Tk widgets first. On Windows, winfo_id() may be the inner
        # Tk client HWND; SetWindowLong/SetWindowPos must target its GA_ROOT
        # wrapper HWND, not that child window.
        overlay.update_idletasks()
        client_hwnd = int(overlay.winfo_id())
        if not win32gui.IsWindow(client_hwnd):
            raise RuntimeError("The compact Macro overlay client window could not be created.")

        overlay.attributes("-topmost", True)
        overlay.deiconify()
        overlay.update_idletasks()

        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_ancestor = user32.GetAncestor
        get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        get_ancestor.restype = wintypes.HWND
        GA_ROOT = 2
        outer_hwnd = int(
            get_ancestor(wintypes.HWND(client_hwnd), GA_ROOT) or 0
        )
        if not outer_hwnd:
            outer_hwnd = int(win32gui.GetParent(client_hwnd) or client_hwnd)
        if not win32gui.IsWindow(outer_hwnd):
            raise RuntimeError(
                "The compact Macro overlay's outer Windows window could not be found."
            )

        # Tkinter initially gives a Toplevel the root as its native owner.
        # Detach the actual top-level wrapper AFTER it is shown, so Tk has
        # finished mapping it and cannot re-apply the old owner afterward.
        self._set_top_level_owner(outer_hwnd, 0)

        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        overlay_ex_style = int(
            win32gui.GetWindowLong(outer_hwnd, win32con.GWL_EXSTYLE)
        )
        overlay_ex_style = (
            overlay_ex_style | WS_EX_TOOLWINDOW
        ) & ~WS_EX_APPWINDOW
        win32gui.SetWindowLong(
            outer_hwnd, win32con.GWL_EXSTYLE, overlay_ex_style
        )

        win32gui.SetWindowPos(
            outer_hwnd, -1, left, top, width, height,
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE
            | win32con.SWP_FRAMECHANGED
        )
        overlay.update_idletasks()

        actual_owner = int(win32gui.GetWindow(
            outer_hwnd, getattr(win32con, "GW_OWNER", 4)
        ) or 0)
        actual_ex_style = int(
            win32gui.GetWindowLong(outer_hwnd, win32con.GWL_EXSTYLE)
        )
        actual_rect = tuple(
            int(value) for value in win32gui.GetWindowRect(outer_hwnd)
        )
        if actual_owner != 0:
            raise RuntimeError(
                f"The compact overlay's outer window is still owned (owner {actual_owner})."
            )
        if not (actual_ex_style & WS_EX_TOOLWINDOW):
            raise RuntimeError(
                "The compact overlay's outer window did not retain WS_EX_TOOLWINDOW."
            )
        if not win32gui.IsWindowVisible(outer_hwnd):
            raise RuntimeError("The compact CHANGE BACK control is not visible.")
        if (
            abs(actual_rect[0] - left) > 3
            or abs(actual_rect[1] - top) > 3
            or abs(actual_rect[2] - right) > 3
            or abs(actual_rect[3] - bottom) > 3
        ):
            raise RuntimeError(
                "The compact CHANGE BACK control could not be placed at the requested position."
            )
    def enter_big_screen_mode(self) -> None:
        """Use the v1.0.44 transition with full-size Roblox and a floating return control."""
        if self._big_screen_mode:
            return
        if (
            self._lobby_sequence_active
            or self._roblox_scroll_test_active
            or self._map_detection_test_active
            or self._position_capture_armed
            or self._map_preview_original_rect is not None
        ):
            self.map_detection_result_var.set(
                "Stop the active task or preview before changing screen mode."
            )
            return
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.map_detection_result_var.set(
                "The bigger-screen mode requires Windows and pywin32."
            )
            return

        try:
            hwnd = self._get_roblox_hwnd_for_detection()
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window is no longer available.")

            was_docked = (
                self.docked_hwnd is not None
                and int(self.docked_hwnd) == int(hwnd)
                and self.original_window_state is not None
            )
            root_topmost = bool(self.root.attributes("-topmost"))

            # Step 1: preserve the v1.0.44 normal-layout undock sequence.
            if was_docked:
                self.append_log(
                    "Big-screen transition: undocking Roblox from its normal game-panel dock."
                )
                if not self.undock_roblox():
                    raise RuntimeError("Roblox could not be undocked from the normal layout.")

            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("Roblox closed while leaving the normal dock.")

            saved_native_state = self._capture_roblox_window_state(hwnd)
            root_hwnd = int(self.root.winfo_id())
            state = {
                "hwnd": int(hwnd),
                "was_docked": bool(was_docked),
                "window_state": saved_native_state,
                "root_topmost": root_topmost,
                "root_ex_style": int(
                    win32gui.GetWindowLong(root_hwnd, win32con.GWL_EXSTYLE)
                ),
                "work_area": None,
            }
            self._big_screen_state = state
            self._big_screen_mode = True

            monitor = win32api.MonitorFromWindow(
                hwnd, getattr(win32con, "MONITOR_DEFAULTTONEAREST", 2)
            )
            monitor_info = win32api.GetMonitorInfo(monitor)
            work_area = tuple(int(value) for value in monitor_info["Work"])
            state["work_area"] = work_area

            dpi_scale = self._get_window_dpi_scale(root_hwnd)
            mini_width = int(round(300 * dpi_scale))
            mini_height = int(round(104 * dpi_scale))
            gap = max(8, int(round(10 * dpi_scale)))
            mini_width = min(
                mini_width, max(220, int((work_area[2] - work_area[0]) * 0.30))
            )

            # Step 2: keep the real Macro root alive as Roblox's owner, but move
            # its unused 1x1 client area beneath Roblox. The mini control is a
            # separate topmost tool window so it can overlay the game.
            self.root.state("normal")
            self.root.minsize(1, 1)
            self.main_layout.pack_forget()
            self.root.title(APP_TITLE)
            self.root.attributes("-topmost", False)
            self.root.geometry(f"1x1+{work_area[0]}+{work_area[1]}")
            self.root.update_idletasks()
            self.root.deiconify()

            # Hide the unused owner shell from taskbar/Alt+Tab without hiding it
            # from Windows; Roblox keeps WS_EX_APPWINDOW in its own big-mode style.
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            root_ex_style = int(
                win32gui.GetWindowLong(root_hwnd, win32con.GWL_EXSTYLE)
            )
            root_ex_style = (
                root_ex_style | WS_EX_TOOLWINDOW
            ) & ~WS_EX_APPWINDOW
            win32gui.SetWindowLong(
                root_hwnd, win32con.GWL_EXSTYLE, root_ex_style
            )
            win32gui.SetWindowPos(
                root_hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED
            )

            # Step 3: execute v1.0.44's original owner/style/show/bring-to-top
            # sequence. Only the target rectangle is changed to the full work area.
            big_rect, mini_rect = self._dock_roblox_big_screen(
                hwnd, saved_native_state, work_area,
                mini_width, mini_height, gap
            )

            # Step 4: display the separate return button above the full-size game.
            self._create_big_screen_compact_overlay(mini_rect)

            actual = self._get_roblox_client_metrics(hwnd)
            self.map_detection_result_var.set(
                "Bigger layout active. Roblox fills the work area; CHANGE BACK floats over the game."
            )
            self.set_status(
                "Roblox fills the available screen. Macro return button floats over it.",
                "success"
            )
            self.append_log(
                "Big-screen layout completed using v1.0.44's original Roblox undock, "
                "native owner assignment, style order, ShowWindow, BringWindowToTop, "
                "and restore mechanism. Only the layout changed: Roblox now fills "
                f"the full monitor work area {big_rect}; the topmost CHANGE BACK "
                f"control floats at {mini_rect}. DPI scale={dpi_scale:.2f}. "
                "The taskbar remains outside the work area and Roblox keeps its "
                "native title bar and taskbar/Alt+Tab style."
            )
        except Exception as exc:
            self.append_log(f"ERROR entering bigger-screen dock: {exc}")
            if self._big_screen_state is not None:
                if not self._restore_big_screen_mode():
                    self.set_status(
                        f"Bigger-screen setup failed; compact recovery control was kept: {exc}",
                        "warning"
                    )
                    self.map_detection_result_var.set(
                        f"Could not finish bigger-screen docking: {exc}"
                    )
                    return
            else:
                self.set_status(f"Could not enter bigger-screen mode: {exc}", "warning")
                self.map_detection_result_var.set(
                    f"Could not enter bigger-screen mode: {exc}"
                )
    def change_back_to_normal_screen(self) -> None:
        """Undock the larger Roblox window, restore Macro, and dock Roblox normally."""
        self._restore_big_screen_mode()


    def _restore_big_screen_mode(self) -> bool:
        state = self._big_screen_state
        if not self._big_screen_mode and state is None:
            return True

        hwnd = int(state.get("hwnd") or 0) if state else 0
        was_docked = bool(state.get("was_docked")) if state else False
        exists = bool(hwnd and PYWIN32_AVAILABLE and win32gui.IsWindow(hwnd))

        # Detach the large Roblox dock first. Keep CHANGE BACK available if it fails.
        if self.docked_hwnd is not None:
            if not self.undock_roblox():
                self.set_status(
                    "Could not undock the larger Roblox window; compact control remains available.",
                    "warning"
                )
                self.map_detection_result_var.set(
                    "Big Roblox undock failed. The compact CHANGE BACK button is still available."
                )
                return False
        elif exists:
            try:
                current_owner = int(win32gui.GetWindow(
                    hwnd, getattr(win32con, "GW_OWNER", 4)
                ) or 0)
                if current_owner == int(self.root.winfo_id()):
                    self._restore_roblox_window(hwnd, state["window_state"])
            except Exception as exc:
                self.set_status(
                    f"Could not restore Roblox's native window: {exc}", "warning"
                )
                self.map_detection_result_var.set(
                    "Roblox restoration failed. Compact control remains available."
                )
                return False

        try:
            # Remove the floating button after Roblox is detached.
            if self._big_screen_compact_window is not None:
                try:
                    self._big_screen_compact_window.destroy()
                except tk.TclError:
                    pass
                self._big_screen_compact_window = None

            if self._big_screen_compact_frame is not None:
                self._big_screen_compact_frame.destroy()
                self._big_screen_compact_frame = None

            # Restore the root's original taskbar/Alt+Tab style.
            root_hwnd = int(self.root.winfo_id())
            if state and state.get("root_ex_style") is not None and win32gui.IsWindow(root_hwnd):
                win32gui.SetWindowLong(
                    root_hwnd, win32con.GWL_EXSTYLE,
                    int(state["root_ex_style"])
                )
                win32gui.SetWindowPos(
                    root_hwnd, 0, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                    | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                    | win32con.SWP_FRAMECHANGED
                )

            self.main_layout.pack(fill="both", expand=True, padx=8, pady=8)
            self.root.title(APP_TITLE)
            self.root.attributes(
                "-topmost",
                state.get("root_topmost", False) if state else False
            )
            self.root.minsize(1000, 620)
            self.root.state("zoomed")
            self.root.update_idletasks()
            self._maximize_and_lock_window()

            self._big_screen_mode = False
            self._big_screen_state = None

            # Use the untouched normal docking routine with the same Roblox HWND.
            if was_docked and exists:
                self.root.update_idletasks()
                self.game_host.update_idletasks()
                self.dock_roblox(hwnd_override=hwnd)
                if self.docked_hwnd == hwnd:
                    self.set_status(
                        "Roblox undocked from the big layout and docked back to normal.",
                        "success"
                    )
                    self.map_detection_result_var.set(
                        "Normal layout restored. Roblox is docked in the normal game panel."
                    )
                    self.append_log(
                        "Normal return completed: large layout removed; Roblox's original "
                        "native state restored; full Macro layout restored; same Roblox "
                        "HWND docked again using the original normal game-panel geometry."
                    )
                else:
                    self.set_status(
                        "Normal Macro layout restored, but Roblox could not be re-docked.",
                        "warning"
                    )
                    self.map_detection_result_var.set(
                        "Normal layout restored. Roblox is undocked; use DOCK ROBLOX to retry."
                    )
            else:
                self.set_status("Returned to the normal BTD Macro layout.")
                if was_docked and not exists:
                    self.map_detection_result_var.set(
                        "Roblox closed during big mode. Normal Macro layout restored."
                    )
                else:
                    self.map_detection_result_var.set(
                        "Normal layout restored. Roblox remains undocked as before."
                    )

            self.root.lift()
            return True
        except Exception as exc:
            self.set_status(
                f"Returned to Macro, but normal docking needs attention: {exc}",
                "warning"
            )
            self.append_log(f"WARNING restoring normal screen mode: {exc}")
            return False
    def expand_roblox_for_manual_capture(self) -> None:
        """Leave Roblox expanded so the user can capture references or re-record F3 points."""
        if self._lobby_sequence_active or self._roblox_scroll_test_active or self._map_detection_test_active:
            self.map_detection_result_var.set(
                "Stop the active task before manually expanding Roblox."
            )
            return
        try:
            hwnd = self._get_roblox_hwnd_for_detection()
            self._expand_roblox_for_map_detection(hwnd)
            metrics = self._get_roblox_client_metrics(hwnd)
            self._update_map_detection_size(metrics)
            self.map_detection_result_var.set(
                "Roblox is expanded in windowed mode. Press F8 to restore its original size."
            )
            self.set_status("Roblox expanded for screenshots / F3 capture. Press F8 to restore.")
            self.append_log(
                "Roblox left expanded for manual map screenshots or F3 coordinate capture; "
                "F8 will restore the saved docked rectangle."
            )
        except Exception as exc:
            self._restore_roblox_preview_window()
            self.map_detection_result_var.set(f"Could not expand Roblox: {exc}")
            self.set_status(f"Could not expand Roblox: {exc}", "warning")

    def preview_roblox_map_size(self) -> None:
        self._begin_map_detection_test(preview_only=True)

    def test_map_detection(self) -> None:
        self.start_map_detector()

    def start_map_detector(self) -> None:
        if self._lobby_sequence_active or self._roblox_scroll_test_active or self._map_detection_test_active:
            self.map_detection_result_var.set(
                "Stop the currently running task before starting Map Detection."
            )
            return
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.map_detection_result_var.set(
                "Map Detection requires Windows and pywin32."
            )
            return

        missing = [
            name for name in MAP_OPTIONS if self._map_reference_path(name) is None
        ]
        if missing:
            message = (
                "Missing map reference images: " + ", ".join(missing) +
                ". Put BTD_Macro_1.0.32_map_references.zip beside the .py file and restart, "
                "or import the missing map crops from this tab."
            )
            self.map_detection_result_var.set(message)
            self.append_log("Map detector not started: " + message)
            return

        try:
            self._require_map_detection_libraries()
            hwnd = self._get_roblox_hwnd_for_detection()
            self._map_detection_test_active = True
            self._map_detection_test_state = {
                "hwnd": hwnd,
                "preview_only": False,
                "all_maps": True,
                "cursor_before": tuple(int(value) for value in win32gui.GetCursorPos()),
                "down_notches": 0,
                "scroll_point": None,
                "detected": {},
                "previous_frame": None,
                "unchanged_count": 0,
                "scan_count": 0
            }
            for map_name in MAP_OPTIONS:
                self._set_map_detection_row(map_name, "Searching")
            self.map_detection_count_var.set("0 / 15 detected")
            self.map_detection_test_button.configure(state="disabled")
            self.map_preview_button.configure(state="disabled")
            self.map_detection_result_var.set(
                "Expanding Roblox, resetting the map list, then checking all 15 maps…"
            )
            self._expand_roblox_for_map_detection(hwnd)
            self._map_detection_after_id = self.root.after(
                MAP_DETECTION_EXPAND_SETTLE_MS,
                self._map_detection_test_after_expand
            )
        except Exception as exc:
            self._finish_map_detection_test(
                f"Map detector could not start: {exc}", success=False
            )

    def _begin_map_detection_test(self, preview_only: bool) -> None:
        if not preview_only:
            self.start_map_detector()
            return
        if self._lobby_sequence_active or self._roblox_scroll_test_active or self._map_detection_test_active:
            self.map_detection_result_var.set(
                "Stop the currently running task before starting a map preview."
            )
            return
        try:
            hwnd = self._get_roblox_hwnd_for_detection()
            self._require_map_capture_libraries()
            self._map_detection_test_active = True
            self._map_detection_test_state = {
                "hwnd": hwnd,
                "preview_only": True,
                "all_maps": False,
                "cursor_before": tuple(int(value) for value in win32gui.GetCursorPos()),
                "down_notches": 0,
                "scroll_point": None
            }
            self.map_preview_button.configure(state="disabled")
            self.map_detection_test_button.configure(state="disabled")
            self.map_detection_result_var.set(
                "Expanding Roblox in windowed mode and capturing its real client area…"
            )
            self._expand_roblox_for_map_detection(hwnd)
            self._map_detection_after_id = self.root.after(
                MAP_DETECTION_EXPAND_SETTLE_MS,
                self._map_detection_test_after_expand
            )
        except Exception as exc:
            self._finish_map_detection_test(
                f"Map preview could not start: {exc}", success=False
            )

    def _map_detection_test_after_expand(self) -> None:
        if not self._map_detection_test_active or not self._map_detection_test_state:
            return
        try:
            state = self._map_detection_test_state
            hwnd = state["hwnd"]
            screenshot, origin, metrics = self._capture_roblox_client_image(hwnd)
            self._update_map_detection_size(metrics)
            if state["preview_only"]:
                self._show_map_detection_preview(screenshot)
                self._finish_map_detection_test(
                    "Roblox size preview captured. The screenshot above shows the actual client area.",
                    success=True
                )
                return

            scroll_point = self._map_detection_scroll_point(hwnd)
            state["scroll_point"] = scroll_point
            win32api.SetCursorPos(scroll_point)
            self.map_detection_result_var.set(
                "Resetting the map list to its top before scanning all 15 maps…"
            )
            self._map_detection_test_reset_scroll(0)
        except Exception as exc:
            self._finish_map_detection_test(
                f"Map detector failed before scanning: {exc}", success=False
            )

    def _map_detection_test_reset_scroll(self, completed: int) -> None:
        if not self._map_detection_test_active or not self._map_detection_test_state:
            return
        try:
            state = self._map_detection_test_state
            hwnd = state["hwnd"]
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("Roblox closed during map detection.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError("Roblox lost foreground focus; map scanning cancelled.")
            if completed >= MAP_DETECTION_RESET_NOTCHES:
                metrics = self._get_roblox_client_metrics(hwnd)
                origin = metrics["client_origin"]
                state["safe_cursor"] = (
                    origin[0] + max(3, metrics["client_width"] - 5),
                    origin[1] + max(3, metrics["client_height"] - 5)
                )
                win32api.SetCursorPos(state["safe_cursor"])
                self._map_detection_after_id = self.root.after(
                    MAP_DETECTION_SETTLE_MS, self._map_detection_test_scan
                )
                return
            win32api.SetCursorPos(state["scroll_point"])
            win32api.mouse_event(0x0800, 0, 0, 120, 0)
            self._map_detection_after_id = self.root.after(
                MAP_DETECTION_SCROLL_INTERVAL_MS,
                self._map_detection_test_reset_scroll, completed + 1
            )
        except Exception as exc:
            self._finish_map_detection_test(
                f"Map-list reset failed: {exc}", success=False
            )

    def _map_detection_test_scan(self) -> None:
        if not self._map_detection_test_active or not self._map_detection_test_state:
            return
        try:
            state = self._map_detection_test_state
            hwnd = state["hwnd"]
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("Roblox closed during map detection.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError("Roblox lost foreground focus; map detection cancelled.")

            screenshot, origin, metrics = self._capture_roblox_client_image(hwnd)
            self._update_map_detection_size(metrics)
            newly_detected = []
            for map_name in MAP_OPTIONS:
                if map_name in state["detected"]:
                    continue
                match = self._match_map_reference_for_detector(map_name, screenshot)
                if match and match.get("matched"):
                    state["detected"][map_name] = match
                    self._set_map_detection_row(map_name, "Detected")
                    marked = dict(match)
                    marked["map_name"] = map_name
                    newly_detected.append(marked)
                    self.append_log(
                        f"Map detector: {map_name} detected "
                        f"(combined={match['score']:.3f}, "
                        f"title={match['title_score']:.3f}, "
                        f"art={match['art_score'] if match['art_score'] is not None else 'partial-card'})."
                    )

            state["scan_count"] += 1
            self._show_map_detection_preview(screenshot, newly_detected)
            found_count = len(state["detected"])
            self.map_detection_count_var.set(f"{found_count} / 15 detected")
            self.map_detection_result_var.set(
                f"Scanning map list: {found_count}/15 detected — "
                f"scroll position {state['down_notches'] + 1}."
            )

            if found_count >= len(MAP_OPTIONS):
                self._finish_map_detection_test("", success=True)
                return

            Image, ImageDraw, ImageGrab, ImageTk, cv2, np = self._require_map_detection_libraries()
            thumbnail = cv2.cvtColor(
                np.asarray(screenshot.convert("RGB")), cv2.COLOR_RGB2GRAY
            )
            thumbnail = cv2.resize(thumbnail, (96, 54), interpolation=cv2.INTER_AREA)
            previous = state.get("previous_frame")
            if previous is not None:
                difference = float(np.mean(cv2.absdiff(thumbnail, previous)))
                if difference < 0.9:
                    state["unchanged_count"] += 1
                else:
                    state["unchanged_count"] = 0
            state["previous_frame"] = thumbnail

            if state["unchanged_count"] >= 2:
                self._finish_map_detection_test("", success=True)
                return
            if state["down_notches"] >= MAP_DETECTION_SCAN_MAX_DOWN_NOTCHES:
                self._finish_map_detection_test("", success=True)
                return

            win32api.SetCursorPos(state["scroll_point"])
            win32api.mouse_event(0x0800, 0, 0, -120, 0)
            state["down_notches"] += 1
            if state.get("safe_cursor"):
                win32api.SetCursorPos(state["safe_cursor"])
            self.map_detection_result_var.set(
                f"Scanning all 15 maps: {found_count}/15 detected — "
                f"moving down the list ({state['down_notches']}/"
                f"{MAP_DETECTION_SCAN_MAX_DOWN_NOTCHES})."
            )
            self._map_detection_after_id = self.root.after(
                MAP_DETECTION_SETTLE_MS, self._map_detection_test_scan
            )
        except Exception as exc:
            self._finish_map_detection_test(
                f"Map detection scan failed: {exc}", success=False
            )

    def _set_map_detection_row(self, map_name: str, status: str) -> None:
        tree = getattr(self, "map_detection_tree", None)
        if tree is None:
            return
        item_id = self._map_reference_slug(map_name)
        if not tree.exists(item_id):
            return
        tag = (
            "detected" if status == "Detected"
            else "not_detected" if status == "Not detected"
            else "stopped" if status == "Stopped"
            else "pending"
        )
        tree.set(item_id, "status", status)
        tree.item(item_id, tags=(tag,))

    def _finish_map_detection_test(self, message: str, success: bool) -> None:
        self._map_detection_test_active = False
        if self._map_detection_after_id is not None:
            try:
                self.root.after_cancel(self._map_detection_after_id)
            except tk.TclError:
                pass
            self._map_detection_after_id = None
        state = self._map_detection_test_state
        self._map_detection_test_state = None

        if state and state.get("all_maps"):
            if success:
                detected = set(state.get("detected", {}).keys())
                for map_name in MAP_OPTIONS:
                    self._set_map_detection_row(
                        map_name,
                        "Detected" if map_name in detected else "Not detected"
                    )
                count = len(detected)
                message = (
                    f"Detector complete: {count}/15 maps detected, "
                    f"{15 - count} not detected. No map was clicked."
                )
            else:
                for map_name in MAP_OPTIONS:
                    if map_name not in state.get("detected", {}):
                        self._set_map_detection_row(map_name, "Stopped")
                count = len(state.get("detected", {}))
                if not message:
                    message = f"Detector stopped: {count}/15 maps had been detected. No map was clicked."
            self.map_detection_count_var.set(
                f"{count} / 15 detected"
            )

        self._restore_roblox_preview_window()
        if state and os.name == "nt" and PYWIN32_AVAILABLE:
            try:
                win32api.SetCursorPos(state["cursor_before"])
            except Exception:
                pass
        for button_name in ("map_preview_button", "map_detection_test_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                try:
                    button.configure(state="normal")
                except tk.TclError:
                    pass
        if hasattr(self, "map_detection_result_var"):
            self.map_detection_result_var.set(message)
        if success:
            self.set_status(message, "success")
            self.append_log("Map detection completed: " + message)
        else:
            self.set_status(message, "warning")
            self.append_log("Map detection stopped: " + message)

    def _build_debug_tab(self) -> None:
        content = tk.Frame(self.debug_tab, bg=BG)
        content.pack(fill="both", expand=True, padx=12, pady=12)

        header = tk.Frame(content, bg=BG)
        header.pack(fill="x")
        title_area = tk.Frame(header, bg=BG)
        title_area.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_area, text="Debug log", bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 16)
        ).pack(anchor="w")
        tk.Label(
            title_area, text="Application messages and diagnostic output.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(3, 0))
        self._make_button(
            header, "CLEAR LOG", self.clear_log, small=True,
            bg=PANEL_ALT, fg=TEXT
        ).pack(side="right", padx=(10, 0), pady=3)

        log_panel = tk.Frame(
            content, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        log_panel.pack(fill="both", expand=True, pady=(16, 0))
        self.log_text = tk.Text(
            log_panel, bg="#0D1118", fg="#C9D3E2", insertbackground=TEXT,
            selectbackground="#344263", relief="flat", bd=0,
            wrap="word", font=("Consolas", 9), padx=12, pady=12,
            state="disabled"
        )
        log_scroll = ttk.Scrollbar(
            log_panel, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------------
    # Shared UI helpers
    # ------------------------------------------------------------------
    def _make_button(
        self, parent: tk.Widget, text: str, command,
        *, bg: str = PANEL_ALT, fg: str = TEXT,
        active_bg: str | None = None, small: bool = False
    ) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground=active_bg or bg,
            activeforeground=fg, relief="flat", bd=0,
            font=("Segoe UI Semibold", 8 if small else 9),
            padx=11 if small else 14, pady=7 if small else 10,
            cursor="hand2"
        )

    @staticmethod
    def _classify_status(message: str) -> str:
        """Infer a semantic status category from the existing status messages."""
        value = str(message).strip().casefold()

        if any(term in value for term in (
            "error", "failed", "failure", "could not", "couldn't",
            "not found", "was not docked", "cannot safely", "unsafe"
        )):
            return "error"
        if any(term in value for term in (
            "choose ", "select ", "enter ", "requires ", "not connected",
            "no active ", "not currently ", "outside roblox",
            "could not verify", "unavailable", "mismatch"
        )):
            return "warning"
        if any(term in value for term in (
            "downloading", "installing", "checking ", "armed",
            "starting ", "capturing ", "updating ", "in progress"
        )):
            return "progress"
        if any(term in value for term in (
            "saved", "loaded", "deleted", "docked", "undocked",
            "restored", "cleared", "up to date", "recorded", "closed"
        )):
            return "success"
        return "idle"

    def _resize_status_wrap(self, event=None) -> None:
        """Keep status wrapping aligned with the available width in the panel."""
        try:
            width = event.width if event is not None else self.status_frame.winfo_width()
            self.status_label.configure(wraplength=max(150, width - 125))
        except (AttributeError, tk.TclError):
            pass

    def set_status(self, message: str, state: str | None = None) -> None:
        full_message = " ".join(str(message).split())
        display_message = textwrap.shorten(
            full_message, width=62, placeholder="…"
        ) or "Idle — ready."
        self.status_var.set(display_message)

        category = state or self._classify_status(full_message)
        colors = {
            "progress": STATUS_PROGRESS,
            "success": STATUS_SUCCESS,
            "warning": STATUS_WARNING,
            "error": STATUS_ERROR,
            "idle": STATUS_IDLE,
        }
        self.status_label.configure(fg=colors.get(category, STATUS_IDLE))

        if display_message != full_message:
            self.append_log(f"Full status detail: {full_message}")
        self.root.update_idletasks()

    def append_log(self, message: str) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.set_status("Debug log cleared.")

    # ------------------------------------------------------------------
    # Macro tab: Roblox button position capture
    # ------------------------------------------------------------------
    def _position_target_options(self) -> list[str]:
        """Return fixed lobby controls plus the map-list scroll area for detection."""
        # Map cards move and overlap when Roblox changes size. Use image detection,
        # not saved per-map coordinates.
        return [
            "PLAY", "CREATE PARTY", "PRIVATE", "KICK", "CHANGE MAP",
            "MAP SCROLL AREA", "START"
        ]

    def test_roblox_focus_and_scroll(self) -> None:
        """Focus Roblox, move to the saved map-list point, refresh hover, and scroll once."""
        if self._lobby_sequence_active:
            self.set_status("Stop the current lobby sequence before running this test.", "warning")
            return
        if self._roblox_scroll_test_active:
            self.set_status("Roblox scroll test is already running.", "warning")
            return
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.set_status(
                "Roblox test requires Windows and pywin32.", "warning"
            )
            self.append_log(
                "Roblox test unavailable: Windows/pywin32 support is required."
            )
            return

        scroll_area_record = self.button_positions.get("MAP SCROLL AREA")
        if not isinstance(scroll_area_record, dict):
            self.set_status(
                "Record MAP SCROLL AREA with F3 before running this test.",
                "warning"
            )
            self.append_log(
                "Roblox scroll test not started: MAP SCROLL AREA has no saved "
                "position. Select it in Position Recorder, hover the scrollable "
                "map list in Roblox, and press F3."
            )
            return

        try:
            if self.docked_hwnd is not None and win32gui.IsWindow(self.docked_hwnd):
                hwnd = int(self.docked_hwnd)
            else:
                hwnd = self._find_roblox_window()

            if hwnd is None or not win32gui.IsWindow(hwnd):
                self.set_status("Roblox window not found. Open Roblox first.", "error")
                self.append_log("Roblox scroll test stopped: no Roblox window found.")
                return

            self._roblox_scroll_test_active = True
            self.test_roblox_button.configure(state="disabled")
            self.set_status("Focusing Roblox for the scroll test…", "progress")
            self.append_log(
                "Roblox test started: focus game, smoothly move cursor to the "
                "saved MAP SCROLL AREA, verify arrival, refresh hover, then scroll once."
            )

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            self.root.after(180, self._continue_roblox_scroll_test, hwnd, 5)
        except Exception as exc:
            self._finish_roblox_scroll_test(
                False, f"Could not focus Roblox: {exc}"
            )

    def _continue_roblox_scroll_test(
        self, hwnd: int, attempts_left: int
    ) -> None:
        """Wait for Roblox focus, then begin gradual cursor movement."""
        if not self._roblox_scroll_test_active:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window closed during the test.")

            foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
            if foreground_hwnd != hwnd:
                if attempts_left <= 0:
                    raise RuntimeError(
                        "Windows did not give Roblox foreground focus. "
                        "No cursor movement or wheel input was sent."
                    )
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
                self.root.after(
                    120, self._continue_roblox_scroll_test,
                    hwnd, attempts_left - 1
                )
                return

            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            client_width = int(right - left)
            client_height = int(bottom - top)
            if client_width <= 0 or client_height <= 0:
                raise RuntimeError("Roblox has an invalid client area.")

            record = self.button_positions.get("MAP SCROLL AREA")
            if not isinstance(record, dict):
                raise RuntimeError(
                    "MAP SCROLL AREA position is missing. Record it with F3 first."
                )
            saved_width = int(record.get("client_width", 0))
            saved_height = int(record.get("client_height", 0))
            if saved_width <= 0 or saved_height <= 0:
                raise RuntimeError(
                    "Saved MAP SCROLL AREA has invalid client dimensions. Re-record it."
                )

            # Saved positions are client-relative. Scale to the current game size
            # if Roblox was resized after the map-list location was captured.
            client_x = round(int(record["x"]) * client_width / saved_width)
            client_y = round(int(record["y"]) * client_height / saved_height)
            client_x = max(0, min(client_width - 1, client_x))
            client_y = max(0, min(client_height - 1, client_y))
            target = tuple(int(value) for value in win32gui.ClientToScreen(
                hwnd, (client_x, client_y)
            ))
            start = tuple(int(value) for value in win32gui.GetCursorPos())
            self.append_log(
                f"Roblox MAP SCROLL AREA target: client=({client_x}, {client_y}), "
                f"saved_client={saved_width}x{saved_height}, "
                f"current_client={client_width}x{client_height}, "
                f"screen=({target[0]}, {target[1]}), "
                f"start=({start[0]}, {start[1]})."
            )
            self.set_status(
                "Moving cursor to saved map-list scroll area…", "progress"
            )
            self._move_roblox_test_cursor(hwnd, start, target, 1, 1)
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    def _move_roblox_test_cursor(
        self, hwnd: int, start: tuple[int, int], target: tuple[int, int],
        step_number: int, attempts_left: int
    ) -> None:
        """Move in eased increments and verify the actual Windows cursor position."""
        if not self._roblox_scroll_test_active:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window closed during cursor movement.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError(
                    "Roblox lost focus during cursor movement. Scrolling cancelled."
                )

            progress = min(1.0, step_number / TEST_CURSOR_MOVE_STEPS)
            eased = progress * progress * (3.0 - 2.0 * progress)
            x = round(start[0] + (target[0] - start[0]) * eased)
            y = round(start[1] + (target[1] - start[1]) * eased)
            win32api.SetCursorPos((x, y))

            if step_number < TEST_CURSOR_MOVE_STEPS:
                interval = max(
                    1, round(TEST_CURSOR_MOVE_DURATION_MS / TEST_CURSOR_MOVE_STEPS)
                )
                self.root.after(
                    interval, self._move_roblox_test_cursor,
                    hwnd, start, target, step_number + 1, attempts_left
                )
                return

            actual = tuple(int(value) for value in win32gui.GetCursorPos())
            error_x = actual[0] - target[0]
            error_y = actual[1] - target[1]
            if (
                abs(error_x) > TEST_CURSOR_POSITION_TOLERANCE_PX
                or abs(error_y) > TEST_CURSOR_POSITION_TOLERANCE_PX
            ):
                self.append_log(
                    f"Cursor arrival check failed: expected=({target[0]}, {target[1]}), "
                    f"actual=({actual[0]}, {actual[1]}), "
                    f"delta=({error_x}, {error_y})."
                )
                if attempts_left > 0:
                    self.set_status("Rechecking cursor position…", "warning")
                    self._move_roblox_test_cursor(
                        hwnd, actual, target, 1, attempts_left - 1
                    )
                    return
                raise RuntimeError(
                    "Cursor did not reach Roblox center. "
                    f"Expected ({target[0]}, {target[1]}), got "
                    f"({actual[0]}, {actual[1]}). No scroll input was sent."
                )

            self.append_log(
                f"Cursor arrival verified: expected=({target[0]}, {target[1]}), "
                f"actual=({actual[0]}, {actual[1]}). "
                f"Waiting {TEST_CURSOR_ARRIVAL_DELAY_MS} ms before scrolling."
            )
            self.set_status("Cursor centered. Preparing to scroll…", "progress")
            self.root.after(
                TEST_CURSOR_ARRIVAL_DELAY_MS,
                self._begin_roblox_test_scroll, hwnd, target
            )
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    @staticmethod
    def _send_relative_mouse_move(dx: int, dy: int) -> None:
        """Insert a real relative mouse-movement event through the Windows input stream."""
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_int32),
                ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class INPUT(ctypes.Structure):
            # The mouse input structure occupies the largest INPUT union member.
            _fields_ = [
                ("type", ctypes.c_uint32),
                ("mi", MOUSEINPUT),
            ]

        event = INPUT()
        event.type = 0  # INPUT_MOUSE
        event.mi = MOUSEINPUT(
            int(dx), int(dy), 0, 0x0001, 0, 0  # MOUSEEVENTF_MOVE (relative)
        )

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_input = user32.SendInput
        send_input.argtypes = [
            ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int
        ]
        send_input.restype = ctypes.c_uint
        ctypes.set_last_error(0)
        sent = int(send_input(1, ctypes.byref(event), ctypes.sizeof(INPUT)))
        if sent != 1:
            error_code = ctypes.get_last_error()
            if error_code:
                raise ctypes.WinError(error_code)
            raise RuntimeError(
                "Windows did not accept the relative mouse-movement event."
            )

    def _begin_roblox_test_scroll(
        self, hwnd: int, target: tuple[int, int]
    ) -> None:
        """Recheck focus/position, then nudge the pointer to refresh Roblox GUI hover."""
        if not self._roblox_scroll_test_active:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window closed before scrolling.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError(
                    "Roblox lost focus during the arrival delay. No scroll input was sent."
                )
            actual = tuple(int(value) for value in win32gui.GetCursorPos())
            if (
                abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX
                or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX
            ):
                raise RuntimeError(
                    f"Cursor moved after arrival: expected=({target[0]}, {target[1]}), "
                    f"actual=({actual[0]}, {actual[1]}). No scroll input was sent."
                )

            # SetCursorPos places the pointer, but does not guarantee the same
            # relative mouse-motion event stream as a physical mouse movement.
            # Nudge right slightly and then return to wake the game's hover state.
            self._send_relative_mouse_move(TEST_HOVER_NUDGE_PX, 0)
            self.append_log(
                f"Sent relative mouse nudge (+{TEST_HOVER_NUDGE_PX}, 0) "
                "to refresh the map GUI hover state."
            )
            self.root.after(
                TEST_HOVER_NUDGE_OUT_DELAY_MS,
                self._return_roblox_cursor_after_hover_nudge,
                hwnd, target
            )
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    def _return_roblox_cursor_after_hover_nudge(
        self, hwnd: int, target: tuple[int, int]
    ) -> None:
        """Return from the relative nudge, restoring the exact saved screen point."""
        if not self._roblox_scroll_test_active:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window closed during the hover nudge.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError(
                    "Roblox lost focus during the hover nudge; scrolling cancelled."
                )

            after_nudge = tuple(int(value) for value in win32gui.GetCursorPos())
            delta_x = after_nudge[0] - target[0]
            delta_y = after_nudge[1] - target[1]
            if abs(delta_x) > 20 or abs(delta_y) > 20:
                raise RuntimeError(
                    "The relative mouse nudge moved farther than expected: "
                    f"target=({target[0]}, {target[1]}), "
                    f"actual=({after_nudge[0]}, {after_nudge[1]}). "
                    "Scrolling cancelled for safety."
                )

            if delta_x or delta_y:
                self._send_relative_mouse_move(-delta_x, -delta_y)
            else:
                # Even if Windows reports no cursor displacement, send a reverse
                # relative event so the game receives a distinct movement update.
                self._send_relative_mouse_move(-TEST_HOVER_NUDGE_PX, 0)

            self.root.after(
                TEST_HOVER_SETTLE_DELAY_MS,
                self._finish_roblox_hover_nudge,
                hwnd, target
            )
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    def _finish_roblox_hover_nudge(
        self, hwnd: int, target: tuple[int, int]
    ) -> None:
        """Verify the pointer is back on target after the GUI hover refresh."""
        if not self._roblox_scroll_test_active:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window closed after the hover nudge.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError(
                    "Roblox lost focus after the hover nudge. Scrolling cancelled."
                )

            actual = tuple(int(value) for value in win32gui.GetCursorPos())
            if (
                abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX
                or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX
            ):
                self.append_log(
                    f"Hover nudge return needed correction: target={target}, actual={actual}."
                )
                win32api.SetCursorPos(target)
                actual = tuple(int(value) for value in win32gui.GetCursorPos())

            if (
                abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX
                or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX
            ):
                raise RuntimeError(
                    f"Cursor did not return to MAP SCROLL AREA: "
                    f"target={target}, actual={actual}. No scroll input was sent."
                )

            self.append_log(
                f"Relative hover nudge completed; cursor returned to {actual}. "
                f"Waiting {TEST_HOVER_SETTLE_DELAY_MS} ms before wheel input."
            )
            self.set_status(
                "Map GUI hover refreshed. Scrolling down (1/1)…", "progress"
            )
            self._send_roblox_test_scroll_notch(hwnd, 1, target)
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    def _send_roblox_test_scroll_notch(
        self, hwnd: int, notch_number: int, target: tuple[int, int]
    ) -> None:
        """Send one original global mouse-wheel input after focus/position checks."""
        if not self._roblox_scroll_test_active:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError("The Roblox window closed during the scroll test.")
            if int(win32gui.GetForegroundWindow() or 0) != hwnd:
                raise RuntimeError(
                    "Roblox lost focus. Remaining scroll input was cancelled."
                )

            actual = tuple(int(value) for value in win32gui.GetCursorPos())
            if (
                abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX
                or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX
            ):
                raise RuntimeError(
                    f"Cursor is no longer at the verified target: "
                    f"expected=({target[0]}, {target[1]}), "
                    f"actual=({actual[0]}, {actual[1]}). "
                    "Remaining scroll input was cancelled."
                )

            # Restore the v1.0.17 input path:
            # MOUSEEVENTF_WHEEL = 0x0800; WHEEL_DELTA = 120.
            self.append_log(
                f"Scroll notch {notch_number}: sending global mouse_event "
                "wheel input (-120/down) at the verified cursor position."
            )
            win32api.mouse_event(0x0800, 0, 0, -120, 0)

            self._finish_roblox_scroll_test(
                True,
                "Roblox focused; map-list area targeted; sent one downward wheel notch."
            )
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    def _finish_roblox_scroll_test(self, success: bool, message: str) -> None:
        """Restore the test button and report the result without hiding details."""
        self._roblox_scroll_test_active = False
        try:
            self.test_roblox_button.configure(state="normal")
        except (AttributeError, tk.TclError):
            pass

        if success:
            self.set_status(message, "success")
            self.append_log("Roblox scroll test completed: " + message)
        else:
            self.set_status("Roblox scroll test failed.", "error")
            self.append_log("Roblox scroll test failed: " + message)

    def _load_button_positions(self) -> dict[str, dict]:
        """Load saved positions, accepting only Roblox-client coordinate records."""
        try:
            with self.button_positions_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, dict):
                raise ValueError("Position file root must be an object.")
            if data.get("coordinate_space") != "roblox_client":
                raise ValueError("Position file uses an unsupported coordinate space.")
            raw_positions = data.get("positions", {})
            if not isinstance(raw_positions, dict):
                raise ValueError("Position records must be an object.")

            valid = {}
            for target, record in raw_positions.items():
                if not isinstance(target, str) or not isinstance(record, dict):
                    continue
                if target.startswith("MAP — "):
                    # Fixed map-card coordinates are obsolete; visual matching locates map cards.
                    continue
                x = record.get("x")
                y = record.get("y")
                width = record.get("client_width")
                height = record.get("client_height")
                if (
                    type(x) is int and type(y) is int
                    and type(width) is int and type(height) is int
                    and width > 0 and height > 0
                    and 0 <= x < width and 0 <= y < height
                ):
                    valid[target] = {
                        "x": x, "y": y,
                        "client_width": width, "client_height": height
                    }
            return valid
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # A damaged position file should not prevent the Macro from opening.
            return {}

    def _save_button_positions(self) -> None:
        """Atomically persist positions alongside the versioned Macro script."""
        payload = {
            "schema_version": 1,
            "coordinate_space": "roblox_client",
            "positions": self.button_positions
        }
        temporary_path = self.button_positions_path.with_suffix(".json.tmp")
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, self.button_positions_path)

    def _update_position_target_status(self, _event=None) -> None:
        """Show whether the currently selected lobby control has been captured."""
        if not hasattr(self, "position_status_var"):
            return
        target = self.position_target_var.get().strip()
        record = self.button_positions.get(target)
        active_targets = self._position_target_options()
        saved_count = sum(1 for name in active_targets if name in self.button_positions)
        total_count = len(active_targets)
        if record:
            detail = (
                f"Saved: ({record['x']}, {record['y']}) relative to Roblox. "
                f"{saved_count}/{total_count} positions recorded."
            )
        else:
            detail = (
                f"Not recorded yet. {saved_count}/{total_count} positions recorded."
            )
        self.position_status_var.set(detail)

    def toggle_position_capture(self) -> None:
        """Arm or cancel F3 mouse-position capture for the selected lobby control."""
        if self._position_capture_armed:
            self._stop_position_capture()
            self.set_status("Position capture cancelled.")
            return
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.set_status("Position capture requires Windows and pywin32.")
            self.append_log("Position capture could not start: Windows/pywin32 unavailable.")
            return
        target = self.position_target_var.get().strip()
        if target not in self._position_target_options():
            self.set_status("Choose a lobby control or scroll area to record first.")
            return
        if self._global_get_async_key_state is None:
            self.set_status("Global F3/F8 keyboard monitoring is unavailable.")
            return
        self._position_capture_armed = True
        self._position_f3_was_down = False
        self.position_capture_button.configure(text="CANCEL CAPTURE")
        self.set_status(
            f"F3 capture armed for {target}. Hover the Roblox control and press F3. F8 stops all."
        )
        self.append_log(f"F3 position capture armed for '{target}'.")

    def _stop_position_capture(self) -> None:
        """Disarm coordinate capture without affecting other running tasks."""
        self._position_capture_armed = False
        if hasattr(self, "position_capture_button"):
            try:
                self.position_capture_button.configure(text="ARM F3 CAPTURE")
            except tk.TclError:
                pass
        self._position_f3_was_down = False

    def _start_global_hotkey_polling(self) -> None:
        """Poll global F3/F8 so the hotkeys work while Roblox covers the GUI."""
        if os.name != "nt":
            self.append_log("Global F3/F8 hotkeys are available on Windows only.")
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_async_key_state = user32.GetAsyncKeyState
            get_async_key_state.argtypes = [ctypes.c_int]
            get_async_key_state.restype = ctypes.c_short
            self._global_get_async_key_state = get_async_key_state
        except Exception as exc:
            self.append_log(f"ERROR initializing global F3/F8 hotkeys: {exc}")
            return
        self._poll_global_hotkeys()

    def _poll_global_hotkeys(self) -> None:
        """Handle rising edges for F3 capture and F8 universal emergency stop."""
        if self._global_get_async_key_state is None:
            return
        try:
            f3_down = bool(int(self._global_get_async_key_state(0x72)) & 0x8000)
            f8_down = bool(int(self._global_get_async_key_state(0x77)) & 0x8000)
            if f8_down and not self._global_f8_was_down:
                self.universal_stop()
            elif f3_down and not self._position_f3_was_down:
                if self._position_capture_armed:
                    self._capture_selected_button_position()
            self._global_f8_was_down = f8_down
            self._position_f3_was_down = f3_down
        except Exception as exc:
            self.append_log(f"ERROR polling global F3/F8 hotkeys: {exc}")
            self._global_get_async_key_state = None
            return
        try:
            self._global_hotkey_after_id = self.root.after(
                GLOBAL_HOTKEY_POLL_MS, self._poll_global_hotkeys
            )
        except tk.TclError:
            self._global_hotkey_after_id = None

    def _capture_selected_button_position(self) -> None:
        """Capture the mouse inside Roblox and store client-relative coordinates."""
        try:
            if (
                self.docked_hwnd is not None
                and win32gui.IsWindow(self.docked_hwnd)
            ):
                hwnd = self.docked_hwnd
            else:
                hwnd = self._find_roblox_window()

            if hwnd is None or not win32gui.IsWindow(hwnd):
                self.set_status(
                    "Roblox window not found. Open Roblox and hover its button, then press F3."
                )
                return

            screen_x, screen_y = win32gui.GetCursorPos()
            client_x, client_y = win32gui.ScreenToClient(
                hwnd, (screen_x, screen_y)
            )
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            client_width = int(right - left)
            client_height = int(bottom - top)

            if not (
                0 <= client_x < client_width
                and 0 <= client_y < client_height
            ):
                self.set_status(
                    "Cursor is outside Roblox's game area. Hover the target button and press F3."
                )
                return

            target = self.position_target_var.get().strip()
            record = {
                "x": int(client_x),
                "y": int(client_y),
                "client_width": client_width,
                "client_height": client_height
            }
            previous_record = self.button_positions.get(target)
            self.button_positions[target] = record
            self._save_button_positions()
            self._stop_position_capture()
            self._update_position_target_status()

            action = "Updated" if previous_record else "Saved"
            self.set_status(
                f"{action} {target} at ({client_x}, {client_y}) in Roblox."
            )
            self.append_log(
                f"{action} position for '{target}': x={client_x}, y={client_y}, "
                f"client={client_width}x{client_height}."
            )
        except Exception as exc:
            self.set_status(f"Could not capture/save position: {exc}")
            self.append_log(f"ERROR capturing Roblox button position: {exc}")
            self._stop_position_capture()

    # ------------------------------------------------------------------
    # Ball Tower Defense lobby automation
    # ------------------------------------------------------------------
    def _validate_lobby_positions(self, map_name: str, mode: str) -> list[str]:
        required = []
        if mode == "test_choose_map":
            required.extend(["PLAY", "CREATE PARTY", "PRIVATE", "KICK", "START"])
        required.extend(["CHANGE MAP", "MAP SCROLL AREA"])
        return [key for key in required if key not in self.button_positions]

    def _begin_lobby_sequence(self, mode: str) -> None:
        if self._lobby_sequence_active or self._roblox_scroll_test_active:
            self.set_status("Another Roblox test or macro sequence is already running.", "warning")
            return
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.set_status("Lobby automation requires Windows and pywin32.", "error")
            return
        map_name = self.map_var.get().strip()
        if map_name not in MAP_OPTIONS:
            self.set_status("Choose a valid map before continuing.", "warning")
            return
        # This test sequence does not depend on a difficulty or recording assignment.
        reference_path = self._map_reference_path(map_name)
        if reference_path is None:
            message = (
                f"No reference image is installed for {map_name}. Open Map Detection "
                "and import a tightly cropped map-art image first."
            )
            self.set_status(message, "warning")
            self.append_log("Lobby sequence not started: " + message)
            return
        try:
            self._require_map_detection_libraries()
        except Exception as exc:
            self.set_status(f"Map detection is not ready: {exc}", "error")
            self.append_log(f"Lobby sequence not started because map detection is unavailable: {exc}")
            return

        missing = self._validate_lobby_positions(map_name, mode)
        if missing:
            message = "Missing F3 position(s): " + ", ".join(missing)
            self.set_status(message, "error")
            self.append_log(message + ". Capture each position in Roblox first.")
            return

        try:
            if self.docked_hwnd is not None and win32gui.IsWindow(self.docked_hwnd):
                hwnd = int(self.docked_hwnd)
            else:
                hwnd = self._find_roblox_window()
            if hwnd is None or not win32gui.IsWindow(hwnd):
                raise RuntimeError("Roblox window not found. Open Roblox first.")
            self._validate_map_detection_scroll_record(hwnd)

            self._lobby_sequence_active = True
            self._lobby_sequence_mode = mode
            self._lobby_sequence_hwnd = hwnd
            self._lobby_sequence_map = map_name
            self._lobby_sequence_kick_count = 0
            self.start_macro_button.configure(state="disabled")
            self.test_choose_map_button.configure(state="disabled")
            self.test_roblox_button.configure(state="disabled")
            self.stop_macro_button.configure(state="normal")

            self.set_status(
                "Preparing TEST CHOOSE MAP sequence…" if mode == "test_choose_map"
                else "Preparing selected-map test…", "progress"
            )
            self.append_log(
                f"Lobby sequence started: mode={mode}, map={map_name}; "
                "map selection will use live reference-image detection."
            )
            if mode == "test_choose_map":
                self.append_log(
                    "TEST CHOOSE MAP sequence: PLAY → CREATE PARTY → PRIVATE → 10 KICK clicks "
                    f"→ CHANGE MAP → detect {map_name} → START. START MACRO stays unassigned; "
                    "no recording playback is triggered."
                )
            else:
                self.append_log(
                    f"Map test: CHANGE MAP → visually locate {map_name}; "
                    "the game START control will not be clicked."
                )

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            self._schedule_lobby_sequence(180, self._lobby_wait_for_focus, hwnd, 5)
        except Exception as exc:
            self._finish_lobby_sequence(False, str(exc))

    def test_choose_map(self) -> None:
        """Run lobby setup, select the chosen map, and click the game START control."""
        self._begin_lobby_sequence("test_choose_map")

    def _schedule_lobby_sequence(self, delay_ms: int, callback, *args) -> None:
        if not self._lobby_sequence_active:
            return
        if self._lobby_sequence_after_id is not None:
            try:
                self.root.after_cancel(self._lobby_sequence_after_id)
            except tk.TclError:
                pass
            self._lobby_sequence_after_id = None

        def dispatch():
            self._lobby_sequence_after_id = None
            if not self._lobby_sequence_active:
                return
            try:
                callback(*args)
            except Exception as exc:
                self._finish_lobby_sequence(False, str(exc))

        self._lobby_sequence_after_id = self.root.after(max(1, int(delay_ms)), dispatch)

    def _lobby_require_focus(self, hwnd: int) -> None:
        if not self._lobby_sequence_active:
            raise RuntimeError("Lobby sequence was cancelled.")
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError("Roblox window closed during lobby automation.")
        if int(win32gui.GetForegroundWindow() or 0) != hwnd:
            raise RuntimeError("Roblox lost focus. Lobby automation stopped for safety.")

    def _lobby_wait_for_focus(self, hwnd: int, attempts_left: int) -> None:
        if not self._lobby_sequence_active:
            return
        if not win32gui.IsWindow(hwnd):
            self._finish_lobby_sequence(False, "Roblox window closed before the sequence began.")
            return
        if int(win32gui.GetForegroundWindow() or 0) != hwnd:
            if attempts_left <= 0:
                self._finish_lobby_sequence(
                    False, "Windows did not give Roblox focus. No lobby clicks were sent."
                )
                return
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            self._schedule_lobby_sequence(140, self._lobby_wait_for_focus, hwnd, attempts_left - 1)
            return
        if self._lobby_sequence_mode == "map_test":
            self._lobby_click_target(hwnd, "CHANGE MAP", self._lobby_after_change_map)
        else:
            self._lobby_click_target(hwnd, "PLAY", self._lobby_after_play)

    def _lobby_screen_point(self, hwnd: int, target_name: str) -> tuple[int, int]:
        record = self.button_positions.get(target_name)
        if not isinstance(record, dict):
            raise RuntimeError(f"Missing saved position: {target_name}.")
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width, height = int(right - left), int(bottom - top)
        saved_width = int(record.get("client_width", 0))
        saved_height = int(record.get("client_height", 0))
        if width <= 0 or height <= 0 or saved_width <= 0 or saved_height <= 0:
            raise RuntimeError(f"Invalid client dimensions for saved position {target_name}.")
        x = round(int(record["x"]) * width / saved_width)
        y = round(int(record["y"]) * height / saved_height)
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        sx, sy = win32gui.ClientToScreen(hwnd, (x, y))
        return int(sx), int(sy)

    def _lobby_move_to_target(self, hwnd: int, target_name: str, callback, *callback_args) -> None:
        self._lobby_require_focus(hwnd)
        target = self._lobby_screen_point(hwnd, target_name)
        start = tuple(int(v) for v in win32gui.GetCursorPos())
        self.append_log(f"Moving to {target_name}: start={start}, target={target}.")
        self._lobby_cursor_move_step(hwnd, target_name, start, target, 1, callback, callback_args)

    def _lobby_cursor_move_step(
        self, hwnd: int, target_name: str, start: tuple[int, int],
        target: tuple[int, int], step: int, callback, callback_args: tuple
    ) -> None:
        self._lobby_require_focus(hwnd)
        progress = min(1.0, step / TEST_CURSOR_MOVE_STEPS)
        eased = progress * progress * (3.0 - 2.0 * progress)
        x = round(start[0] + (target[0] - start[0]) * eased)
        y = round(start[1] + (target[1] - start[1]) * eased)
        win32api.SetCursorPos((int(x), int(y)))
        if step < TEST_CURSOR_MOVE_STEPS:
            interval = max(1, round(TEST_CURSOR_MOVE_DURATION_MS / TEST_CURSOR_MOVE_STEPS))
            self._schedule_lobby_sequence(
                interval, self._lobby_cursor_move_step,
                hwnd, target_name, start, target, step + 1, callback, callback_args
            )
            return
        actual = tuple(int(v) for v in win32gui.GetCursorPos())
        if abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX:
            raise RuntimeError(f"Cursor missed {target_name}: expected={target}, actual={actual}.")
        self._send_relative_mouse_move(TEST_HOVER_NUDGE_PX, 0)
        self._schedule_lobby_sequence(
            TEST_HOVER_NUDGE_OUT_DELAY_MS, self._lobby_return_cursor_from_nudge,
            hwnd, target_name, target, callback, callback_args
        )

    def _lobby_return_cursor_from_nudge(
        self, hwnd: int, target_name: str, target: tuple[int, int],
        callback, callback_args: tuple
    ) -> None:
        self._lobby_require_focus(hwnd)
        after_nudge = tuple(int(v) for v in win32gui.GetCursorPos())
        dx, dy = after_nudge[0] - target[0], after_nudge[1] - target[1]
        if abs(dx) > 20 or abs(dy) > 20:
            raise RuntimeError(f"Unexpected cursor displacement at {target_name}: target={target}, actual={after_nudge}.")
        if dx or dy:
            self._send_relative_mouse_move(-dx, -dy)
        else:
            self._send_relative_mouse_move(-TEST_HOVER_NUDGE_PX, 0)
        self._schedule_lobby_sequence(
            TEST_HOVER_SETTLE_DELAY_MS, self._lobby_hover_ready,
            hwnd, target_name, target, callback, callback_args
        )

    def _lobby_hover_ready(
        self, hwnd: int, target_name: str, target: tuple[int, int],
        callback, callback_args: tuple
    ) -> None:
        self._lobby_require_focus(hwnd)
        actual = tuple(int(v) for v in win32gui.GetCursorPos())
        if abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX:
            win32api.SetCursorPos(target)
            actual = tuple(int(v) for v in win32gui.GetCursorPos())
        if abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX:
            raise RuntimeError(f"Cursor did not settle over {target_name}: expected={target}, actual={actual}.")
        callback(hwnd, target, *callback_args)

    def _lobby_click_target(self, hwnd: int, target_name: str, after_click, *callback_args) -> None:
        self.set_status(f"Moving to {target_name}…", "progress")
        self._lobby_move_to_target(hwnd, target_name, self._lobby_click_ready, after_click, callback_args)

    def _lobby_click_ready(
        self, hwnd: int, target: tuple[int, int], after_click, callback_args: tuple
    ) -> None:
        self._lobby_require_focus(hwnd)
        actual = tuple(int(v) for v in win32gui.GetCursorPos())
        if abs(actual[0] - target[0]) > TEST_CURSOR_POSITION_TOLERANCE_PX or abs(actual[1] - target[1]) > TEST_CURSOR_POSITION_TOLERANCE_PX:
            raise RuntimeError("Cursor moved away from the target before clicking.")
        win32api.mouse_event(0x0002, 0, 0, 0, 0)
        self._lobby_mouse_down = True
        self._schedule_lobby_sequence(LOBBY_CLICK_HOLD_MS, self._lobby_release_click, hwnd, target, after_click, callback_args)

    def _lobby_release_click(
        self, hwnd: int, target: tuple[int, int], after_click, callback_args: tuple
    ) -> None:
        if self._lobby_mouse_down:
            win32api.mouse_event(0x0004, 0, 0, 0, 0)
            self._lobby_mouse_down = False
        self.append_log(f"Clicked Roblox coordinate {target}.")
        self._schedule_lobby_sequence(LOBBY_ACTION_DELAY_MS, after_click, hwnd, *callback_args)

    def _lobby_after_play(self, hwnd: int) -> None:
        self._lobby_click_target(hwnd, "CREATE PARTY", self._lobby_after_create_party)

    def _lobby_after_create_party(self, hwnd: int) -> None:
        self._lobby_click_target(hwnd, "PRIVATE", self._lobby_after_private)

    def _lobby_after_private(self, hwnd: int) -> None:
        self._lobby_sequence_kick_count = 0
        self.set_status("Checking party members…", "progress")
        self._lobby_kick_next(hwnd)

    def _lobby_kick_next(self, hwnd: int) -> None:
        if self._lobby_sequence_kick_count >= KICK_MAX_ATTEMPTS:
            self._lobby_after_kicks(hwnd)
            return
        next_click = self._lobby_sequence_kick_count + 1
        self.set_status(
            f"Kicking party ({next_click}/{KICK_MAX_ATTEMPTS})…", "progress"
        )
        self._lobby_move_to_target(hwnd, "KICK", self._lobby_kick_button_ready)

    def _lobby_kick_button_ready(
        self, hwnd: int, target: tuple[int, int]
    ) -> None:
        self._lobby_click_ready(hwnd, target, self._lobby_after_kick, ())

    def _lobby_after_kick(self, hwnd: int) -> None:
        self._lobby_sequence_kick_count += 1
        self.append_log(
            f"KICK click {self._lobby_sequence_kick_count}/"
            f"{KICK_MAX_ATTEMPTS} completed."
        )
        if self._lobby_sequence_kick_count >= KICK_MAX_ATTEMPTS:
            self._lobby_after_kicks(hwnd)
            return
        self._lobby_kick_next(hwnd)

    def _lobby_after_kicks(self, hwnd: int) -> None:
        self.append_log(f"KICK loop ended after {self._lobby_sequence_kick_count} attempt(s).")
        self._lobby_click_target(hwnd, "CHANGE MAP", self._lobby_after_change_map)

    def _lobby_after_change_map(self, hwnd: int) -> None:
        map_name = self._lobby_sequence_map
        if not map_name:
            raise RuntimeError("No map is selected for detection.")
        self.set_status(f"Expanding Roblox to locate {map_name}…", "progress")
        self._expand_roblox_for_map_detection(hwnd)
        self._schedule_lobby_sequence(
            MAP_DETECTION_EXPAND_SETTLE_MS,
            self._lobby_begin_map_detection,
            hwnd,
            map_name
        )

    def _lobby_begin_map_detection(self, hwnd: int, map_name: str) -> None:
        """Reset the list to its top before searching with a visual reference."""
        if self._map_reference_path(map_name) is None:
            raise RuntimeError(
                f"No map reference image is installed for {map_name}. "
                "Import a cropped map-art image in the Map Detection tab."
            )
        self._require_map_detection_libraries()
        self._lobby_require_focus(hwnd)
        scroll_point = self._map_detection_scroll_point(hwnd)
        win32api.SetCursorPos(scroll_point)
        self.append_log(
            f"Map detection for {map_name}: resetting the list with "
            f"{MAP_DETECTION_RESET_NOTCHES} upward wheel notches."
        )
        self._lobby_reset_map_list(hwnd, map_name, scroll_point, 0)

    def _lobby_reset_map_list(
        self, hwnd: int, map_name: str,
        scroll_point: tuple[int, int], completed: int
    ) -> None:
        self._lobby_require_focus(hwnd)
        if completed >= MAP_DETECTION_RESET_NOTCHES:
            self._schedule_lobby_sequence(
                MAP_DETECTION_SETTLE_MS,
                self._lobby_detect_selected_map,
                hwnd, map_name, scroll_point, 0
            )
            return
        win32api.mouse_event(0x0800, 0, 0, 120, 0)
        self._schedule_lobby_sequence(
            MAP_DETECTION_SCROLL_INTERVAL_MS,
            self._lobby_reset_map_list,
            hwnd, map_name, scroll_point, completed + 1
        )

    def _lobby_detect_selected_map(
        self, hwnd: int, map_name: str,
        scroll_point: tuple[int, int], down_notches: int
    ) -> None:
        """Search screenshots and refuse to click low-confidence map matches."""
        self._lobby_require_focus(hwnd)
        screenshot, origin, metrics = self._capture_roblox_client_image(hwnd)
        match = self._match_map_reference(map_name, screenshot)
        if (
            match is not None
            and match["score"] >= MAP_DETECTION_THRESHOLD
            and match.get("margin", 0.0) >= MAP_DETECTION_MIN_MARGIN
        ):
            center = match["center"]
            screen_target = (origin[0] + center[0], origin[1] + center[1])
            self.append_log(
                f"Map detector found {map_name}: confidence={match['score']:.3f}, "
                f"separation={match['margin']:.3f}, client_box={match['box']}."
            )
            self.set_status(
                f"{map_name} detected ({match['score']:.0%}); selecting…",
                "success"
            )
            self._lobby_move_to_detected_map(hwnd, map_name, screen_target)
            return

        score_text = (f"{match['score']:.3f} (margin {match.get('margin', 0.0):.3f})"
                      if match is not None else "no match")
        self.append_log(
            f"Map scan {down_notches + 1}: {map_name} confidence={score_text}."
        )
        if down_notches >= MAP_DETECTION_MAX_DOWN_NOTCHES:
            raise RuntimeError(
                f"Could not uniquely identify {map_name} after scanning the map list "
                f"(best candidate: {score_text}). No map was clicked. Check its reference image."
            )
        win32api.SetCursorPos(scroll_point)
        win32api.mouse_event(0x0800, 0, 0, -120, 0)
        self.set_status(
            f"Searching for {map_name} ({down_notches + 1}/"
            f"{MAP_DETECTION_MAX_DOWN_NOTCHES + 1})…", "progress"
        )
        self._schedule_lobby_sequence(
            MAP_DETECTION_SETTLE_MS,
            self._lobby_detect_selected_map,
            hwnd, map_name, scroll_point, down_notches + 1
        )

    def _lobby_move_to_detected_map(
        self, hwnd: int, map_name: str,
        screen_target: tuple[int, int]
    ) -> None:
        self._lobby_require_focus(hwnd)
        start = tuple(int(value) for value in win32gui.GetCursorPos())
        target = tuple(int(value) for value in screen_target)
        self.append_log(
            f"Moving to detected {map_name} card center: start={start}, target={target}."
        )
        self._lobby_cursor_move_step(
            hwnd, f"detected {map_name}", start, target, 1,
            self._lobby_click_ready,
            (self._lobby_after_map_selected, ())
        )

    def _lobby_after_map_selected(self, hwnd: int) -> None:
        self._restore_roblox_preview_window()
        if self._lobby_sequence_mode == "map_test":
            self._finish_lobby_sequence(
                True,
                f"Map detection test completed: detected and clicked {self._lobby_sequence_map}."
            )
            return
        self._schedule_lobby_sequence(
            300, self._lobby_click_start_after_map_restore, hwnd
        )

    def _lobby_click_start_after_map_restore(self, hwnd: int) -> None:
        self._lobby_click_target(hwnd, "START", self._lobby_after_game_start)

    def _lobby_after_game_start(self, hwnd: int) -> None:
        self._finish_lobby_sequence(
            True,
            f"Lobby sequence completed for {self._lobby_sequence_map}; game START was clicked. Recording playback was not started."
        )

    def _finish_lobby_sequence(self, success: bool, message: str) -> None:
        self._lobby_sequence_active = False
        self._restore_roblox_preview_window()
        if self._lobby_sequence_after_id is not None:
            try:
                self.root.after_cancel(self._lobby_sequence_after_id)
            except tk.TclError:
                pass
            self._lobby_sequence_after_id = None
        if self._lobby_mouse_down:
            try:
                win32api.mouse_event(0x0004, 0, 0, 0, 0)
            except Exception:
                pass
            self._lobby_mouse_down = False
        for button_name in ("start_macro_button", "test_choose_map_button", "test_roblox_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                try:
                    button.configure(state="normal")
                except tk.TclError:
                    pass
        if success:
            self.set_status(message, "success")
            self.append_log("Lobby automation completed: " + message)
        else:
            self.set_status("Lobby automation stopped: " + message, "error")
            self.append_log("Lobby automation stopped: " + message)
        self._lobby_sequence_mode = None
        self._lobby_sequence_hwnd = None
        self._lobby_sequence_map = None
    def start_macro(self) -> None:
        """Intentionally unassigned: clicking START MACRO must do nothing."""
        return

    def stop_macro(self) -> None:
        self.universal_stop()

    def _release_macro_inputs(self) -> None:
        """Release only inputs explicitly tracked as held by this Macro."""
        if os.name != "nt" or not PYWIN32_AVAILABLE:
            return
        if self._lobby_mouse_down:
            try:
                win32api.mouse_event(0x0004, 0, 0, 0, 0)
            except Exception:
                pass
            self._lobby_mouse_down = False
        for virtual_key in tuple(self._macro_held_keys):
            try:
                win32api.keybd_event(int(virtual_key), 0, 0x0002, 0)
            except Exception:
                pass
            self._macro_held_keys.discard(virtual_key)

    def universal_stop(self) -> None:
        """F8 emergency stop: cancel automation, disarm capture, release inputs."""
        stopped = []
        was_preview_expanded = self._map_preview_original_rect is not None
        was_big_screen_mode = self._big_screen_mode
        self._stop_position_capture()
        if self._lobby_sequence_active:
            self._finish_lobby_sequence(False, "Emergency STOP pressed (F8).")
            stopped.append("lobby sequence")
        if self._roblox_scroll_test_active:
            self._finish_roblox_scroll_test(False, "Emergency STOP pressed (F8).")
            stopped.append("Roblox scroll test")
        if self._map_detection_test_active:
            self._finish_map_detection_test(
                "Map detection test cancelled by emergency STOP (F8).",
                success=False
            )
            stopped.append("map detection test")
        self._release_macro_inputs()
        self._restore_roblox_preview_window()
        if was_big_screen_mode and self._restore_big_screen_mode():
            stopped.append("expanded Roblox screen")
        if was_preview_expanded and not stopped:
            stopped.append("expanded Roblox preview")
        detail = ", ".join(stopped) if stopped else "no active task"
        self.set_status(f"UNIVERSAL STOP (F8) — stopped {detail}.", "warning")
        self.append_log(
            f"UNIVERSAL STOP (F8): stopped {detail}; held Macro inputs released and Roblox restored."
        )

    # ------------------------------------------------------------------
    # Macro and recording controls
    # ------------------------------------------------------------------
    def start_recording(self) -> None:
        name = self.recording_name_var.get().strip()
        if not name:
            self.set_status("Enter a recording name first.")
            self.append_log("Start recording requested without a name.")
            return
        self.set_status("Recorder engine not connected yet — no inputs are being captured.")
        self.append_log(f'Start recording requested for "{name}". Capture is not connected.')

    def end_recording(self) -> None:
        self.set_status("Recorder engine not connected; there is no active capture to end.")
        self.append_log("End recording button pressed; no recording was active.")

    def save_recording(self) -> None:
        name = self.recording_name_var.get().strip()
        if not name:
            self.set_status("Enter a recording name before saving.")
            self.append_log("Save recording requested without a name.")
            return
        self.set_status("Cannot save yet: the recording engine has not captured any actions.")
        self.append_log(f'Save requested for "{name}", but no recording data exists.')

    def _selected_recording_path(self) -> Path | None:
        selected = self.recordings_tree.selection()
        if not selected:
            return None
        name = self.recordings_tree.item(selected[0], "values")[0]
        path = self.recordings_dir / f"{name}.json"
        return path if path.is_file() else None

    def delete_recording(self) -> None:
        path = self._selected_recording_path()
        if path is None:
            self.set_status("Select a saved recording to delete.")
            return
        if not messagebox.askyesno("Delete recording", f"Delete '{path.stem}'?", parent=self.root):
            return
        try:
            path.unlink()
            self.refresh_recordings()
            self.set_status(f"Deleted recording: {path.stem}")
            self.append_log(f"Deleted recording file: {path.name}")
        except OSError as exc:
            self.set_status(f"Could not delete recording: {exc}")
            self.append_log(f"ERROR deleting {path.name}: {exc}")

    def load_recording(self) -> None:
        path = self._selected_recording_path()
        if path is None:
            self.set_status("Select a saved recording to load.")
            return
        try:
            with path.open("r", encoding="utf-8") as file:
                json.load(file)
            self.currently_loaded_recording = path.stem
            self.recording_name_var.set(path.stem)
            self.set_status(f"Loaded recording metadata: {path.stem}. Playback engine not connected.")
            self.append_log(f"Loaded recording metadata from {path.name}.")
        except (OSError, json.JSONDecodeError) as exc:
            self.set_status(f"Could not load recording: {exc}")
            self.append_log(f"ERROR loading {path.name}: {exc}")

    def playback_recording(self) -> None:
        selected = self._selected_recording_path()
        if selected is None and self.currently_loaded_recording is None:
            self.set_status("Select or load a recording before playback.")
            return
        self.set_status("Playback engine not connected yet — no actions were replayed.")
        self.append_log("Playback requested; playback engine is not connected.")

    def stop_playback(self) -> None:
        self.set_status("Playback stop requested — no playback is currently running.")
        self.append_log("Stop playback button pressed.")

    def refresh_recordings(self) -> None:
        if not hasattr(self, "recordings_tree"):
            return
        for item in self.recordings_tree.get_children():
            self.recordings_tree.delete(item)

        self.recording_names = sorted(
            path.stem for path in self.recordings_dir.glob("*.json")
            if path.is_file()
        )
        for name in self.recording_names:
            self.recordings_tree.insert("", "end", values=(name,))

        choices = ["Choose a Recording"] + self.recording_names
        self.recording_combo.configure(values=choices)
        if self.macro_recording_var.get() not in choices:
            self.macro_recording_var.set("Choose a Recording")
        self.append_log(f"Recording list refreshed: {len(self.recording_names)} saved recording(s).")

    def _on_recording_selected(self, _event=None) -> None:
        path = self._selected_recording_path()
        if path is not None:
            self.recording_name_var.set(path.stem)
            self.macro_recording_var.set(path.stem)

    # ------------------------------------------------------------------
    # GitHub automatic updater
    # ------------------------------------------------------------------
    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int, int]:
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
        if not match:
            raise ValueError(f"Unsupported version format: {value!r}")
        return tuple(int(part) for part in match.groups())

    def check_for_updates(self) -> None:
        if self._update_check_running:
            return
        self._update_check_running = True
        self.update_button.configure(text="CHECKING...", state="disabled")
        self.append_log("Checking GitHub for a BTD Macro update.")
        threading.Thread(
            target=self._check_for_updates_worker,
            name="BTDUpdateCheck",
            daemon=True
        ).start()

    def _check_for_updates_worker(self) -> None:
        try:
            request = urllib.request.Request(
                GITHUB_LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"BTD-Macro/{APP_VERSION}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read(2_000_000).decode("utf-8"))

            latest_tag = str(payload.get("tag_name", "")).strip()
            latest_version = self._version_tuple(latest_tag)
            current_version = self._version_tuple(APP_VERSION)
            if latest_version <= current_version:
                result = {"available": False, "version": latest_tag}
            else:
                expected_name = f"BTD_Macro_{latest_tag.lstrip('v')}.py"
                assets = payload.get("assets") or []
                asset = next(
                    (item for item in assets
                     if item.get("name") == expected_name
                     and item.get("state") == "uploaded"),
                    None
                )
                if asset is None:
                    raise RuntimeError(
                        f"GitHub release {latest_tag} has no uploaded {expected_name} asset."
                    )
                download_url = str(asset.get("browser_download_url", ""))
                expected_prefix = (
                    "https://github.com/"
                    f"{GITHUB_REPOSITORY}/releases/download/{latest_tag}/"
                )
                if not download_url.startswith(expected_prefix):
                    raise RuntimeError("The release asset download URL was not trusted.")
                result = {
                    "available": True,
                    "version": latest_tag.lstrip("v"),
                    "tag": latest_tag,
                    "asset": asset,
                    "name": expected_name,
                }
        except Exception as exc:
            result = {"error": str(exc)}

        try:
            self.root.after(0, self._apply_update_check_result, result)
        except tk.TclError:
            pass

    def _apply_update_check_result(self, result: dict) -> None:
        self._update_check_running = False
        try:
            self.update_button.configure(state="normal")
        except tk.TclError:
            return

        if result.get("error"):
            self.update_button.configure(
                text="CHECK AGAIN", command=self.check_for_updates
            )
            self.set_status("Update check unavailable.")
            self.append_log(f"Update check failed: {result['error']}")
            return

        if not result.get("available"):
            self._update_release = None
            self.update_button.configure(
                text="CHECK UPDATES", command=self.check_for_updates
            )
            self.set_status("Idle — application is up to date.")
            self.append_log("No newer BTD Macro release is available.")
            return

        self._update_release = result
        self.update_button.configure(
            text="UPDATE AVAILABLE", command=self._show_update_dialog,
            bg=SUCCESS, fg="#101522", activebackground=SUCCESS
        )
        self.set_status(f"Update available: version {result['version']}.")
        self.append_log(f"GitHub update available: BTD Macro {result['version']}.")
        self._show_update_dialog()

    def _show_update_dialog(self) -> None:
        if not self._update_release:
            self.check_for_updates()
            return
        if self._update_dialog is not None:
            try:
                if self._update_dialog.winfo_exists():
                    self._update_dialog.lift()
                    return
            except tk.TclError:
                pass

        version = self._update_release["version"]
        dialog = tk.Toplevel(self.root)
        self._update_dialog = dialog
        dialog.title("BTD Macro Update Available")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.geometry("390x190")
        tk.Label(
            dialog, text="UPDATE AVAILABLE", bg=BG, fg=SUCCESS,
            font=("Segoe UI Semibold", 13)
        ).pack(anchor="w", padx=20, pady=(18, 5))
        tk.Label(
            dialog, text=f"BTD Macro {version} is ready to install.",
            bg=BG, fg=TEXT, font=("Segoe UI", 10)
        ).pack(anchor="w", padx=20)
        tk.Label(
            dialog,
            text="Your recordings and settings are kept in place.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=20, pady=(5, 15))
        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill="x", padx=20, pady=(0, 16))
        self._make_button(
            buttons, "UPDATE NOW", self._begin_update_download,
            bg=SUCCESS, fg="#101522"
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self._make_button(
            buttons, "LATER", dialog.destroy,
            bg=PANEL_ALT, fg=TEXT
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))
        dialog.grab_set()
        dialog.focus_force()

    def _begin_update_download(self) -> None:
        if not self._update_release or self._update_download_running:
            return
        if self._update_dialog is not None:
            try:
                self._update_dialog.destroy()
            except tk.TclError:
                pass
            self._update_dialog = None

        self._update_download_running = True
        self.update_button.configure(
            text="DOWNLOADING...", state="disabled",
            bg=PANEL_ALT, fg=TEXT
        )
        self.set_status("Downloading and verifying the update...")
        self.append_log("Downloading the selected GitHub release asset.")
        threading.Thread(
            target=self._download_update_worker,
            args=(self._update_release,),
            name="BTDUpdateDownload",
            daemon=True
        ).start()

    def _download_update_worker(self, release_info: dict) -> None:
        pending_path: str | None = None
        try:
            asset = release_info["asset"]
            request = urllib.request.Request(
                asset["browser_download_url"],
                headers={"User-Agent": f"BTD-Macro/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_UPDATE_SIZE + 1)
            if not data or len(data) > MAX_UPDATE_SIZE:
                raise RuntimeError("The update file is empty or larger than the safety limit.")
            expected_size = asset.get("size")
            if expected_size is not None and int(expected_size) != len(data):
                raise RuntimeError("The downloaded file size did not match GitHub's release data.")

            digest = str(asset.get("digest") or "")
            if digest.startswith("sha256:"):
                actual_digest = hashlib.sha256(data).hexdigest()
                if actual_digest.lower() != digest.split(":", 1)[1].lower():
                    raise RuntimeError("The downloaded file failed its SHA-256 integrity check.")

            source = data.decode("utf-8-sig")
            compile(source, release_info["name"], "exec")
            tree = ast.parse(source, filename=release_info["name"])
            found_version = None
            found_title = None
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "APP_VERSION":
                            found_version = ast.literal_eval(node.value)
                        elif isinstance(target, ast.Name) and target.id == "APP_TITLE":
                            found_title = ast.literal_eval(node.value)
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name) and node.target.id == "APP_VERSION":
                        found_version = ast.literal_eval(node.value)
                    elif isinstance(node.target, ast.Name) and node.target.id == "APP_TITLE":
                        found_title = ast.literal_eval(node.value)

            expected_version = release_info["version"]
            if found_version != expected_version or found_title != f"BTD Macro {expected_version}":
                raise RuntimeError("The downloaded script's internal version does not match its release.")

            target = Path(__file__).resolve()
            handle, pending_path = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=".update.py",
                dir=str(target.parent)
            )
            with os.fdopen(handle, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except Exception as exc:
            if pending_path:
                try:
                    Path(pending_path).unlink(missing_ok=True)
                except OSError:
                    pass
            pending_path = None
            error = str(exc)
        else:
            error = None

        try:
            self.root.after(0, self._on_update_downloaded, pending_path, error)
        except tk.TclError:
            if pending_path:
                try:
                    Path(pending_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _on_update_downloaded(
        self, pending_path: str | None, error: str | None
    ) -> None:
        self._update_download_running = False
        if error or not pending_path:
            self.update_button.configure(
                text="CHECK UPDATES", state="normal",
                command=self.check_for_updates, bg=PANEL_ALT, fg=TEXT
            )
            self.set_status("Update download failed; your current version is unchanged.")
            self.append_log(f"Update installation was not started: {error or 'unknown error'}")
            messagebox.showerror(
                "Update failed",
                f"BTD Macro could not download or verify the update.\n\n{error or 'Unknown error'}",
                parent=self.root
            )
            return

        if self.docked_hwnd is not None and not self.undock_roblox():
            Path(pending_path).unlink(missing_ok=True)
            self.update_button.configure(state="normal", text="UPDATE AVAILABLE")
            messagebox.showerror(
                "Update cancelled",
                "Roblox could not be safely restored. Undock Roblox and try again.",
                parent=self.root
            )
            return

        old_target = Path(__file__).resolve()
        new_target = old_target.with_name(self._update_release["name"])
        if old_target == new_target:
            Path(pending_path).unlink(missing_ok=True)
            messagebox.showerror(
                "Update failed",
                "The downloaded release points to the currently running filename.",
                parent=self.root
            )
            return

        process_id = os.getpid()
        helper = Path(tempfile.gettempdir()) / f"btd_macro_updater_{process_id}.py"
        startup_marker = (
            Path(tempfile.gettempdir())
            / f"btd_macro_startup_{process_id}_{self._update_release['version']}.txt"
        )
        existing_target_backup = new_target.with_name(
            f".{new_target.name}.{process_id}.previous"
        )
        expected_version = self._update_release["version"]

        helper_source = r'''import ctypes
import json
import os
import subprocess
import sys
import time

download_path, old_target_path, new_target_path, existing_backup_path, marker_path, parent_pid, python_exe, args_json, working_dir, expected_version = sys.argv[1:11]
parent_pid = int(parent_pid)

def wait_for_parent():
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if handle:
            try:
                kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            finally:
                kernel.CloseHandle(handle)
            return
    while True:
        try:
            os.kill(parent_pid, 0)
            time.sleep(0.25)
        except OSError:
            return

def notify(title, message, error=False):
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(
                None, message, title, 0x10 if error else 0x40
            )
        except Exception:
            pass

def launch_options(python_exe):
    if os.name == "nt":
        if os.path.basename(python_exe).lower() == "pythonw.exe":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            return 0, startup
        return getattr(subprocess, "CREATE_NO_WINDOW", 0), None
    return 0, None

def remove_with_retry(path, attempts=20):
    for _ in range(attempts):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.25)
    return False

wait_for_parent()
args = json.loads(args_json)
moved_existing_target = False
installed_new_target = False
new_process = None
try:
    # Preserve an already-existing copy of the target version until the new
    # copy has passed its startup check.
    if os.path.exists(new_target_path):
        if os.path.exists(existing_backup_path):
            raise RuntimeError("A previous update-recovery file already exists.")
        os.replace(new_target_path, existing_backup_path)
        moved_existing_target = True

    os.replace(download_path, new_target_path)
    installed_new_target = True

    launch_env = os.environ.copy()
    launch_env["BTD_MACRO_UPDATE_STARTUP_MARKER"] = marker_path
    launch_env["BTD_MACRO_EXPECTED_VERSION"] = expected_version
    launch_flags, launch_startupinfo = launch_options(python_exe)
    new_process = subprocess.Popen(
        [python_exe, new_target_path, *args],
        cwd=working_dir,
        env=launch_env,
        close_fds=True,
        creationflags=launch_flags,
        startupinfo=launch_startupinfo
    )

    # The new app writes its marker only after the Tk interface has been
    # constructed and its event loop has started.
    deadline = time.monotonic() + 35.0
    confirmed = False
    while time.monotonic() < deadline:
        if new_process.poll() is not None:
            raise RuntimeError("The new application exited before confirming startup.")
        if os.path.isfile(marker_path):
            try:
                with open(marker_path, "r", encoding="utf-8") as marker_file:
                    marker_version = marker_file.read().strip()
            except OSError:
                marker_version = ""
            if marker_version == expected_version:
                time.sleep(2.5)
                if new_process.poll() is not None:
                    raise RuntimeError("The new application exited immediately after startup.")
                confirmed = True
                break
        time.sleep(0.25)

    if not confirmed:
        raise RuntimeError("The new application did not confirm startup within 35 seconds.")

    # Only now, after the new version has stayed alive, remove the script that
    # launched this update. The recordings folder and other app data are untouched.
    old_deleted = True
    if os.path.abspath(old_target_path) != os.path.abspath(new_target_path):
        old_deleted = remove_with_retry(old_target_path)

    if moved_existing_target:
        remove_with_retry(existing_backup_path)

    if old_deleted:
        notify(
            "BTD Macro Updated",
            "BTD Macro " + expected_version + " is now running.\n\n"
            "The previous version's Python file was removed. Your recordings and "
            "other data were kept."
        )
    else:
        notify(
            "BTD Macro Updated",
            "BTD Macro " + expected_version + " is now running.\n\n"
            "The previous version could not be deleted automatically, so its "
            "Python file was left in place. Your recordings and other data were kept."
        )

except Exception as exc:
    rollback_errors = []

    if new_process is not None and new_process.poll() is None:
        try:
            new_process.terminate()
            new_process.wait(timeout=5)
        except Exception:
            try:
                new_process.kill()
            except Exception:
                pass

    if installed_new_target:
        if not remove_with_retry(new_target_path):
            rollback_errors.append("Could not remove the failed new-version file.")

    if moved_existing_target and os.path.exists(existing_backup_path):
        try:
            os.replace(existing_backup_path, new_target_path)
        except Exception as rollback_error:
            rollback_errors.append("Could not restore the pre-existing target version: " + str(rollback_error))

    if rollback_errors:
        notify(
            "BTD Macro Update Recovery Needs Attention",
            "The update failed: " + str(exc) + "\n\n"
            + "\n".join(rollback_errors)
            + "\n\nThe previous running version was not intentionally deleted.",
            True
        )
    else:
        notify(
            "BTD Macro Update Failed",
            "The new version could not be started. The previous version was kept.\n\n"
            + str(exc),
            True
        )
        try:
            rollback_env = os.environ.copy()
            rollback_env.pop("BTD_MACRO_UPDATE_STARTUP_MARKER", None)
            rollback_env.pop("BTD_MACRO_EXPECTED_VERSION", None)
            rollback_flags, rollback_startupinfo = launch_options(python_exe)
            subprocess.Popen(
                [python_exe, old_target_path, *args],
                cwd=working_dir,
                env=rollback_env,
                close_fds=True,
                creationflags=rollback_flags,
                startupinfo=rollback_startupinfo
            )
        except Exception as restart_error:
            notify(
                "BTD Macro Restart Failed",
                "The previous file is still present, but it could not be reopened automatically.\n\n"
                + str(restart_error),
                True
            )

finally:
    remove_with_retry(download_path)
    remove_with_retry(marker_path)
    try:
        os.remove(__file__)
    except OSError:
        pass
'''

        try:
            helper.write_text(helper_source, encoding="utf-8")
            creation_flags = 0
            startupinfo = None
            if os.name == "nt":
                creation_flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            subprocess.Popen(
                [
                    sys.executable, str(helper), pending_path,
                    str(old_target), str(new_target), str(existing_target_backup),
                    str(startup_marker), str(process_id), get_gui_python_executable(),
                    json.dumps(sys.argv[1:]), str(Path.cwd()), expected_version
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creation_flags,
                startupinfo=startupinfo
            )
        except Exception as exc:
            Path(pending_path).unlink(missing_ok=True)
            self.update_button.configure(
                text="UPDATE AVAILABLE", state="normal",
                command=self._show_update_dialog
            )
            self.set_status("Could not start the safe update installer.")
            messagebox.showerror(
                "Update failed",
                f"Could not start the update helper. Your current version is unchanged.\n\n{exc}",
                parent=self.root
            )
            return

        self.set_status(f"Installing BTD Macro {expected_version}...")
        self.append_log(
            f"Verified update staged as {new_target.name}; the updater will launch it, "
            "confirm startup, then remove the previous script."
        )
        self.root.destroy()

    # ------------------------------------------------------------------
    # Roblox child-window embedding (Windows + pywin32)
    # ------------------------------------------------------------------
    def toggle_dock(self) -> None:
        if self.docked_hwnd is not None:
            self.undock_roblox()
        else:
            self.dock_roblox()

    @staticmethod
    def _get_window_process_name(hwnd: int) -> str | None:
        """Return the executable name for a window, without requiring psutil."""
        if os.name != "nt":
            return None
        try:
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            get_window_thread_process_id = user32.GetWindowThreadProcessId
            get_window_thread_process_id.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
            ]
            get_window_thread_process_id.restype = wintypes.DWORD

            process_id = wintypes.DWORD()
            if not get_window_thread_process_id(
                wintypes.HWND(hwnd), ctypes.byref(process_id)
            ):
                return None

            open_process = kernel32.OpenProcess
            open_process.argtypes = [
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
            ]
            open_process.restype = wintypes.HANDLE

            query_image = kernel32.QueryFullProcessImageNameW
            query_image.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD)
            ]
            query_image.restype = wintypes.BOOL

            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL

            # PROCESS_QUERY_LIMITED_INFORMATION works without PROCESS_VM_READ.
            process_handle = open_process(0x1000, False, process_id.value)
            if not process_handle:
                return None
            try:
                image_path = ctypes.create_unicode_buffer(32768)
                image_size = wintypes.DWORD(len(image_path))
                if not query_image(
                    process_handle, 0, image_path, ctypes.byref(image_size)
                ):
                    return None
                return os.path.basename(image_path.value).lower()
            finally:
                close_handle(process_handle)
        except Exception:
            return None

    def _find_roblox_window(self) -> int | None:
        """Find the actual Roblox player window, preferring its game process."""
        if not PYWIN32_AVAILABLE:
            return None

        own_hwnd = self.root.winfo_id()
        candidates: list[tuple[int, int, int, str, str]] = []
        browser_processes = {
            "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
            "opera.exe", "vivaldi.exe"
        }
        preferred_processes = {
            "robloxplayerbeta.exe", "robloxplayer.exe", "roblox.exe"
        }

        def callback(hwnd: int, _extra) -> bool:
            try:
                if hwnd == own_hwnd or not win32gui.IsWindowVisible(hwnd):
                    return True
                if win32gui.GetWindow(hwnd, getattr(win32con, "GW_OWNER", 4)):
                    return True

                title = win32gui.GetWindowText(hwnd).strip()
                process_name = self._get_window_process_name(hwnd) or ""
                title_has_roblox = "roblox" in title.lower()
                process_has_roblox = "roblox" in process_name

                if not title_has_roblox and not process_has_roblox:
                    return True
                if "studio" in process_name or "crash" in process_name:
                    return True
                if process_name in browser_processes:
                    return True

                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width = max(0, right - left)
                height = max(0, bottom - top)
                area = width * height
                if width < 200 or height < 200:
                    return True

                if process_name in preferred_processes:
                    process_score = 5
                elif process_has_roblox:
                    process_score = 3
                elif not process_name:
                    # Some Windows builds deny process-query access. In that
                    # case, retain the title match as a lower-priority fallback.
                    process_score = 1
                else:
                    process_score = 0

                candidates.append(
                    (process_score, area, hwnd, title, process_name or "unknown process")
                )
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        score, area, hwnd, title, process_name = candidates[0]
        self.append_log(
            f"Roblox window selected: title='{title or '(untitled)'}', "
            f"process={process_name}, client-window area={area}px², match={score}."
        )
        return hwnd

    @staticmethod
    def _capture_clip_cursor() -> tuple[int, int, int, int] | None:
        if os.name != "nt":
            return None
        try:
            from ctypes import wintypes
            rect = wintypes.RECT()
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if user32.GetClipCursor(ctypes.byref(rect)):
                return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass
        return None

    @staticmethod
    def _restore_clip_cursor(rect_tuple: tuple[int, int, int, int] | None) -> None:
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if rect_tuple is None:
                user32.ClipCursor(None)
            else:
                rect = wintypes.RECT(*rect_tuple)
                user32.ClipCursor(ctypes.byref(rect))
        except Exception:
            pass

    @staticmethod
    def _assert_compatible_dpi_contexts(child_hwnd: int, parent_hwnd: int) -> None:
        if os.name != "nt":
            raise RuntimeError("Roblox window embedding is only supported on Windows.")
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_context = user32.GetWindowDpiAwarenessContext
            get_context.argtypes = [ctypes.c_void_p]
            get_context.restype = ctypes.c_void_p
            equal = user32.AreDpiAwarenessContextsEqual
            equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            equal.restype = ctypes.c_int
            child_context = get_context(child_hwnd)
            parent_context = get_context(parent_hwnd)
            if child_context and parent_context and not equal(child_context, parent_context):
                raise RuntimeError(
                    "Windows reports different DPI-awareness modes for Roblox and BTD Macro. "
                    "Embedding was cancelled to avoid changing Roblox's scaling and mouse "
                    "coordinates. The windows were left separate."
                )
        except AttributeError:
            # Older Windows versions do not expose these query functions.
            return

    @staticmethod
    def _set_top_level_owner(hwnd: int, owner_hwnd: int) -> None:
        """Set a top-level window's owner through GWLP_HWNDPARENT."""
        if os.name != "nt":
            raise RuntimeError("Window ownership is only available on Windows.")
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_owner = getattr(user32, "SetWindowLongPtrW", None)
        if set_owner is None:
            set_owner = user32.SetWindowLongW
        set_owner.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_owner.restype = ctypes.c_ssize_t

        ctypes.set_last_error(0)
        previous = set_owner(
            wintypes.HWND(int(hwnd)), -8, ctypes.c_ssize_t(int(owner_hwnd))
        )
        error_code = ctypes.get_last_error()
        if previous == 0 and error_code:
            raise ctypes.WinError(error_code)

    def dock_roblox(self, hwnd_override: int | None = None) -> None:
        """Dock Roblox as an owned top-level window without a separate taskbar button."""
        if not PYWIN32_AVAILABLE or os.name != "nt":
            self.set_status("Roblox docking requires Windows and pywin32.")
            self.append_log("Dock requested, but Windows/pywin32 support is unavailable.")
            return

        hwnd = int(hwnd_override) if hwnd_override is not None else self._find_roblox_window()
        if hwnd is None:
            self.set_status("Roblox window not found. Open Roblox, then try again.")
            self.append_log("No visible Roblox top-level window was found.")
            return

        def dock_step(label: str, operation):
            """Run one Win32 operation with HWND checks and a precise failure label."""
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError(f"{label}: Roblox HWND {hwnd} is invalid before the call.")
            self.append_log(f"Dock step: {label}")
            try:
                result = operation()
            except Exception as step_exc:
                raise RuntimeError(f"{label} failed: {step_exc}") from step_exc
            if not win32gui.IsWindow(hwnd):
                raise RuntimeError(f"{label}: Roblox HWND {hwnd} became invalid after the call.")
            return result

        try:
            dock_step("update Macro layout", self.root.update_idletasks)
            dock_step("update game panel layout", self.game_host.update_idletasks)

            old_style = int(dock_step(
                "read Roblox window style",
                lambda: win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            ))
            WS_CHILD = 0x40000000
            WS_POPUP = 0x80000000
            if old_style & WS_CHILD:
                raise RuntimeError(
                    "The selected Roblox window is already a child window. "
                    "It was left unchanged."
                )

            owner_index = getattr(win32con, "GW_OWNER", 4)
            original_owner = int(dock_step(
                "read Roblox owner", lambda: win32gui.GetWindow(hwnd, owner_index)
            ) or 0)
            original_ex_style = int(dock_step(
                "read Roblox extended style",
                lambda: win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            ))
            original_rect = dock_step(
                "read Roblox position", lambda: win32gui.GetWindowRect(hwnd)
            )
            original_placement = dock_step(
                "read Roblox placement", lambda: win32gui.GetWindowPlacement(hwnd)
            )
            original_visible = bool(dock_step(
                "read Roblox visibility", lambda: win32gui.IsWindowVisible(hwnd)
            ))
            original_title = dock_step(
                "read Roblox title", lambda: win32gui.GetWindowText(hwnd)
            )
            was_iconic = bool(dock_step(
                "read Roblox minimized state", lambda: win32gui.IsIconic(hwnd)
            ))
            self.original_window_state = {
                "owner": original_owner,
                "style": old_style,
                "ex_style": original_ex_style,
                "rect": original_rect,
                "placement": original_placement,
                "visible": original_visible,
                "title": original_title,
                "was_iconic": was_iconic,
            }

            root_hwnd = int(self.root.winfo_id())
            if not win32gui.IsWindow(root_hwnd):
                raise RuntimeError("BTD Macro's top-level window is unavailable.")
            if root_hwnd == int(hwnd):
                raise RuntimeError("Roblox and BTD Macro resolved to the same window.")

            self.docked_hwnd = int(hwnd)
            self._arranging_windows = True

            # Clear WS_EX_APPWINDOW if a prior taskbar experiment left it set.
            # An owned top-level window is otherwise omitted from the taskbar.
            WS_EX_APPWINDOW = 0x00040000
            docked_ex_style = original_ex_style & ~WS_EX_APPWINDOW
            if docked_ex_style != original_ex_style:
                dock_step(
                    "remove separate taskbar-button style",
                    lambda: win32gui.SetWindowLong(
                        hwnd, win32con.GWL_EXSTYLE, docked_ex_style
                    )
                )
            else:
                self.append_log("Dock step: separate taskbar-button style is already disabled")

            overlay_style = (
                (old_style & ~(WS_CHILD | 0x00C00000 | 0x00040000
                               | 0x00020000 | 0x00010000))
                | WS_POPUP | win32con.WS_VISIBLE
            )
            if overlay_style != old_style:
                dock_step(
                    "apply top-level overlay style",
                    lambda: win32gui.SetWindowLong(
                        hwnd, win32con.GWL_STYLE, overlay_style
                    )
                )
            else:
                self.append_log("Dock step: top-level overlay style already correct")

            # Native ownership gives Roblox the same stable foreground behavior
            # as the older, simpler build. No taskbar override or topmost polling.
            if original_owner != root_hwnd:
                dock_step(
                    "assign BTD Macro as Roblox owner",
                    lambda: self._set_top_level_owner(hwnd, root_hwnd)
                )
            else:
                self.append_log("Dock step: Roblox is already owned by BTD Macro")

            dock_step(
                "refresh Roblox window styles",
                lambda: win32gui.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                    | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                    | win32con.SWP_FRAMECHANGED
                )
            )

            actual_owner = int(dock_step(
                "verify Roblox owner", lambda: win32gui.GetWindow(hwnd, owner_index)
            ) or 0)
            if actual_owner != root_hwnd:
                raise RuntimeError(
                    f"Roblox must be owned by BTD Macro (expected {root_hwnd}, got {actual_owner})."
                )
            actual_ex_style = int(dock_step(
                "verify no separate taskbar style",
                lambda: win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            ))
            if actual_ex_style & WS_EX_APPWINDOW:
                raise RuntimeError("Windows retained WS_EX_APPWINDOW; Roblox may get a separate taskbar button.")

            if original_visible:
                dock_step(
                    "ensure Roblox remains visible",
                    lambda: win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                )
            dock_step(
                "position owned Roblox window in game panel",
                lambda: self._position_roblox_overlay(show=True, force=True)
            )

            self.game_placeholder.place_forget()
            self.dock_button.configure(
                text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
            )
            self.set_status("Roblox docked above Macro; no separate taskbar button.")
            self.append_log(
                "Roblox is a visible top-level window owned by BTD Macro. Windows "
                "keeps it above its owner and omits a separate Roblox taskbar button. "
                "The original window state is saved for safe restoration. No SetParent "
                "or manual taskbar registration is used."
            )
        except Exception as exc:
            rollback_error = None
            try:
                if self.original_window_state is not None:
                    self._restore_roblox_window(hwnd, self.original_window_state)
            except Exception as restore_exc:
                rollback_error = str(restore_exc)

            self.set_status(f"Roblox was not docked: {exc}")
            self.append_log(f"ERROR docking Roblox at a labelled step: {exc}")
            if rollback_error:
                self.append_log(
                    f"ERROR restoring Roblox after failed dock: {rollback_error}"
                )
                self.dock_button.configure(
                    text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
                )
                self.set_status("Dock failed; restoration needs attention. Use UNDOCK.")
            else:
                self.docked_hwnd = None
                self.original_window_state = None
                self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
                self.dock_button.configure(
                    text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
                )
                self.append_log("Roblox original state restoration completed after dock failure.")
        finally:
            self._arranging_windows = False

    def _on_game_host_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.game_host:
            return
        if (
            self.docked_hwnd is not None
            and not self._arranging_windows
            and not self._big_screen_mode
        ):
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

    def _position_roblox_overlay(
        self, *, show: bool = False, force: bool = False
    ) -> None:
        """Place the owned Roblox top-level HWND over the game panel."""
        hwnd = self.docked_hwnd
        if hwnd is None or not PYWIN32_AVAILABLE or self._big_screen_mode:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                self._handle_lost_roblox_window()
                return

            if self.root.state() == "iconic":
                # Windows automatically minimizes an owned top-level window.
                return

            self.root.update_idletasks()
            self.game_host.update_idletasks()
            host_hwnd = self.game_host.winfo_id()
            left, top, right, bottom = win32gui.GetClientRect(host_hwnd)
            width = max(1, int(right - left))
            height = max(1, int(bottom - top))
            screen_x, screen_y = win32gui.ClientToScreen(host_hwnd, (0, 0))

            flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
            if force:
                flags |= win32con.SWP_FRAMECHANGED

            # HWND_TOP reasserts the normal Z-order. Native owner semantics keep
            # the owned Roblox window above BTD Macro without HWND_TOPMOST.
            win32gui.SetWindowPos(
                hwnd, 0, int(screen_x), int(screen_y), width, height, flags
            )

            if show and win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(
                    hwnd, getattr(win32con, "SW_SHOWNOACTIVATE", 4)
                )
        except Exception as exc:
            self.append_log(f"WARNING positioning the Roblox overlay: {exc}")

    def _handle_lost_roblox_window(self) -> None:
        self.docked_hwnd = None
        self.original_window_state = None
        self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.dock_button.configure(
            text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
        )
        self.set_status("Roblox closed; the game panel is empty.")
        self.append_log("Roblox closed independently; the visual docking state was cleared.")

    def _restore_roblox_window(
        self, hwnd: int, state: dict
    ) -> None:
        """Best-effort restoration that always attempts the saved visibility last."""
        if not PYWIN32_AVAILABLE:
            raise RuntimeError("Windows window restoration is unavailable without pywin32.")
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(
                f"Cannot restore Roblox: HWND {hwnd} is no longer a valid window."
            )

        errors: list[str] = []
        owner_index = getattr(win32con, "GW_OWNER", 4)
        original_owner = int(state.get("owner", 0) or 0)
        root_hwnd = int(self.root.winfo_id())

        def attempt(label: str, operation) -> bool:
            if not win32gui.IsWindow(hwnd):
                errors.append(f"{label}: Roblox HWND became invalid.")
                return False
            try:
                operation()
                if not win32gui.IsWindow(hwnd):
                    errors.append(f"{label}: Roblox HWND became invalid after the call.")
                    return False
                return True
            except Exception as restore_exc:
                message = f"{label} failed: {restore_exc}"
                errors.append(message)
                self.append_log(f"Roblox restore step: {message}")
                return False

        current_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
        if current_owner != original_owner:
            if current_owner in (0, root_hwnd):
                attempt(
                    "restore original owner",
                    lambda: self._set_top_level_owner(hwnd, original_owner)
                )
            else:
                errors.append(
                    "Owner changed unexpectedly; left it unchanged "
                    f"(current {current_owner}, original {original_owner})."
                )

        restored_style = int(state["style"])
        if restored_style & 0x40000000:
            errors.append(
                "Saved style contains WS_CHILD; refusing to restore a child-style window."
            )
        else:
            attempt(
                "restore original window style",
                lambda: win32gui.SetWindowLong(
                    hwnd, win32con.GWL_STYLE, restored_style
                )
            )
        attempt(
            "restore original extended style",
            lambda: win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE, int(state["ex_style"])
            )
        )
        attempt(
            "refresh restored window styles",
            lambda: win32gui.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED
            )
        )

        title = state.get("title") or "Roblox"
        attempt("restore window title", lambda: win32gui.SetWindowText(hwnd, title))
        placement = state.get("placement")
        if placement is not None:
            attempt(
                "restore window placement",
                lambda: win32gui.SetWindowPlacement(hwnd, placement)
            )
        elif state.get("rect") is not None:
            left, top, right, bottom = state["rect"]
            attempt(
                "restore window rectangle",
                lambda: win32gui.SetWindowPos(
                    hwnd, 0, left, top, right - left, bottom - top,
                    win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                    | win32con.SWP_FRAMECHANGED
                )
            )

        # Visibility recovery is deliberately the last independent operation.
        # Even if a style, title, or placement call failed, still try to show Roblox.
        if state.get("visible", True):
            show_cmd = (
                win32con.SW_SHOWMINNOACTIVE
                if state.get("was_iconic") else win32con.SW_SHOW
            )
            attempt("restore Roblox visibility", lambda: win32gui.ShowWindow(hwnd, show_cmd))
        else:
            attempt(
                "restore Roblox hidden state",
                lambda: win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            )

        if win32gui.IsWindow(hwnd):
            final_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
            if final_owner != original_owner:
                errors.append(
                    f"Final owner verification failed (expected {original_owner}, got {final_owner})."
                )
            if state.get("visible", True) and not win32gui.IsWindowVisible(hwnd):
                # Last-chance visibility recovery, even when prior restoration failed.
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                except Exception as visibility_exc:
                    errors.append(f"Last-chance show failed: {visibility_exc}")
                if not win32gui.IsWindowVisible(hwnd):
                    errors.append("Roblox is still not visible after last-chance show.")

        if errors:
            raise RuntimeError(" | ".join(errors))

    def undock_roblox(self) -> bool:
        if self.docked_hwnd is None:
            return True

        hwnd = self.docked_hwnd
        try:
            if win32gui.IsWindow(hwnd):
                if self.original_window_state is None:
                    raise RuntimeError("The original Roblox window state is unavailable.")
                self._restore_roblox_window(hwnd, self.original_window_state)
            else:
                self.append_log("Roblox already exited; clearing the visual docking state.")

            self.docked_hwnd = None
            self.original_window_state = None
            self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
            self.dock_button.configure(
                text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
            )
            self.set_status("Roblox undocked and restored as an independent window.")
            self.append_log(
                "Roblox remains running as a separate top-level process. Its original "
                "window style, title, size/placement, owner, and visibility were restored."
            )
            return True
        except Exception as exc:
            self.set_status(f"Could not restore Roblox safely: {exc}")
            self.append_log(f"ERROR undocking Roblox: {exc}")
            return False

    def close_app(self) -> None:
        self.universal_stop()
        if self._big_screen_mode and not self._restore_big_screen_mode():
            messagebox.showerror(
                "Roblox could not be restored",
                "BTD Macro will stay open because Roblox could not be restored "
                "from the bigger-screen layout. Use CHANGE BACK TO NORMAL and retry.",
                parent=self.root
            )
            return
        if self._global_hotkey_after_id is not None:
            try:
                self.root.after_cancel(self._global_hotkey_after_id)
            except tk.TclError:
                pass
            self._global_hotkey_after_id = None
        # An owned window is destroyed with its owner. Restore Roblox before
        # destroying the Tk root and verify it is no longer owned by this app.
        hwnd = self.docked_hwnd
        if hwnd is not None and not self.undock_roblox():
            messagebox.showerror(
                "Roblox could not be restored",
                "BTD Macro will stay open because Roblox could not be detached "
                "from its owner relationship safely. Retry UNDOCK ROBLOX first.",
                parent=self.root
            )
            return

        if hwnd is not None and PYWIN32_AVAILABLE and win32gui.IsWindow(hwnd):
            owner_index = getattr(win32con, "GW_OWNER", 4)
            owner_now = int(win32gui.GetWindow(hwnd, owner_index) or 0)
            root_hwnd = int(self.root.winfo_id())
            style_now = int(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE))
            if owner_now == root_hwnd or (style_now & 0x40000000):
                messagebox.showerror(
                    "Roblox is still attached",
                    "BTD Macro will not close because Roblox is still associated "
                    "with the Macro window. Roblox has been left running.",
                    parent=self.root
                )
                return

        self.root.destroy()


def configure_process_dpi_awareness() -> None:
    """Opt the Tk host into modern per-monitor DPI handling before creating HWNDs."""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_awareness = user32.SetProcessDpiAwarenessContext
        set_awareness.argtypes = [ctypes.c_void_p]
        set_awareness.restype = ctypes.c_int
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the pseudo-handle -4.
        set_awareness(ctypes.c_void_p(-4))
    except Exception:
        # Windows versions or launchers that already selected a DPI mode may
        # reject the call. Docking checks the actual HWND contexts before SetParent.
        pass


def _write_update_startup_marker(marker_path: str, expected_version: str) -> None:
    """Signal the updater after the new GUI has entered its Tk event loop."""
    if expected_version != APP_VERSION:
        return
    try:
        marker = Path(marker_path)
        with marker.open("w", encoding="utf-8") as marker_file:
            marker_file.write(f"{APP_VERSION}\n")
            marker_file.flush()
            os.fsync(marker_file.fileno())
    except OSError:
        # The updater will time out and roll back rather than deleting the old file.
        pass


def _migrate_legacy_filename_worker(
    root: tk.Tk, old_path: Path, new_path: Path, launch_args: list[str]
) -> None:
    """Move a first-upgrade install left under the old updater's filename."""
    process = None
    staging_path = None
    marker_path = Path(tempfile.gettempdir()) / (
        f"btd_macro_legacy_migration_{os.getpid()}_{APP_VERSION}.txt"
    )
    backup_path = new_path.with_name(
        f".{new_path.name}.{os.getpid()}.migration-backup"
    )
    moved_existing = False
    installed_new = False

    def remove_with_retry(path: Path, attempts: int = 20) -> bool:
        for _ in range(attempts):
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return True
            except OSError:
                time.sleep(0.25)
        return False

    try:
        if new_path.exists():
            if backup_path.exists():
                raise RuntimeError("A prior migration backup already exists.")
            os.replace(new_path, backup_path)
            moved_existing = True

        handle, staging_path = tempfile.mkstemp(
            prefix=f".{new_path.stem}.", suffix=".migration.py",
            dir=str(new_path.parent)
        )
        with os.fdopen(handle, "wb") as output, old_path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

        os.replace(staging_path, new_path)
        staging_path = None
        installed_new = True

        launch_env = os.environ.copy()
        launch_env["BTD_MACRO_UPDATE_STARTUP_MARKER"] = str(marker_path)
        launch_env["BTD_MACRO_EXPECTED_VERSION"] = APP_VERSION
        gui_python, launch_flags, launch_startupinfo = get_gui_python_launch_settings()
        process = subprocess.Popen(
            [gui_python, str(new_path), *launch_args],
            cwd=str(old_path.parent),
            env=launch_env,
            close_fds=True,
            creationflags=launch_flags,
            startupinfo=launch_startupinfo
        )

        deadline = time.monotonic() + 35.0
        confirmed = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("The correctly named copy exited before confirming startup.")
            if marker_path.is_file():
                try:
                    marker_version = marker_path.read_text(encoding="utf-8").strip()
                except OSError:
                    marker_version = ""
                if marker_version == APP_VERSION:
                    time.sleep(2.5)
                    if process.poll() is not None:
                        raise RuntimeError("The correctly named copy exited immediately after startup.")
                    confirmed = True
                    break
            time.sleep(0.25)

        if not confirmed:
            raise RuntimeError("The correctly named copy did not confirm startup.")

        old_deleted = remove_with_retry(old_path)
        if moved_existing:
            remove_with_retry(backup_path)

        if old_deleted:
            root.after(0, root.destroy)
        else:
            def warn_and_close() -> None:
                messagebox.showwarning(
                    "BTD Macro Filename Migration",
                    "BTD Macro 1.0.16 is running from its correct filename, but "
                    "Windows would not let the old script filename be deleted. "
                    "You can remove that old .py file manually.",
                    parent=root
                )
                root.destroy()
            root.after(0, warn_and_close)

    except Exception as exc:
        error_text = str(exc)
        rollback_errors = []
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

        if installed_new and not remove_with_retry(new_path):
            rollback_errors.append("Could not remove the failed versioned copy.")
        if moved_existing and backup_path.exists():
            try:
                os.replace(backup_path, new_path)
            except OSError as rollback_error:
                rollback_errors.append("Could not restore the existing versioned copy: " + str(rollback_error))

        def restore_old_window() -> None:
            try:
                root.deiconify()
                messagebox.showerror(
                    "BTD Macro Filename Migration Failed",
                    "The application will keep running under its current filename. "
                    "No previous script was intentionally removed.\n\n"
                    + error_text
                    + (("\n\n" + "\n".join(rollback_errors)) if rollback_errors else ""),
                    parent=root
                )
            except tk.TclError:
                pass
        root.after(0, restore_old_window)

    finally:
        if staging_path:
            remove_with_retry(Path(staging_path))
        remove_with_retry(marker_path)


def _begin_legacy_filename_migration(root: tk.Tk) -> None:
    current_path = Path(__file__).resolve()
    expected_path = current_path.with_name(f"BTD_Macro_{APP_VERSION}.py")
    if current_path.name.casefold() == expected_path.name.casefold():
        return

    # 1.0.4's older updater checked that the replacement stayed alive for
    # eight seconds. Delay this one-time rename so that check completes first.
    root.withdraw()
    threading.Thread(
        target=_migrate_legacy_filename_worker,
        args=(root, current_path, expected_path, sys.argv[1:]),
        name="BTDLegacyFilenameMigration",
        daemon=True
    ).start()


def detach_gui_console() -> None:
    """Detach from a console if an older updater or file association launched python.exe."""
    if os.name != "nt" or Path(sys.executable).name.lower() == "pythonw.exe":
        return
    try:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_console_window = kernel32.GetConsoleWindow
        get_console_window.argtypes = []
        get_console_window.restype = wintypes.HWND
        free_console = kernel32.FreeConsole
        free_console.argtypes = []
        free_console.restype = wintypes.BOOL
        if get_console_window():
            free_console()
    except Exception:
        # The UI remains usable even where console detachment is unavailable.
        pass


def get_primary_display_configuration() -> tuple[int, int, int]:
    """Return primary-screen width, height, and effective DPI before Tk exists."""
    if os.name != "nt":
        raise OSError("Display configuration detection is only available on Windows.")

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_system_metrics = user32.GetSystemMetrics
    get_system_metrics.argtypes = [ctypes.c_int]
    get_system_metrics.restype = ctypes.c_int

    # DPI awareness is configured before this call, so dimensions are physical pixels.
    width = int(get_system_metrics(0))   # SM_CXSCREEN
    height = int(get_system_metrics(1))  # SM_CYSCREEN
    if width <= 0 or height <= 0:
        raise ctypes.WinError(ctypes.get_last_error())

    dpi = 0
    get_dpi_for_system = getattr(user32, "GetDpiForSystem", None)
    if get_dpi_for_system is not None:
        get_dpi_for_system.argtypes = []
        get_dpi_for_system.restype = wintypes.UINT
        dpi = int(get_dpi_for_system() or 0)

    if dpi <= 0:
        # Compatibility fallback for Windows versions without GetDpiForSystem.
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        get_dc = user32.GetDC
        get_dc.argtypes = [wintypes.HWND]
        get_dc.restype = wintypes.HDC
        release_dc = user32.ReleaseDC
        release_dc.argtypes = [wintypes.HWND, wintypes.HDC]
        release_dc.restype = ctypes.c_int
        get_device_caps = gdi32.GetDeviceCaps
        get_device_caps.argtypes = [wintypes.HDC, ctypes.c_int]
        get_device_caps.restype = ctypes.c_int

        hdc = get_dc(None)
        if hdc:
            try:
                dpi = int(get_device_caps(hdc, 88))  # LOGPIXELSX
            finally:
                release_dc(None, hdc)

    if dpi <= 0:
        raise RuntimeError("Windows did not return a usable display DPI.")
    return width, height, dpi


def warn_before_gui_if_display_mismatch() -> None:
    """Show a themed startup warning when the primary display differs from target."""
    expected_width, expected_height, expected_scale = 1920, 1080, 125
    current_display = None
    problems = []
    detection_error = None

    try:
        width, height, dpi = get_primary_display_configuration()
        actual_scale = int(round(dpi * 100 / 96))
        current_display = (width, height, actual_scale)
        if (width, height) != (expected_width, expected_height):
            problems.append(
                f"Resolution: {width} × {height} (expected "
                f"{expected_width} × {expected_height})"
            )
        if actual_scale != expected_scale:
            problems.append(
                f"Windows scaling: {actual_scale}% (expected "
                f"{expected_scale}%)"
            )
        if not problems:
            return
    except Exception as exc:
        detection_error = str(exc)

    dialog = None
    try:
        dialog = tk.Tk()
        dialog.title("BTD Macro — Display Settings Warning")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)

        content = tk.Frame(dialog, bg=BG)
        content.pack(fill="both", expand=True, padx=22, pady=20)

        tk.Label(
            content, text="DISPLAY SETTINGS", bg=BG, fg=ACCENT,
            font=("Segoe UI Semibold", 9)
        ).pack(anchor="w")

        heading = (
            "Your display settings need attention"
            if detection_error is None
            else "Display settings could not be verified"
        )
        tk.Label(
            content, text=heading, bg=BG, fg=TEXT,
            font=("Segoe UI Semibold", 17)
        ).pack(anchor="w", pady=(5, 4))

        explanation = (
            "BTD Macro is designed for the display configuration below. "
            "You can open Windows Display Settings, make changes, and return "
            "here. This warning will stay open until you choose CONTINUE."
            if detection_error is None
            else "BTD Macro could not read your current display configuration. "
                 "You can open Windows Display Settings to check it manually, "
                 "or continue without changing anything."
        )
        tk.Label(
            content, text=explanation, bg=BG, fg=MUTED,
            justify="left", wraplength=490,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(0, 15))

        settings_card = tk.Frame(
            content, bg=PANEL, highlightthickness=1,
            highlightbackground=BORDER
        )
        settings_card.pack(fill="x")

        columns = tk.Frame(settings_card, bg=PANEL)
        columns.pack(fill="x", padx=16, pady=14)
        columns.grid_columnconfigure(0, weight=1)
        columns.grid_columnconfigure(1, weight=1)

        tk.Label(
            columns, text="EXPECTED", bg=PANEL, fg=MUTED,
            font=("Segoe UI Semibold", 8)
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        tk.Label(
            columns, text="CURRENT", bg=PANEL, fg=MUTED,
            font=("Segoe UI Semibold", 8)
        ).grid(row=0, column=1, sticky="w", pady=(0, 8))

        expected_text = (
            f"{expected_width} × {expected_height}\n"
            f"{expected_scale}% scaling"
        )
        if current_display is None:
            current_text = "Unable to verify\nCheck Windows Settings"
        else:
            current_text = (
                f"{current_display[0]} × {current_display[1]}\n"
                f"{current_display[2]}% scaling"
            )

        tk.Label(
            columns, text=expected_text, bg=PANEL, fg=TEXT,
            justify="left", font=("Segoe UI Semibold", 11)
        ).grid(row=1, column=0, sticky="w")
        tk.Label(
            columns, text=current_text, bg=PANEL,
            fg=DANGER if problems or detection_error else TEXT,
            justify="left", font=("Segoe UI Semibold", 11)
        ).grid(row=1, column=1, sticky="w")

        if problems:
            issue_text = "Mismatch: " + "  •  ".join(problems)
        elif detection_error is not None:
            issue_text = f"Detection details: {detection_error}"
        else:
            issue_text = "You can continue with the current display settings."

        tk.Label(
            content, text=issue_text, bg=BG,
            fg=DANGER if problems or detection_error else MUTED,
            justify="left", wraplength=490,
            font=("Segoe UI", 8)
        ).pack(anchor="w", pady=(11, 0))

        status_var = tk.StringVar(
            value="Choose OPEN DISPLAY SETTINGS to adjust Windows, or CONTINUE."
        )
        tk.Label(
            content, textvariable=status_var, bg=BG, fg=MUTED,
            justify="left", wraplength=490,
            font=("Segoe UI", 8)
        ).pack(anchor="w", pady=(10, 13))

        buttons = tk.Frame(content, bg=BG)
        buttons.pack(fill="x")

        def open_display_settings() -> None:
            try:
                if os.name != "nt" or not hasattr(os, "startfile"):
                    raise OSError(
                        "Opening Windows Display Settings is only supported on Windows."
                    )
                os.startfile("ms-settings:display")
                status_var.set(
                    "Windows Display Settings opened. Adjust your settings, "
                    "then return here and select CONTINUE."
                )
            except Exception as exc:
                status_var.set(f"Could not open Display Settings: {exc}")

        def continue_startup() -> None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass

        tk.Button(
            buttons, text="OPEN DISPLAY SETTINGS",
            command=open_display_settings,
            bg=ACCENT, fg="#101522",
            activebackground=ACCENT_HOVER, activeforeground="#101522",
            relief="flat", bd=0, font=("Segoe UI Semibold", 9),
            padx=15, pady=10, cursor="hand2"
        ).pack(side="left")

        tk.Button(
            buttons, text="CONTINUE", command=continue_startup,
            bg=PANEL_ALT, fg=TEXT,
            activebackground=BORDER, activeforeground=TEXT,
            relief="flat", bd=0, font=("Segoe UI Semibold", 9),
            padx=20, pady=10, cursor="hand2"
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", continue_startup)
        dialog.update_idletasks()
        window_width = max(550, dialog.winfo_reqwidth())
        window_height = max(385, dialog.winfo_reqheight())
        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()
        x = max(0, (screen_width - window_width) // 2)
        y = max(0, (screen_height - window_height) // 2)
        dialog.geometry(f"{window_width}x{window_height}+{x}+{y}")
        dialog.after(100, dialog.lift)
        dialog.mainloop()
    except Exception as exc:
        # Keep a native fallback so display-check failures never block startup.
        try:
            if dialog is not None:
                dialog.destroy()
        except tk.TclError:
            pass

        fallback_lines = [
            "BTD Macro expects a primary display of "
            f"{expected_width} × {expected_height} at {expected_scale}% scaling."
        ]
        if current_display is not None:
            fallback_lines.append(
                f"Detected: {current_display[0]} × {current_display[1]} "
                f"at {current_display[2]}% scaling."
            )
        if problems:
            fallback_lines.append("Mismatch: " + "; ".join(problems))
        if detection_error:
            fallback_lines.append("Detection details: " + detection_error)
        fallback_lines.append(f"The themed warning could not be opened: {exc}")
        fallback_lines.append("BTD Macro will continue opening.")

        try:
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            show_message = user32.MessageBoxW
            show_message.argtypes = [
                wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT
            ]
            show_message.restype = ctypes.c_int
            show_message(
                None,
                "\n\n".join(fallback_lines),
                "BTD Macro — Display Settings Warning",
                0x00000030 | 0x00010000
            )
        except Exception:
            pass

def main() -> None:
    detach_gui_console()
    configure_process_dpi_awareness()
    warn_before_gui_if_display_mismatch()
    marker_path = os.environ.pop("BTD_MACRO_UPDATE_STARTUP_MARKER", None)
    expected_version = os.environ.pop("BTD_MACRO_EXPECTED_VERSION", None)
    root = tk.Tk()
    BTDGui(root)
    if marker_path and expected_version:
        root.after(1200, _write_update_startup_marker, marker_path, expected_version)
    else:
        root.after(15000, _begin_legacy_filename_migration, root)
    root.mainloop()


if __name__ == "__main__":
    main()
