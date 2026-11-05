"""BTD Macro 1.0.19.

Dark four-tab GUI with a GitHub release updater and a Windows Roblox child-window
host. The macro and recording engines remain GUI placeholders; no gameplay
automation is sent by this version.
"""

from __future__ import annotations

import ast
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import textwrap
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import messagebox, ttk

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


APP_VERSION = "1.0.19"
APP_TITLE = "BTD Macro 1.0.19"
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
        self._position_capture_armed = False
        self._position_capture_after_id = None
        self._position_f8_was_down = False
        self._position_get_async_key_state = None
        self._roblox_scroll_test_active = False

        self._update_check_running = False
        self._update_download_running = False
        self._update_release: dict | None = None
        self._update_dialog: tk.Toplevel | None = None

        self._configure_styles()
        self._build_layout()
        self.refresh_recordings()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.game_host.bind("<Configure>", self._on_game_host_configure, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.bind("<Map>", self._on_root_map, add="+")
        self.root.bind("<Unmap>", self._on_root_unmap, add="+")

        self.append_log("BTD Macro 1.0.16 initialized. Lobby automation is not connected yet.")
        if self.button_positions:
            self.append_log(
                f"Loaded {len(self.button_positions)} saved Roblox button position(s)."
            )
        if not PYWIN32_AVAILABLE:
            self.append_log("pywin32 was not found. Roblox embedding will be unavailable.")
        self.root.after_idle(self._maximize_and_lock_window)
        self.root.after(900, self.check_for_updates)

    def _on_root_configure(self, event=None) -> None:
        """Re-maximize if Windows restores the app out of its fixed desktop layout."""
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, "_maximizing_window", False):
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

    def _on_root_unmap(self, event=None) -> None:
        """Owned Roblox windows are hidden automatically when the owner minimizes."""
        if event is not None and event.widget is not self.root:
            return
        # Windows automatically hides and restores an owned top-level window
        # with its owner. Do not independently minimize or hide Roblox here.

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
            title_area, text="BTD MACRO 1.0.16", bg=BG, fg=TEXT,
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

        self.notebook.add(self.macro_tab, text="  Macro  ")
        self.notebook.add(self.recording_tab, text="  Recording  ")
        self.notebook.add(self.settings_tab, text="  Settings  ")
        self.notebook.add(self.debug_tab, text="  Debug  ")

        self._build_macro_tab()
        self._build_recording_tab()
        self._build_settings_tab()
        self._build_debug_tab()

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
        self._add_dropdown(
            form, 1, "2. CHOOSE A DIFFICULTY", self.difficulty_var,
            ["Choose a Difficulty"] + DIFFICULTY_OPTIONS
        )
        self.recording_combo = self._add_dropdown(
            form, 2, "3. CHOOSE YOUR RECORDING", self.macro_recording_var,
            ["Choose a Recording"]
        )

        controls = tk.Frame(content, bg=BG)
        controls.pack(fill="x", pady=(18, 0))
        self._make_button(
            controls, "START MACRO", self.start_macro,
            bg=ACCENT, fg="#101522"
        ).pack(side="left", fill="x", expand=True, padx=(0, 7))
        self._make_button(
            controls, "STOP MACRO", self.stop_macro,
            bg="#4A2931", fg="#F4DDE0", active_bg="#62333D"
        ).pack(side="left", fill="x", expand=True, padx=(7, 0))
        self.test_roblox_button = self._make_button(
            controls, "TEST ROBLOX", self.test_roblox_focus_and_scroll,
            bg=PANEL_ALT, fg=TEXT, active_bg=BORDER, small=True
        )
        self.test_roblox_button.pack(
            side="left", fill="x", expand=True, padx=(7, 0)
        )

        tk.Label(
            content,
            text="Lobby automation is not connected yet. Record the Roblox button positions below first.",
            bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=320,
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
            text="Select a target, hover it in Roblox, then press F8.",
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
            position_controls, "ARM F8 CAPTURE",
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
        """Return shared lobby controls, the map-list scroll area, and map targets."""
        return (
            [
                "PLAY", "CREATE PARTY", "PRIVATE", "KICK", "CHANGE MAP",
                "MAP SCROLL AREA", "START"
            ]
            + [f"MAP — {map_name}" for map_name in MAP_OPTIONS]
        )

    def test_roblox_focus_and_scroll(self) -> None:
        """Focus Roblox, smoothly center the cursor, verify it, then scroll twice."""
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
                "Record MAP SCROLL AREA with F8 before running this test.",
                "warning"
            )
            self.append_log(
                "Roblox scroll test not started: MAP SCROLL AREA has no saved "
                "position. Select it in Position Recorder, hover the scrollable "
                "map list in Roblox, and press F8."
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
                "saved MAP SCROLL AREA, verify arrival, wait, then scroll down twice."
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
                    "MAP SCROLL AREA position is missing. Record it with F8 first."
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

    def _begin_roblox_test_scroll(
        self, hwnd: int, target: tuple[int, int]
    ) -> None:
        """Recheck focus and cursor after the arrival pause before scrolling."""
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

            self.append_log(
                f"Arrival delay complete ({TEST_CURSOR_ARRIVAL_DELAY_MS} ms); "
                "focus and cursor position rechecked."
            )
            self.set_status("Roblox focused. Scrolling down (1/2)…", "progress")
            self._send_roblox_test_scroll_notch(hwnd, 1, target)
        except Exception as exc:
            self._finish_roblox_scroll_test(False, str(exc))

    def _send_roblox_test_scroll_notch(
        self, hwnd: int, notch_number: int, target: tuple[int, int]
    ) -> None:
        """Send the original global mouse-wheel input after focus/position checks."""
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

            if notch_number < 2:
                self.root.after(
                    TEST_SCROLL_NOTCH_INTERVAL_MS,
                    self._send_roblox_test_scroll_notch,
                    hwnd, notch_number + 1, target
                )
            else:
                self._finish_roblox_scroll_test(
                    True,
                    "Roblox focused; map-list area targeted; sent two downward wheel notches."
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
        saved_count = len(self.button_positions)
        total_count = len(self._position_target_options())
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
        """Arm or cancel the F8 mouse-position capture mode."""
        if self._position_capture_armed:
            self._stop_position_capture()
            self.set_status("Position capture cancelled.")
            return

        if os.name != "nt" or not PYWIN32_AVAILABLE:
            self.set_status(
                "Position capture requires Windows and pywin32. Install pywin32 first."
            )
            self.append_log("Position capture could not start: Windows/pywin32 unavailable.")
            return

        target = self.position_target_var.get().strip()
        if target not in self._position_target_options():
            self.set_status("Choose a button or map target to record first.")
            return

        try:
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_async_key_state = user32.GetAsyncKeyState
            get_async_key_state.argtypes = [ctypes.c_int]
            get_async_key_state.restype = ctypes.c_short
            self._position_get_async_key_state = get_async_key_state
        except Exception as exc:
            self.set_status(f"Could not initialize F8 capture: {exc}")
            self.append_log(f"ERROR initializing position capture: {exc}")
            return

        self._position_capture_armed = True
        self._position_f8_was_down = False
        self.position_capture_button.configure(text="CANCEL CAPTURE")
        self.set_status(
            f"F8 capture armed for {target}. Hover the Roblox button and press F8."
        )
        self.append_log(f"Position capture armed for '{target}'.")
        self._poll_position_capture_key()

    def _stop_position_capture(self) -> None:
        """Disarm capture and cancel its pending key-state poll."""
        self._position_capture_armed = False
        if self._position_capture_after_id is not None:
            try:
                self.root.after_cancel(self._position_capture_after_id)
            except tk.TclError:
                pass
            self._position_capture_after_id = None
        if hasattr(self, "position_capture_button"):
            try:
                self.position_capture_button.configure(text="ARM F8 CAPTURE")
            except tk.TclError:
                pass
        self._position_f8_was_down = False

    def _poll_position_capture_key(self) -> None:
        """Poll F8 only while capture is armed; capture once per key press."""
        if not self._position_capture_armed:
            return
        try:
            key_state = int(self._position_get_async_key_state(0x77))  # VK_F8
            key_down = bool(key_state & 0x8000)
        except Exception as exc:
            self._stop_position_capture()
            self.set_status(f"F8 capture stopped: {exc}")
            self.append_log(f"ERROR polling F8 for position capture: {exc}")
            return

        if key_down and not self._position_f8_was_down:
            self._capture_selected_button_position()
        self._position_f8_was_down = key_down

        if self._position_capture_armed:
            self._position_capture_after_id = self.root.after(
                25, self._poll_position_capture_key
            )

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
                    "Roblox window not found. Open Roblox and hover its button, then press F8."
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
                    "Cursor is outside Roblox's game area. Hover the target button and press F8."
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
    # Macro and recording placeholders
    # ------------------------------------------------------------------
    def start_macro(self) -> None:
        if self.map_var.get() == "Choose a Map":
            self.set_status("Choose a map before starting the macro.")
            self.append_log("Start macro requested, but no map is selected.")
            return
        if self.difficulty_var.get() == "Choose a Difficulty":
            self.set_status("Choose a difficulty before starting the macro.")
            self.append_log("Start macro requested, but no difficulty is selected.")
            return
        if self.macro_recording_var.get() == "Choose a Recording":
            self.set_status("Choose a recording before starting the macro.")
            self.append_log("Start macro requested, but no recording is selected.")
            return
        self.set_status("Macro engine not connected yet — GUI only.")
        self.append_log(
            "Start macro requested for "
            f"{self.map_var.get()} / {self.difficulty_var.get()} / "
            f"{self.macro_recording_var.get()}. No game actions were sent."
        )

    def stop_macro(self) -> None:
        self.set_status("Macro stop requested — no macro is currently running.")
        self.append_log("Stop macro button pressed.")

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

    def dock_roblox(self) -> None:
        """Dock Roblox visually and as a native owned top-level window."""
        if not PYWIN32_AVAILABLE or os.name != "nt":
            self.set_status("Roblox docking requires Windows and pywin32.")
            self.append_log("Dock requested, but Windows/pywin32 support is unavailable.")
            return

        hwnd = self._find_roblox_window()
        if hwnd is None:
            self.set_status("Roblox window not found. Open Roblox, then try again.")
            self.append_log("No visible Roblox top-level window was found.")
            return

        try:
            self.root.update_idletasks()
            self.game_host.update_idletasks()

            old_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE))
            WS_CHILD = 0x40000000
            WS_POPUP = 0x80000000
            if old_style & WS_CHILD:
                raise RuntimeError(
                    "The selected Roblox window is already a child window. "
                    "It was left unchanged."
                )

            owner_index = getattr(win32con, "GW_OWNER", 4)
            original_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
            self.original_window_state = {
                "owner": original_owner,
                "style": old_style,
                "ex_style": int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)),
                "rect": win32gui.GetWindowRect(hwnd),
                "placement": win32gui.GetWindowPlacement(hwnd),
                "visible": bool(win32gui.IsWindowVisible(hwnd)),
                "title": win32gui.GetWindowText(hwnd),
                "was_iconic": bool(win32gui.IsIconic(hwnd)),
            }

            root_hwnd = int(self.root.winfo_id())
            if not win32gui.IsWindow(root_hwnd):
                raise RuntimeError("BTD Macro's top-level window is unavailable.")
            if root_hwnd == int(hwnd):
                raise RuntimeError("Roblox and BTD Macro resolved to the same window.")

            self.docked_hwnd = int(hwnd)
            self._arranging_windows = True

            # Keep Roblox a true top-level popup. Assign an owner—not a parent—
            # so it stays above the Macro while retaining Roblox's input thread.
            overlay_style = (
                (old_style & ~(WS_CHILD | 0x00C00000 | 0x00040000
                               | 0x00020000 | 0x00010000))
                | WS_POPUP | win32con.WS_VISIBLE
            )
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, overlay_style)
            self._set_top_level_owner(hwnd, root_hwnd)
            win32gui.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED
            )

            actual_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
            if actual_owner != root_hwnd:
                raise RuntimeError(
                    f"Windows did not assign BTD Macro as Roblox's owner "
                    f"(expected {root_hwnd}, got {actual_owner})."
                )

            self._position_roblox_overlay(show=True, force=True)
            self.game_placeholder.place_forget()
            self.dock_button.configure(
                text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
            )
            self.set_status("Roblox docked above Macro — native input preserved.")
            self.append_log(
                "Roblox remains a top-level window owned by BTD Macro. The native "
                "owner relationship keeps it above the Macro while Windows retains "
                "Roblox's own input thread and window procedure. No SetParent is used."
            )
        except Exception as exc:
            rollback_error = None
            try:
                if self.docked_hwnd is not None and self.original_window_state is not None:
                    self._restore_roblox_window(
                        self.docked_hwnd, self.original_window_state
                    )
            except Exception as restore_exc:
                rollback_error = str(restore_exc)

            self.set_status(f"Roblox was not docked: {exc}")
            self.append_log(f"ERROR docking Roblox: {exc}")
            if rollback_error:
                self.append_log(f"ERROR restoring Roblox after failed dock: {rollback_error}")
                self.dock_button.configure(
                    text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
                )
                self.set_status("Dock failed; Roblox may still be owned. Retry UNDOCK.")
            else:
                self.docked_hwnd = None
                self.original_window_state = None
                self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
                self.dock_button.configure(
                    text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
                )
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

    def _position_roblox_overlay(
        self, *, show: bool = False, force: bool = False
    ) -> None:
        """Place the independent Roblox top-level HWND over the game panel."""
        hwnd = self.docked_hwnd
        if hwnd is None or not PYWIN32_AVAILABLE:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                self._handle_lost_roblox_window()
                return

            if self.root.state() == "iconic":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
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
            # An owned top-level window stays above its owner. HWND_TOP also
            # reasserts placement in the ordinary (non-topmost) z-order.
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
        """Remove Macro ownership, then restore Roblox as an independent window."""
        if not PYWIN32_AVAILABLE or not win32gui.IsWindow(hwnd):
            return

        owner_index = getattr(win32con, "GW_OWNER", 4)
        original_owner = int(state.get("owner", 0) or 0)
        root_hwnd = int(self.root.winfo_id())
        current_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)

        # Remove ownership before the Tk owner is destroyed. Windows destroys
        # owned windows when their owner is destroyed, so verify this transition.
        if current_owner == root_hwnd:
            self._set_top_level_owner(hwnd, original_owner)
        elif current_owner != original_owner:
            raise RuntimeError(
                "Roblox's owner changed unexpectedly. "
                f"Expected BTD Macro ({root_hwnd}) or original owner "
                f"({original_owner}), got {current_owner}."
            )

        restored_style = int(state["style"])
        if restored_style & 0x40000000:
            raise RuntimeError(
                "The saved Roblox style contains WS_CHILD. Refusing to close "
                "BTD Macro because Roblox might still depend on the Macro window."
            )

        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, restored_style)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE, int(state["ex_style"])
        )
        win32gui.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            | win32con.SWP_FRAMECHANGED
        )

        restored_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
        if restored_owner != original_owner:
            raise RuntimeError(
                "Roblox is not independent from BTD Macro yet. "
                f"Expected original owner {original_owner}, got {restored_owner}."
            )

        title = state.get("title") or "Roblox"
        win32gui.SetWindowText(hwnd, title)
        placement = state.get("placement")
        if placement is not None:
            win32gui.SetWindowPlacement(hwnd, placement)
        else:
            left, top, right, bottom = state["rect"]
            win32gui.SetWindowPos(
                hwnd, 0, left, top, right - left, bottom - top,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED
            )

        if state.get("visible", True):
            show_cmd = (
                win32con.SW_SHOWMINNOACTIVE
                if state.get("was_iconic") else win32con.SW_SHOW
            )
            win32gui.ShowWindow(hwnd, show_cmd)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

        if not win32gui.IsWindow(hwnd):
            raise RuntimeError("Roblox window disappeared during restoration.")
        final_owner = int(win32gui.GetWindow(hwnd, owner_index) or 0)
        if final_owner == root_hwnd:
            raise RuntimeError("Roblox is still owned by BTD Macro; shutdown blocked.")

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
                "window style, title, size/placement, and visibility were restored."
            )
            return True
        except Exception as exc:
            self.set_status(f"Could not restore Roblox safely: {exc}")
            self.append_log(f"ERROR undocking Roblox: {exc}")
            return False

    def close_app(self) -> None:
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
