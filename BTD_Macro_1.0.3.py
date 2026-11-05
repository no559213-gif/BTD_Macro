"""BTD Macro 1.0.2 - GUI scaffold.

This file builds the requested dark, four-tab interface. The macro and recording
engines are intentionally placeholders; no game inputs are automated yet.

Roblox docking is implemented on Windows when pywin32 is installed.
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


APP_VERSION = "1.0.3"
APP_TITLE = "BTD Macro 1.0.3"
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
        self._original_clip_rect: tuple[int, int, int, int] | None = None
        self._arranging_windows = False
        self.recording_names: list[str] = []
        self.currently_loaded_recording: str | None = None

        self._update_check_running = False
        self._update_download_running = False
        self._update_release: dict | None = None
        self._update_dialog: tk.Toplevel | None = None

        self._configure_styles()
        self._build_layout()
        self.refresh_recordings()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.game_host.bind("<Configure>", self._on_game_host_configure, add="+")

        self.append_log("BTD Macro 1.0.3 initialized. Automation engines are not connected yet.")
        if not PYWIN32_AVAILABLE:
            self.append_log("pywin32 was not found. Roblox embedding will be unavailable.")
        self.root.after(900, self.check_for_updates)

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

        # Roblox is hosted as a real child window in this left-hand panel.
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
            title_area, text="BTD MACRO 1.0.3", bg=BG, fg=TEXT,
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
        status_frame = tk.Frame(self.macro_panel, bg=PANEL, height=42)
        status_frame.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        status_frame.grid_propagate(False)
        tk.Label(
            status_frame, text="STATUS:", bg=PANEL, fg=ACCENT,
            font=("Segoe UI Semibold", 8)
        ).pack(side="left", padx=(9, 6))
        self.status_var = tk.StringVar(value="Idle — ready.")
        self.status_label = tk.Label(
            status_frame, textvariable=self.status_var, bg=PANEL,
            fg=TEXT, anchor="w", justify="left", wraplength=250,
            font=("Segoe UI", 8)
        )
        self.status_label.pack(side="left", fill="both", expand=True, padx=(0, 8))

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

        tk.Label(
            content,
            text="Macro execution is a placeholder until the game automation is added.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(14, 0))

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

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
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

        target = Path(__file__).resolve()
        backup = target.with_name(f".{target.name}.{os.getpid()}.bak")
        helper = Path(tempfile.gettempdir()) / f"btd_macro_updater_{os.getpid()}.py"
        helper_source = r'''import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

download_path, target_path, backup_path, parent_pid, python_exe, args_json, working_dir = sys.argv[1:8]
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

def notify_failure(message):
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(
                None, message, "BTD Macro Update Failed", 0x10
            )
        except Exception:
            pass

wait_for_parent()
args = json.loads(args_json)
had_backup = False
try:
    shutil.copy2(target_path, backup_path)
    had_backup = True
    os.replace(download_path, target_path)
    process = subprocess.Popen(
        [python_exe, target_path, *args],
        cwd=working_dir,
        close_fds=True
    )
    time.sleep(8)
    if process.poll() is not None:
        raise RuntimeError("The updated application exited during startup.")
    try:
        os.remove(backup_path)
    except OSError:
        pass
except Exception as exc:
    try:
        if had_backup and os.path.exists(backup_path):
            rollback_path = target_path + ".rollback"
            shutil.copy2(backup_path, rollback_path)
            os.replace(rollback_path, target_path)
    except Exception as rollback_error:
        notify_failure(
            "The update failed and automatic rollback also failed.\n\n"
            + str(exc) + "\n\nRollback: " + str(rollback_error)
        )
    else:
        notify_failure(
            "The update could not be installed. Your previous version was restored.\n\n"
            + str(exc)
        )
        try:
            subprocess.Popen(
                [python_exe, target_path, *args],
                cwd=working_dir,
                close_fds=True
            )
        except Exception:
            pass
finally:
    try:
        os.remove(download_path)
    except OSError:
        pass
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
                    sys.executable, str(helper), pending_path, str(target),
                    str(backup), str(os.getpid()), sys.executable,
                    json.dumps(sys.argv[1:]), str(Path.cwd())
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

        self.set_status("Update staged. Restarting BTD Macro...")
        self.append_log("Verified update staged; handing off replacement and restart.")
        self.root.destroy()

    # ------------------------------------------------------------------
    # Roblox child-window embedding (Windows + pywin32)
    # ------------------------------------------------------------------
    def toggle_dock(self) -> None:
        if self.docked_hwnd is not None:
            self.undock_roblox()
        else:
            self.dock_roblox()

    def _find_roblox_window(self) -> int | None:
        if not PYWIN32_AVAILABLE:
            return None
        matches: list[int] = []
        own_hwnd = self.root.winfo_id()
        own_pid = os.getpid()

        def callback(hwnd: int, _extra) -> bool:
            try:
                if hwnd == own_hwnd or not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd).strip()
                if title and "roblox" in title.lower():
                    matches.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        return matches[0] if matches else None

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

    def dock_roblox(self) -> None:
        if not PYWIN32_AVAILABLE:
            self.set_status("Roblox embedding requires Windows and pywin32.")
            self.append_log("Dock requested, but pywin32 is unavailable.")
            return

        hwnd = self._find_roblox_window()
        if hwnd is None:
            self.set_status("Roblox window not found. Open Roblox, then try again.")
            self.append_log("No visible Roblox top-level window was found.")
            return

        try:
            self.root.update_idletasks()
            self.game_host.update_idletasks()
            parent_hwnd = self.game_host.winfo_id()
            self._assert_compatible_dpi_contexts(hwnd, parent_hwnd)

            self.original_window_state = {
                "parent": win32gui.GetParent(hwnd),
                "style": win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE),
                "ex_style": win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE),
                "rect": win32gui.GetWindowRect(hwnd),
                "placement": win32gui.GetWindowPlacement(hwnd),
                "visible": bool(win32gui.IsWindowVisible(hwnd)),
            }
            self._original_clip_rect = self._capture_clip_cursor()
            self.docked_hwnd = hwnd

            self._arranging_windows = True
            self._cancel_roblox_mouse_mode(hwnd)

            old_style = self.original_window_state["style"]
            child_style = (
                old_style
                | win32con.WS_CHILD
                | win32con.WS_VISIBLE
                | getattr(win32con, "WS_CLIPSIBLINGS", 0)
            )
            child_style &= ~(
                win32con.WS_POPUP
                | win32con.WS_CAPTION
                | win32con.WS_THICKFRAME
                | win32con.WS_SYSMENU
                | win32con.WS_MINIMIZEBOX
                | win32con.WS_MAXIMIZEBOX
            )
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, child_style)

            old_ex_style = self.original_window_state["ex_style"]
            child_ex_style = old_ex_style & ~(
                getattr(win32con, "WS_EX_APPWINDOW", 0)
                | getattr(win32con, "WS_EX_WINDOWEDGE", 0)
            )
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, child_ex_style)

            win32gui.SetParent(hwnd, parent_hwnd)
            if win32gui.GetParent(hwnd) != parent_hwnd:
                raise RuntimeError("Windows did not attach the Roblox window to the game panel.")

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            self._resize_embedded_roblox()
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            self.game_placeholder.place_forget()
            self.dock_button.configure(
                text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
            )
            self.set_status("Roblox embedded — click the game to focus it.")
            self.append_log(
                "Roblox reparented into the left game panel as a WS_CHILD window. "
                "Window styles and DPI-awareness compatibility were checked; the child "
                "is sized in the host panel's client coordinates."
            )
            self._focus_embedded_roblox()
        except Exception as exc:
            self._arranging_windows = False
            rollback_errors: list[str] = []
            if self.docked_hwnd is not None and self.original_window_state is not None:
                try:
                    self._restore_roblox_window(
                        self.docked_hwnd, self.original_window_state,
                        restore_clip=True
                    )
                except Exception as restore_exc:
                    rollback_errors.append(str(restore_exc))
            self.docked_hwnd = None
            self.original_window_state = None
            self._original_clip_rect = None
            self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
            self.dock_button.configure(
                text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
            )
            self.set_status(f"Roblox was not embedded: {exc}")
            self.append_log(f"ERROR embedding Roblox: {exc}")
            for error in rollback_errors:
                self.append_log(f"ERROR restoring Roblox after failed dock: {error}")
        finally:
            self._arranging_windows = False

    def _on_game_host_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.game_host:
            return
        if self.docked_hwnd is None or self._arranging_windows:
            return
        try:
            self.root.after_idle(self._resize_embedded_roblox)
        except tk.TclError:
            pass

    def _resize_embedded_roblox(self) -> None:
        if (
            self.docked_hwnd is None or not PYWIN32_AVAILABLE
            or self._arranging_windows
        ):
            return
        try:
            if not win32gui.IsWindow(self.docked_hwnd):
                self._handle_lost_roblox_window()
                return
            self.game_host.update_idletasks()
            width = max(1, int(self.game_host.winfo_width()))
            height = max(1, int(self.game_host.winfo_height()))
            win32gui.SetWindowPos(
                self.docked_hwnd, 0, 0, 0, width, height,
                win32con.SWP_NOZORDER
                | win32con.SWP_NOACTIVATE
                | win32con.SWP_SHOWWINDOW
            )
        except Exception as exc:
            self.append_log(f"WARNING resizing embedded Roblox window: {exc}")

    def _handle_lost_roblox_window(self) -> None:
        self.docked_hwnd = None
        self.original_window_state = None
        self._original_clip_rect = None
        self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.dock_button.configure(
            text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
        )
        self.set_status("Roblox closed; the game panel is empty.")
        self.append_log("The embedded Roblox window closed while BTD Macro was running.")

    @staticmethod
    def _cancel_roblox_mouse_mode(hwnd: int | None = None) -> None:
        """Cancel capture in Roblox's GUI thread; never call ReleaseCapture cross-process."""
        if hwnd is None or os.name != "nt":
            return
        try:
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            get_thread_info = user32.GetGUIThreadInfo
            get_thread_info.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
            get_thread_info.restype = wintypes.BOOL
            get_thread_id = user32.GetWindowThreadProcessId
            get_thread_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            get_thread_id.restype = wintypes.DWORD
            post_message = user32.PostMessageW
            post_message.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            ]
            post_message.restype = wintypes.BOOL

            thread_id = get_thread_id(hwnd, None)
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if thread_id and get_thread_info(thread_id, ctypes.byref(info)):
                if info.hwndCapture:
                    post_message(info.hwndCapture, 0x001F, 0, 0)  # WM_CANCELMODE
            post_message(hwnd, 0x001F, 0, 0)
        except Exception:
            pass

    def _focus_embedded_roblox(self) -> None:
        if self.docked_hwnd is None or not PYWIN32_AVAILABLE:
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            # Foreground activation is requested on the host; ordinary mouse
            # activation then focuses the child through Windows' input routing.
            win32gui.SetForegroundWindow(self.root.winfo_id())
        except Exception as exc:
            self.append_log(f"Roblox focus handoff note: {exc}")

    def _restore_roblox_window(
        self, hwnd: int, state: dict, *, restore_clip: bool
    ) -> None:
        if not PYWIN32_AVAILABLE or not win32gui.IsWindow(hwnd):
            return
        self._cancel_roblox_mouse_mode(hwnd)

        # Restore the original native parent and styles before restoring placement.
        win32gui.SetParent(hwnd, state["parent"])
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, state["style"])
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, state["ex_style"])
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
        win32gui.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            | win32con.SWP_FRAMECHANGED
        )
        win32gui.ShowWindow(
            hwnd, win32con.SW_SHOW if state.get("visible", True) else win32con.SW_HIDE
        )
        if restore_clip:
            self._restore_clip_cursor(self._original_clip_rect)

    def undock_roblox(self) -> bool:
        if self.docked_hwnd is None:
            return True
        hwnd = self.docked_hwnd
        try:
            if win32gui.IsWindow(hwnd):
                if self.original_window_state is None:
                    raise RuntimeError("The original Roblox window state is unavailable.")
                self._restore_roblox_window(
                    hwnd, self.original_window_state, restore_clip=True
                )
            else:
                self.append_log("Roblox was already closed; clearing the embedded-window state.")

            self.docked_hwnd = None
            self.original_window_state = None
            self._original_clip_rect = None
            self.game_placeholder.place(relx=0.5, rely=0.5, anchor="center")
            self.dock_button.configure(
                text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
            )
            self.set_status("Roblox undocked; its original window state was restored.")
            self.append_log(
                "Roblox was detached from the game panel. Original parent, styles, "
                "window placement, visibility, and the pre-dock cursor clip were restored."
            )
            return True
        except Exception as exc:
            self.set_status(f"Could not safely undock Roblox: {exc}")
            self.append_log(f"ERROR undocking Roblox: {exc}")
            return False

    def close_app(self) -> None:
        if self.docked_hwnd is not None and not self.undock_roblox():
            messagebox.showerror(
                "Roblox could not be restored",
                "BTD Macro could not restore Roblox. The application will remain open "
                "so you can retry UNDOCK ROBLOX.",
                parent=self.root
            )
            return
        self.root.destroy()


    def toggle_dock(self) -> None:
        if self.docked_hwnd is not None:
            self.undock_roblox()
        else:
            self.dock_roblox()

    def _find_roblox_window(self) -> int | None:
        if not PYWIN32_AVAILABLE:
            return None
        matches: list[int] = []
        own_hwnd = self.root.winfo_id()

        def callback(hwnd: int, _extra) -> bool:
            try:
                if hwnd == own_hwnd or not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd).strip()
                if title and "roblox" in title.lower():
                    matches.append(hwnd)
            except win32gui.error:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        return matches[0] if matches else None

    def _get_work_area(self) -> tuple[int, int, int, int]:
        """Return the work area of the monitor containing the macro window."""
        hwnd = self.root.winfo_id()
        monitor = win32api.MonitorFromWindow(
            hwnd, getattr(win32con, "MONITOR_DEFAULTTONEAREST", 2)
        )
        info = win32api.GetMonitorInfo(monitor)
        return tuple(int(value) for value in info["Work"])

    def dock_roblox(self) -> None:
        if not PYWIN32_AVAILABLE:
            self.set_status("Roblox window arrangement needs pywin32 on Windows.")
            self.append_log("Dock requested, but pywin32 is unavailable.")
            return

        hwnd = self._find_roblox_window()
        if hwnd is None:
            self.set_status("Roblox window not found. Open Roblox, then try docking again.")
            self.append_log("Dock requested, but no visible window with 'Roblox' in its title was found.")
            return

        try:
            self.root.update_idletasks()
            macro_hwnd = self.root.winfo_id()
            work_left, work_top, work_right, work_bottom = self._get_work_area()
            work_width = work_right - work_left
            work_height = work_bottom - work_top

            # Reserve about 36% of the monitor for the macro and give the
            # larger remaining area to Roblox. Keep a useful minimum for both.
            macro_width = min(760, max(560, int(work_width * 0.36)))
            macro_width = min(macro_width, work_width - 640)
            if macro_width < 500 or work_height < 600:
                raise RuntimeError(
                    "The selected monitor is too small for side-by-side docking."
                )
            game_width = work_width - macro_width

            self.original_window_state = {
                "parent": win32gui.GetParent(hwnd),
                "style": win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE),
                "ex_style": win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE),
                "rect": win32gui.GetWindowRect(hwnd),
                "placement": win32gui.GetWindowPlacement(hwnd),
                "visible": bool(win32gui.IsWindowVisible(hwnd)),
            }
            self.original_macro_state = {
                "rect": win32gui.GetWindowRect(macro_hwnd),
                "placement": win32gui.GetWindowPlacement(macro_hwnd),
                "visible": bool(win32gui.IsWindowVisible(macro_hwnd)),
            }
            self._dock_work_area = (work_left, work_top, work_right, work_bottom)
            self.docked_hwnd = hwnd

            # Do NOT reparent Roblox or convert it to WS_CHILD. Cross-process
            # SetParent embedding can disrupt DPI/input behavior in games.
            # Instead, arrange two ordinary top-level windows side by side.
            self._arranging_windows = True
            # Clean up any stale capture/clip left by an earlier embedded run
            # before arranging the game as a normal top-level window.
            self._release_roblox_mouse_capture(hwnd)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                hwnd, 0, work_left, work_top, game_width, work_height,
                win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW
                | win32con.SWP_FRAMECHANGED,
            )
            win32gui.SetWindowPos(
                macro_hwnd, 0, work_left + game_width, work_top,
                macro_width, work_height,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED,
            )
            self._arranging_windows = False

            self.dock_button.configure(
                text="UNDOCK ROBLOX", bg=DANGER, fg="#FFFFFF"
            )
            self.set_status("Roblox arranged on the left; BTD Macro is on the right.")
            self.append_log(
                f"Roblox arranged side-by-side ({game_width}px game / "
                f"{macro_width}px macro). Roblox remains a normal top-level window "
                "to preserve native mouse input."
            )
        except Exception as exc:
            self._arranging_windows = False
            rollback_errors = []
            try:
                if self.docked_hwnd is not None and self.original_window_state is not None:
                    self._restore_roblox_window(self.docked_hwnd, self.original_window_state)
            except Exception as restore_exc:
                rollback_errors.append(f"Roblox restore: {restore_exc}")
            try:
                self._restore_macro_window()
            except Exception as restore_exc:
                rollback_errors.append(f"Macro window restore: {restore_exc}")

            self.docked_hwnd = None
            self.original_window_state = None
            self.original_macro_state = None
            self._dock_work_area = None
            self.dock_button.configure(
                text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
            )
            self.set_status(f"Could not dock Roblox: {exc}")
            self.append_log(f"ERROR arranging Roblox: {exc}")
            for error in rollback_errors:
                self.append_log(f"ERROR during docking rollback: {error}")

    def _on_root_configure(self, event=None) -> None:
        if (
            event is not None and event.widget is not self.root
        ) or self.docked_hwnd is None or self._arranging_windows:
            return
        # Defer until Tk has finished processing the current resize/move event.
        try:
            self.root.after_idle(self._resize_docked_window)
        except tk.TclError:
            pass

    def _resize_docked_window(self) -> None:
        """Keep Roblox filling the space immediately to the left of the macro."""
        if self.docked_hwnd is None or not PYWIN32_AVAILABLE or self._arranging_windows:
            return
        try:
            if not win32gui.IsWindow(self.docked_hwnd):
                self.set_status("Roblox window is no longer available; try undocking.")
                return
            if not win32gui.IsWindow(self.root.winfo_id()):
                return

            work_area = self._dock_work_area or self._get_work_area()
            work_left, work_top, work_right, work_bottom = work_area
            macro_left, macro_top, macro_right, macro_bottom = win32gui.GetWindowRect(
                self.root.winfo_id()
            )
            game_width = macro_left - work_left
            game_height = macro_bottom - macro_top
            if game_width < 400 or game_height < 300:
                return

            win32gui.SetWindowPos(
                self.docked_hwnd, 0, work_left, macro_top,
                game_width, game_height,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_SHOWWINDOW,
            )
        except Exception as exc:
            self.append_log(f"WARNING keeping Roblox aligned: {exc}")

    @staticmethod
    def _release_roblox_mouse_capture(hwnd: int | None = None) -> None:
        """Ask Roblox's own GUI thread to cancel capture and clear cursor clipping."""
        if hwnd is None:
            return
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetGUIThreadInfo.argtypes = [
                wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)
            ]
            user32.GetGUIThreadInfo.restype = wintypes.BOOL
            user32.PostMessageW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            ]
            user32.PostMessageW.restype = wintypes.BOOL
            user32.ClipCursor.argtypes = [ctypes.c_void_p]
            user32.ClipCursor.restype = wintypes.BOOL

            # Release any system-wide cursor clip left by a previous embedded run.
            user32.ClipCursor(None)

            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
                if info.hwndCapture:
                    user32.PostMessageW(info.hwndCapture, 0x001F, 0, 0)  # WM_CANCELMODE

            # Post to Roblox as well, in case its main window is handling capture.
            user32.PostMessageW(hwnd, 0x001F, 0, 0)  # WM_CANCELMODE
            user32.ClipCursor(None)
        except (AttributeError, OSError, ValueError):
            # Best effort only; the side-by-side design itself avoids changing
            # Roblox's parent window or client-coordinate system.
            try:
                ctypes.windll.user32.ClipCursor(None)
            except (AttributeError, OSError):
                pass

    def _restore_roblox_window(self, hwnd: int, state: dict) -> None:
        """Restore Roblox's original top-level placement without reparenting it."""
        if not PYWIN32_AVAILABLE or not win32gui.IsWindow(hwnd):
            return

        self._release_roblox_mouse_capture(hwnd)
        placement = state.get("placement")
        if placement is not None:
            win32gui.SetWindowPlacement(hwnd, placement)
        else:
            left, top, right, bottom = state["rect"]
            win32gui.SetWindowPos(
                hwnd, 0, left, top, right - left, bottom - top,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED,
            )

        if state.get("visible", True):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

        # The cursor clip is process-global, while WM_CANCELMODE is posted to
        # the Roblox GUI thread identified above. Clear the clip one more time.
        self._release_roblox_mouse_capture(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            pass

    def _restore_macro_window(self) -> None:
        state = self.original_macro_state
        if not state or not PYWIN32_AVAILABLE:
            return
        hwnd = self.root.winfo_id()
        if not win32gui.IsWindow(hwnd):
            return
        placement = state.get("placement")
        if placement is not None:
            win32gui.SetWindowPlacement(hwnd, placement)
        else:
            left, top, right, bottom = state["rect"]
            win32gui.SetWindowPos(
                hwnd, 0, left, top, right - left, bottom - top,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                | win32con.SWP_FRAMECHANGED,
            )
        if state.get("visible", True):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

    def undock_roblox(self) -> bool:
        if self.docked_hwnd is None:
            return True

        hwnd = self.docked_hwnd
        try:
            if not PYWIN32_AVAILABLE or not win32gui.IsWindow(hwnd):
                self.append_log("Roblox window was already closed; clearing dock state.")
            elif self.original_window_state is None:
                raise RuntimeError("Original Roblox window state is unavailable; cannot safely restore it.")
            else:
                self._restore_roblox_window(hwnd, self.original_window_state)

            self.docked_hwnd = None
            self._dock_work_area = None
            self._restore_macro_window()
            self.original_window_state = None
            self.original_macro_state = None
            self.dock_button.configure(
                text="DOCK ROBLOX", bg=ACCENT, fg="#101522"
            )
            self.set_status("Roblox undocked; original window position and state restored.")
            self.append_log(
                "Roblox restored as a normal top-level window. Mouse capture "
                "cancellation and cursor-clip cleanup were requested."
            )
            return True
        except Exception as exc:
            self.set_status(f"Could not fully restore Roblox: {exc}")
            self.append_log(f"ERROR undocking Roblox: {exc}")
            return False

    def close_app(self) -> None:
        # Always restore Roblox before destroying the macro window.
        if self.docked_hwnd is not None and not self.undock_roblox():
            messagebox.showerror(
                "Roblox could not be restored",
                "BTD Macro could not restore the Roblox window.\n\n"
                "The application will remain open so you can click UNDOCK ROBLOX and retry.",
                parent=self.root,
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


def main() -> None:
    configure_process_dpi_awareness()
    root = tk.Tk()
    BTDGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
