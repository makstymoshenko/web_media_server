# Web Media Server v.0.1.1
# Copyright (C) 2025 EGT Maks Tymoshenko (Ukraine)
# License: MIT
# Summary: Lightweight Web Media Server Serving Files And Media With Thumbnails, Image Rotation, And Optional ffmpeg-Based Conversion.
# ffmpeg: Bundled ffmpeg-8.0-essentials_build Preferred (Fallback To System ffmpeg); Used For Thumbnails, Full Image JPEG, Video MP4, And Audio Cover Extraction.

import os
import sys
import re
import json
import shutil
import hashlib
import threading
import subprocess
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse, quote, parse_qs
from typing import Optional, Tuple, List, Dict
import html

# ------------------------ CONFIG ------------------------

PORT = 8000

BASE_SERVER_DIR: Optional[str] = None   # _temp/_server Or SOURCE_DIR
BASE_THUMB_DIR: Optional[str] = None    # _temp/_thumbnails
BASE_COVER_DIR: Optional[str] = None    # _temp/_images/_preview
SOURCE_DIR: Optional[str] = None        # Chosen Source Folder

# Modes:
# - "ffmpeglog": Same As Now + ffmpeg Output In Console
# - "copytotemp": Normal Flow (copies To _temp/_server)
# - "nocopytotemp": No Copies, BASE_SERVER_DIR = SOURCE_DIR, Thumbnails Created
MODE: str = "copytotemp"  # Default
SHOW_FFMPEG_OUTPUT: bool = False  # Show ffmpeg Output
SHOW_AUDIO_META: bool = False  # Show Cover Art + Artist/title In Audio Player

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma"}

MAX_IMAGE_DIM = 2048
MAX_THUMB_DIM = 320

# Supported Launch Modes And Their ffmpeg Logging Flag (User Can Pick Via Dropdown).
MODE_CHOICES = {
    "nocopytotemp": ("nocopytotemp", False, False),
    "nocopytotemp_meta": ("nocopytotemp", False, True),
    "ffmpeglog": ("nocopytotemp", True, False),  # Legacy Short Name
    "nocopytotemp_ffmpeglog": ("nocopytotemp", True, False),
    "nocopytotemp_ffmpeglog_meta": ("nocopytotemp", True, True),
    "nocopytotemp_ffmpeg": ("nocopytotemp", True, False),  # Alias Requested In Examples
    "copytotemp": ("copytotemp", False, False),
    "copytotemp_meta": ("copytotemp", False, True),
    "copytotemp_ffmpeglog": ("copytotemp", True, False),
    "copytotemp_ffmpeglog_meta": ("copytotemp", True, True),
    "copytotemp_ffmpeg": ("copytotemp", True, False),  # Alias Requested In Examples
}


# -------------------- SCRIPT DIR / ffmpeg --------------------

def get_script_dir() -> str:
    """Return Folder Where Script/.Exe Is Located."""
    if getattr(sys, "frozen", False):
        try:
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass and os.path.isdir(meipass):
                return meipass
        except Exception:
            pass
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0]))


SCRIPT_DIR = get_script_dir()

def get_data_dir() -> str:
    """Return Data Directory (_data If Present Near Executable Or Script)."""
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "_data"))
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            candidates.append(os.path.join(mei, "_data"))
    candidates.append(os.path.join(SCRIPT_DIR, "_data"))
    candidates.append(SCRIPT_DIR)
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    return SCRIPT_DIR


DATA_DIR = get_data_dir()

FFMPEG_BIN = os.path.join(DATA_DIR, "ffmpeg-8.0-essentials_build", "bin")
FFMPEG_EXE = os.path.join(FFMPEG_BIN, "ffmpeg.exe")
FFPROBE_EXE = os.path.join(FFMPEG_BIN, "ffprobe.exe")


def resolve_ffmpeg(path_candidate: str, fallback_bin: str) -> str:
    """Return First Available ffmpeg/ffprobe Path Among Bundle, Executable Dir, System PATH."""
    candidates = []
    candidates.append(path_candidate)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates.append(os.path.join(exe_dir, "ffmpeg-8.0-essentials_build", "bin", os.path.basename(path_candidate)))
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        candidates.append(os.path.join(mei, "ffmpeg-8.0-essentials_build", "bin", os.path.basename(path_candidate)))
    candidates.append(os.path.join(DATA_DIR, "ffmpeg-8.0-essentials_build", "bin", os.path.basename(path_candidate)))
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    found = shutil.which(fallback_bin)
    if found:
        return found
    return fallback_bin


FFMPEG_EXE = resolve_ffmpeg(FFMPEG_EXE, "ffmpeg")
FFPROBE_EXE = resolve_ffmpeg(FFPROBE_EXE, "ffprobe")


# ---------------------- HELPERS ----------------------

def set_hidden(path: str) -> None:
    """Mark File/Folder As Hidden On Windows."""
    if os.name == "nt" and os.path.exists(path):
        subprocess.run(
            ['attrib', '+h', path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True
        )


def ensure_dpi_awareness() -> None:
    """Enable Per-Monitor DPI Awareness On Windows To Keep Icons Sharp."""
    if os.name != "nt":
        return
    try:
        import ctypes
        # Try Per-Monitor V2
        AWARENESS_PER_MONITOR_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(AWARENESS_PER_MONITOR_V2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

# Apply DPI Awareness As Early As Possible
ensure_dpi_awareness()


def set_window_icon(hwnd: int, icon_path: str) -> None:
    """Force-Set High-DPI Icon For A Tk Window Using Win32 API."""
    if os.name != "nt" or not hwnd or not icon_path:
        return
    try:
        import ctypes
        from ctypes import wintypes

        LoadImage = ctypes.windll.user32.LoadImageW
        SendMessage = ctypes.windll.user32.SendMessageW
        GetSystemMetrics = ctypes.windll.user32.GetSystemMetrics

        SM_CXICON = 11
        SM_CYICON = 12
        SM_CXSMICON = 49
        SM_CYSMICON = 50
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        WM_SETICON = 0x0080
        ICON_BIG = 1
        ICON_SMALL = 0

        big_w = GetSystemMetrics(SM_CXICON)
        big_h = GetSystemMetrics(SM_CYICON)
        small_w = GetSystemMetrics(SM_CXSMICON)
        small_h = GetSystemMetrics(SM_CYSMICON)

        hicon_big = LoadImage(
            0,
            icon_path,
            IMAGE_ICON,
            big_w,
            big_h,
            LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        hicon_small = LoadImage(
            0,
            icon_path,
            IMAGE_ICON,
            small_w,
            small_h,
            LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        if hicon_big:
            SendMessage(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            SendMessage(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
    except Exception:
        pass


def sha256_file(path: str, block_size: int = 65536) -> str:
    """Calculate SHA256 Of A File."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            h.update(chunk)
    return h.hexdigest()


def choose_source_folder() -> Optional[str]:
    """Show Tkinter Folder Picker Dialog And Return Chosen Folder."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        print("Tkinter Is Not Available. Install It Or Use Python With Tk Support.")
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title="Choose media folder for iPad 1 server")
    root.destroy()
    if not folder:
        return None
    return folder


def ensure_children_killed_on_close() -> None:
    """
    On Windows, Assign Current Process To A Job With KILL_ON_JOB_CLOSE
    So Closing The Console Kills All Child Processes.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        JobObjectExtendedLimitInformation = 9
        kernel32.SetInformationJobObject(
            h_job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        kernel32.AssignProcessToJobObject(h_job, kernel32.GetCurrentProcess())
    except Exception:
        pass

def temp_root_path() -> str:
    """Return _temp Root Path Next To Script Or .Exe."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(exe_dir, "_temp")
    return os.path.join(SCRIPT_DIR, "_temp")


def ensure_temp_root() -> str:
    """Create _temp Root If Missing And Hide It; Return The Path."""
    root = temp_root_path()
    if not os.path.exists(root):
        os.makedirs(root, exist_ok=True)
    set_hidden(root)
    return root


def ensure_images_root() -> str:
    """Create _temp/_images Root If Needed And Hide It."""
    ensure_temp_root()
    images_root = os.path.join(temp_root_path(), "_images")
    if not os.path.exists(images_root):
        os.makedirs(images_root, exist_ok=True)
    set_hidden(images_root)
    return images_root


def hide_temp_path(path: str) -> None:
    """Mark A Path And Its Parents (Within _temp) As Hidden."""
    temp_root = os.path.abspath(temp_root_path())
    current = os.path.abspath(path)
    while True:
        if not current.startswith(temp_root):
            break
        set_hidden(current)
        parent = os.path.dirname(current)
        if parent == current or parent == temp_root:
            set_hidden(parent)
            break
        current = parent


def remove_empty_dirs(path: str, stop: Optional[str] = None) -> None:
    """Remove Empty Directories Up To Stop (Excluded)."""
    if stop is None:
        stop = temp_root_path()
    stop = os.path.abspath(stop)
    current = os.path.abspath(path)
    while True:
        if not current.startswith(stop):
            break
        if current == stop:
            break
        try:
            os.rmdir(current)
        except OSError:
            break
        current = os.path.dirname(current)

def ensure_console_allocated() -> None:
    """
    Allocate/Attach A Console On Windows After User Clicks Start.
    Keeps Stdout/Stderr Visible Without Relaunching The Script.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        get_console = kernel32.GetConsoleWindow
        if get_console():
            return  # Already Has A Console

        if not kernel32.AllocConsole():
            return

        # Rebind Stdio To The New Console
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8")

        os.environ["IPAD_SERVER_OWN_CONSOLE"] = "1"
    except Exception:
        pass


def launch_in_new_console(mode_key: str, port: int, folder: str) -> bool:
    """
    Relaunch The Script In A Fresh Console Window (Windows).
    Passes Mode/Port/Folder Via Environment; Returns True If Launched.
    """
    if os.name != "nt":
        return False
    try:
        env = os.environ.copy()
        env["IPAD_SERVER_OWN_CONSOLE"] = "1"
        env["IPAD_SERVER_MODE"] = mode_key
        env["IPAD_SERVER_PORT"] = str(port)
        env["IPAD_SERVER_FOLDER"] = folder

        if getattr(sys, "frozen", False):
            prog = sys.executable
            args = [prog, *sys.argv[1:]]
        else:
            prog = sys.executable or "python"
            script_path = os.path.abspath(sys.argv[0])
            args = [prog, script_path, *sys.argv[1:]]

        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(args, env=env, creationflags=creationflags)
        return True
    except Exception:
        return False


def shorten_path_display(path: str, max_len: int = 70) -> str:
    """
    Return A Shortened Path With Leading Ellipsis If It Exceeds Max_len.
    Keeps Deepest Parts; Example: .../Folder3/Folder4/File.Mp3
    """
    if not path:
        return "/"
    normalized = path.replace("\\", "/")
    if len(normalized) <= max_len:
        return normalized
    parts = normalized.split("/")
    while len("/".join(["...", *parts])) > max_len and len(parts) > 1:
        parts = parts[1:]
    return "/".join(["...", *parts])


def apply_mode_from_key(mode_key: str) -> bool:
    """
    Set MODE And SHOW_FFMPEG_OUTPUT According To A Dropdown Key.
    Returns False If The Key Is Unknown.
    """
    global MODE, SHOW_FFMPEG_OUTPUT, SHOW_AUDIO_META

    key = mode_key.lower()
    if key not in MODE_CHOICES:
        return False

    base_mode, ffmpeg_flag, audio_meta_flag = MODE_CHOICES[key]
    MODE = base_mode
    SHOW_FFMPEG_OUTPUT = ffmpeg_flag
    SHOW_AUDIO_META = audio_meta_flag
    return True


def prompt_mode_and_port(default_mode_key: str, default_port: int) -> Optional[Tuple[str, int]]:
    """
    Show A Small GUI To Pick Run Mode And Port.
    Returns (Mode_key, Port) Or None If Cancelled/Unavailable.
    """
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("Tkinter Is Not Available. Install It Or Use Python With Tk Support.")
        return None

    ensure_dpi_awareness()

    root = tk.Tk()
    root.withdraw()  # Hide Until Positioned To Avoid Visible Jump
    root.title("Web Media Server")
    try:
        ico_candidates = ["app_icon.ico", "icon.ico", "favicon.ico"]
        ico_path = None
        for name in ico_candidates:
            candidate = os.path.join(DATA_DIR, name)
            if os.path.isfile(candidate):
                ico_path = candidate
                break
        if ico_path:
            root.iconbitmap(ico_path)
            try:
                set_window_icon(root.winfo_id(), ico_path)
            except Exception:
                pass
    except Exception:
        pass
    desired_w, desired_h = 282, 122  # Keep Window Size
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#393A3B")
    # Center The Window On Screen Using The Configured Size Before Showing It.
    x = (root.winfo_screenwidth() // 2) - (desired_w // 2)
    y = (root.winfo_screenheight() // 2) - (desired_h // 2)
    root.geometry(f"{desired_w}x{desired_h}+{x}+{y}")
    base_font = ("Segoe UI", 8)
    root.option_add("*Font", base_font)

    def attach_tooltip(widget, text: str) -> None:
        tip = {"win": None}

        def show(_event=None) -> None:
            if tip["win"] is not None:
                return
            tw = tk.Toplevel(widget)
            tip["win"] = tw
            tw.wm_overrideredirect(True)
            tw.attributes("-topmost", True)
            x_pos = widget.winfo_rootx() + 12
            y_pos = widget.winfo_rooty() + widget.winfo_height() + 6
            tw.wm_geometry(f"+{x_pos}+{y_pos}")
            lbl = tk.Label(
                tw,
                text=text,
                justify="left",
                bg="#2e2e2e",
                fg="#f0f0f0",
                relief="solid",
                borderwidth=1,
                font=base_font,
                padx=6,
                pady=4,
            )
            lbl.pack()

        def hide(_event=None) -> None:
            if tip["win"] is not None:
                try:
                    tip["win"].destroy()
                except Exception:
                    pass
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)
        widget.bind("<FocusOut>", hide)

    ordered_mode_keys = [
        "nocopytotemp",                # Original Destination (1)
        "nocopytotemp_meta",           # Original Destination (2)
        "nocopytotemp_ffmpeglog",      # Original Destination (3)
        "nocopytotemp_ffmpeglog_meta", # Original Destination (4)
        "copytotemp",                  # Temporary Destination (1)
        "copytotemp_meta",             # Temporary Destination (2)
        "copytotemp_ffmpeglog",        # Temporary Destination (3)
        "copytotemp_ffmpeglog_meta",   # Temporary Destination (4)
    ]
    mode_labels = {
        "nocopytotemp": "Original Destination (1)",
        "nocopytotemp_meta": "Original Destination (2)",
        "nocopytotemp_ffmpeglog": "Original Destination (3)",
        "nocopytotemp_ffmpeglog_meta": "Original Destination (4)",
        "copytotemp": "Temporary Destination (1)",
        "copytotemp_meta": "Temporary Destination (2)",
        "copytotemp_ffmpeglog": "Temporary Destination (3)",
        "copytotemp_ffmpeglog_meta": "Temporary Destination (4)",
    }
    display_labels = {k: f"{mode_labels[k]}" for k in ordered_mode_keys}
    mode_options = [display_labels[k] for k in ordered_mode_keys]
    label_to_key = {v: k for k, v in mode_labels.items()}

    default_key = default_mode_key if default_mode_key in MODE_CHOICES else "nocopytotemp"
    mode_var = tk.StringVar(value=display_labels.get(default_key, mode_options[0]))
    port_var = tk.StringVar(value=str(default_port))
    result: List[Optional[Tuple[str, int]]] = [None]

    # Dark Style For Ttk Widgets To Match Window Background.
    try:
        root_style = ttk.Style()
        if "clam" in root_style.theme_names():
            root_style.theme_use("clam")
        root_style.configure(
            "Dark.TCombobox",
            fieldbackground="#393A3B",
            background="#393A3B",
            foreground="#f0f0f0",
            arrowcolor="#f0f0f0",
            bordercolor="#333333",
            relief="flat",
            font=base_font,
            padding=(4, 6, 4, 6),
        )
        root_style.configure(
            "Dark.TEntry",
            fieldbackground="#393A3B",
            background="#393A3B",
            foreground="#f0f0f0",
            bordercolor="#333333",
            relief="flat",
            padding=(4, 6, 4, 6),
            insertcolor="#f0f0f0",
            font=base_font,
        )
        root_style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#393A3B")],
            foreground=[("readonly", "#f0f0f0")],
        )
    except Exception:
        pass

    def on_start() -> None:
        selected_label = mode_var.get().strip()
        mode_choice = label_to_key.get(selected_label, "").lower()
        if mode_choice not in MODE_CHOICES:
            messagebox.showerror("Invalid mode", "Choose a mode from the dropdown.")
            return

        port_text = port_var.get().strip()
        if not port_text.isdigit():
            messagebox.showerror("Invalid port", "Port must be an integer 1-65535.")
            return

        port_value = int(port_text)
        if port_value < 1 or port_value > 65535:
            messagebox.showerror("Invalid port", "Port must be in range 1-65535.")
            return

        result[0] = (mode_choice, port_value)
        root.destroy()

    def on_cancel() -> None:
        result[0] = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_cancel)

    padding_server_label = {"padx": (18, 4), "pady": (8, 5)}
    padding_server_field = {"padx": (6, 18), "pady": (8, 5)}
    padding_port_label = {"padx": (18, 6), "pady": (2, 8)}
    padding_port_field = {"padx": (6, 18), "pady": (2, 8)}

    tk.Label(root, text="Server:", fg="#f0f0f0", bg="#393A3B", font=base_font).grid(row=0, column=0, sticky="w", **padding_server_label)
    mode_combo = ttk.Combobox(root, textvariable=mode_var, values=mode_options, state="readonly", style="Dark.TCombobox", font=base_font)
    # Prevent Selecting/Highlighting The Text Inside The Combobox Entry Area.
    mode_combo.bind("<FocusIn>", lambda e: e.widget.selection_clear())
    mode_combo.bind("<<ComboboxSelected>>", lambda e: e.widget.selection_clear())
    mode_combo.bind("<ButtonRelease-1>", lambda e: e.widget.selection_clear())
    mode_combo.configure(takefocus=0)
    mode_combo.grid(row=0, column=1, sticky="ew", ipady=0, **padding_server_field)
    tooltip_text = (
        "Original Destination (1): Server From Selected Folder\n"
        "Original Destination (2): Server From Selected Folder + Meta/Tags From Audiofiles\n"
        "Original Destination (3): Server From Selected Folder + Logs\n"
        "Original Destination (4): Server From Selected Folder + Logs + Meta/Tags From Audiofiles\n"
        "Temporary Destination (1): Copy/Convert Into _temp/\n"
        "Temporary Destination (2): Copy/Convert Into _temp/ + Meta/Tags From Audiofiles\n"
        "Temporary Destination (3): Copy/Convert Into _temp/ + Logs\n"
        "Temporary Destination (4): Copy/Convert Into _temp/ + Logs + Meta/Tags From Audiofiles"
    )
    attach_tooltip(mode_combo, tooltip_text)

    tk.Label(root, text="Port:", fg="#f0f0f0", bg="#393A3B", font=base_font).grid(row=1, column=0, sticky="w", **padding_port_label)
    port_entry = ttk.Entry(root, textvariable=port_var, style="Dark.TEntry", font=base_font)
    port_entry.configure(takefocus=0)
    port_entry.grid(row=1, column=1, sticky="ew", ipady=0, **padding_port_field)
    attach_tooltip(port_entry, "HTTP Port for The Server (1-65535); Use A Free Port; Default Is 8000.")

    button_frame = tk.Frame(root, bg="#393A3B")
    button_frame.grid(row=2, column=0, columnspan=2, pady=0)

    start_bg = "#555555"
    start_hover = "#5f5f5f"
    start_active = "#303030"

    start_btn = tk.Button(
        button_frame,
        text="Start",
        command=on_start,
        width=43,
        height=2,
        bg=start_bg,
        fg="#f0f0f0",
        activebackground=start_active,
        activeforeground="#ffffff",
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground="#404040",
        highlightcolor="#505050",
        font=base_font,
        takefocus=0,
    )
    start_btn.pack(side="left", padx=6)
    start_btn.bind("<Enter>", lambda _e: start_btn.configure(bg=start_hover))
    start_btn.bind("<Leave>", lambda _e: start_btn.configure(bg=start_bg))

    root.grid_columnconfigure(1, weight=1)

    # Clear Initial Focus; Move It To The Window Itself (entries/buttons Stay Focusable Via Mouse/tab).
    root.after(50, root.focus_force)
    root.deiconify()  # Show After Positioning To Avoid Visible Jump

    root.mainloop()
    return result[0]


def ensure_dirs() -> None:
    """
    Prepare Base Paths For _temp Subfolders.
    Creation Is Deferred Until Needed To Avoid Empty Folders.
    In Nocopytotemp Mode, BASE_SERVER_DIR Will Be Set To SOURCE_DIR Later.
    """
    global BASE_SERVER_DIR, BASE_THUMB_DIR, BASE_COVER_DIR

    temp_root = temp_root_path()
    images_root = os.path.join(temp_root, "_images")
    BASE_THUMB_DIR = os.path.join(images_root, "_thumbnails")
    BASE_COVER_DIR = os.path.join(images_root, "_preview")

    # BASE_SERVER_DIR Is Determined By The Chosen Mode
    if MODE == "nocopytotemp":
        # Will Be Set Later In Main() After Folder Selection
        BASE_SERVER_DIR = None
    else:
        # Copytotemp And ffmpeglog Modes Serve From _temp/_server
        BASE_SERVER_DIR = os.path.join(temp_root, "_server")

def print_progress(prefix: str, rel_path: str, current: int, total: int) -> None:
    """Simple Console Progress Bar For Copying Files."""
    bar_len = 30
    if total <= 0:
        percent = 100.0
        filled = bar_len
    else:
        percent = (current / float(total)) * 100.0
        filled = int(bar_len * current / float(total))

    bar = "#" * filled + "-" * (bar_len - filled)
    sys.stdout.write(f"\r{prefix} {rel_path} [{bar}] {percent:5.1f}%")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def copy_with_progress(src: str, dst: str, rel_path: str) -> None:
    """Copy File With Visual Progress Bar."""
    total = os.path.getsize(src)
    done = 0
    chunk = 1024 * 1024

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            buf = fsrc.read(chunk)
            if not buf:
                break
            fdst.write(buf)
            done += len(buf)
            print_progress("[COPY]", rel_path, done, total)

    try:
        shutil.copystat(src, dst)
    except Exception:
        pass


# ---------------------- FFPROBE ----------------------

def ffprobe_resolution(path: str) -> Tuple[int, int]:
    """Return (Width, Height) Using Ffprobe, Or (0,0) On Error."""
    try:
        r = subprocess.run(
            [
                FFPROBE_EXE,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                path,
            ],
            capture_output=True,
            text=True,
        )
        line = r.stdout.strip()
        if not line:
            return 0, 0
        w_str, h_str = line.split("x")
        return int(w_str), int(h_str)
    except Exception:
        return 0, 0


def ffprobe_fps(path: str) -> float:
    """Return FPS Using Ffprobe, Or 30.0 On Error."""
    try:
        r = subprocess.run(
            [
                FFPROBE_EXE,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
        )
        fps_raw = r.stdout.strip()
        if not fps_raw:
            return 30.0
        if "/" in fps_raw:
            a, b = fps_raw.split("/")
            return float(a) / float(b)
        return float(fps_raw)
    except Exception:
        return 30.0


def ffprobe_audio_tags(path: str) -> Dict[str, str]:
    """Return Audio Tags (Lowercased Keys) Using Ffprobe."""
    try:
        r = subprocess.run(
            [
                FFPROBE_EXE,
                "-v",
                "error",
                "-show_entries",
                "format_tags",
                "-print_format",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout or "{}")
        tags = {}
        if isinstance(data, dict):
            raw_tags = data.get("format", {}).get("tags", {}) or {}
            for k, v in raw_tags.items():
                if not v:
                    continue
                tags[str(k).lower()] = str(v).strip()
        return tags
    except Exception:
        return {}


# ---------------------- NETWORK INFO ----------------------

def detect_ipv4_ipconfig() -> Optional[str]:
    """
    Try To Grab The First Non-Loopback IPv4 From `Ipconfig` Output.
    Handles Localized Labels By Scanning For An IPv4-Looking Pattern.
    Returns None On Failure.
    """
    try:
        proc = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            errors="ignore",
        )
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        for line in text.splitlines():
            matches = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line)
            for candidate in matches:
                if candidate.startswith(("127.", "0.", "169.254.")):
                    continue
                return candidate
        return None
    except Exception:
        return None


def print_connect_hint(ipv4: Optional[str]) -> None:
    """
    Print A Connection Hint For The User (Always Prints Something).
    """
    if ipv4:
        print(f"\nConnect: http://{ipv4}:{PORT}")
    else:
        print("Could Not Auto-detect IPv4 Via ipconfig. Run `ipconfig` Manually And Use That IP With The Port Above.")


def serving_mode_message() -> str:
    """Return Human-Readable Serving Mode Description."""
    if MODE == "nocopytotemp":
        return "Files Will Be Served Directly From Source Folder Without Modification"
    return "Files Will Be Served From Temporary _temp/_server Folder - Copied/Converted As Needed"


def print_connect_block() -> None:
    """Uniformly Print The Connection Info Block After Sync Messages."""
    print(f"\n{serving_mode_message()}")
    print_connect_hint(detect_ipv4_ipconfig())
    print()
    print("Press Ctrl+C To Stop (Or Close This Window)")


# ---------------------- IMAGE CONVERSION ----------------------

def convert_image_full(src: str, dst: str) -> None:
    """
    Convert Any Image To:
      - Baseline JPEG
      - Max Dimension <= MAX_IMAGE_DIM
      - Good Quality (Q=2)
    """
    w, h = ffprobe_resolution(src)

    if w == 0 or h == 0:
        scale = f"scale={MAX_IMAGE_DIM}:-2"
    else:
        if w >= h:
            scale = f"scale={MAX_IMAGE_DIM}:-2"
        else:
            scale = f"scale=-2:{MAX_IMAGE_DIM}"

    cmd = [
        FFMPEG_EXE,
        "-y",
        "-i", src,
        "-vf", scale,
        "-frames:v", "1",
        "-q:v", "2",
        "-pix_fmt", "yuvj420p",
        dst,
    ]
    if SHOW_FFMPEG_OUTPUT:
        # Show Stdout And Stderr In Console To Inspect ffmpeg Errors
        # Do Not Redirect So Full ffmpeg Output Is Visible
        subprocess.run(cmd)
    else:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def convert_thumbnail(src: str, dst: str) -> None:
    """
    Convert Image To Small Thumbnail:
      - Max Side ~ MAX_THUMB_DIM
      - Keep Aspect Ratio, No Crop
    """
    cmd = [
        FFMPEG_EXE,
        "-y",
        "-i", src,
        "-vf", f"scale={MAX_THUMB_DIM}:{MAX_THUMB_DIM}:force_original_aspect_ratio=decrease",
        "-frames:v", "1",
        "-q:v", "5",
        "-pix_fmt", "yuvj420p",
        dst,
    ]
    if SHOW_FFMPEG_OUTPUT:
        # Show Stdout And Stderr In Console To Inspect ffmpeg Errors
        # Do Not Redirect So Full ffmpeg Output Is Visible
        subprocess.run(cmd)
    else:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------- VIDEO CONVERSION ----------------------

def convert_video(src: str, dst: str) -> None:
    """
    Convert Video To IPad1 Safari-Friendly MP4:
      - H.264 Baseline, Level 3.0
      - Yuv420p
      - Scaled To 1024 (Landscape) Or 768 (Portrait)
      - Force 30 Fps If Original > 30
    """
    w, h = ffprobe_resolution(src)
    fps = ffprobe_fps(src)

    if w == 0 or h == 0:
        scale = "scale=1024:-2"
    else:
        scale = "scale=1024:-2" if w >= h else "scale=-2:768"

    cmd = [
        FFMPEG_EXE,
        "-noautorotate",
        "-i", src,
        "-vf", scale,
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-b:v", "4000k",
        "-maxrate", "4500k",
        "-bufsize", "9000k",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-y",
    ]

    if fps > 30.0:
        # Insert "-r 30" Together (must Keep The Flag Before The Value)
        insert_at = cmd.index("-c:v")
        cmd[insert_at:insert_at] = ["-r", "30"]

    cmd.append(dst)
    if SHOW_FFMPEG_OUTPUT:
        # Show Stdout And Stderr In Console To Inspect ffmpeg Errors
        # Do Not Redirect So Full ffmpeg Output Is Visible
        subprocess.run(cmd)
    else:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------- SYNC / PROCESSING ----------------------

def build_sorted_file_list(source_folder: str) -> List[Tuple[str, int]]:
    """
    Return List Of (Rel_path, Size) For All Files, Sorted By Size Ascending.
    """
    result: List[Tuple[str, int]] = []

    for root, _dirs, files in os.walk(source_folder):
        rel_root = os.path.relpath(root, source_folder)
        if rel_root == ".":
            rel_root = ""
        for name in files:
            rel = os.path.normpath(os.path.join(rel_root, name))
            full = os.path.join(source_folder, rel)
            try:
                size = os.path.getsize(full)
            except Exception:
                size = 0
            result.append((rel, size))

    result.sort(key=lambda x: x[1])
    return result


def cover_rel_path(rel_path: str) -> str:
    """Return Relative Path (Under _preview) For An Audio Preview Jpg."""
    base_no_ext = os.path.splitext(rel_path)[0]
    return base_no_ext + "_preview.jpg"


def extract_audio_cover(src: str, cover_full: str) -> bool:
    """Try To Extract Embedded Cover Art; Return True On Success."""
    ensure_images_root()
    os.makedirs(os.path.dirname(cover_full), exist_ok=True)
    hide_temp_path(os.path.dirname(cover_full))

    # First Try Copying Attached Picture Without Re-encoding.
    cmd_copy = [
        FFMPEG_EXE,
        "-y",
        "-i", src,
        "-map", "0:v:0",
        "-c:v", "copy",
        cover_full,
    ]
    try:
        subprocess.run(
            cmd_copy,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception:
        cmd_copy = None

    if cmd_copy and os.path.exists(cover_full) and os.path.getsize(cover_full) > 0:
        set_hidden(cover_full)
        return True

    # Fallback: Grab First Video Frame As Jpeg.
    cmd_frame = [
        FFMPEG_EXE,
        "-y",
        "-i", src,
        "-map", "0:v:0",
        "-frames:v", "1",
        "-q:v", "2",
        cover_full,
    ]
    try:
        subprocess.run(
            cmd_frame,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception:
        pass

    if os.path.exists(cover_full) and os.path.getsize(cover_full) > 0:
        set_hidden(cover_full)
        return True

    # Cleanup Empty File If Created.
    try:
        if os.path.exists(cover_full) and os.path.getsize(cover_full) == 0:
            os.remove(cover_full)
    except Exception:
        pass
    remove_empty_dirs(os.path.dirname(cover_full))
    return False


def generate_audio_covers(source_folder: str, sorted_files: List[Tuple[str, int]]) -> None:
    """
    Extract Previews From All Audio Files Into _temp/_images/_preview
    Naming: Folder/Track.mp3 -> Folder/Track_preview.jpg
    """
    if BASE_COVER_DIR is None:
        return

    print("\n=== Extracting Audio Previews (Smallest Files First) ===\n")

    for rel, _size in sorted_files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in AUDIO_EXT:
            continue

        src = os.path.join(source_folder, rel)
        cover_rel = cover_rel_path(rel)
        cover_full = os.path.join(BASE_COVER_DIR, cover_rel)

        if os.path.exists(cover_full) and os.path.getsize(cover_full) > 0:
            print(f"[SKIP] {rel} [Preview Already Exists]")
            continue

        try:
            ok = extract_audio_cover(src, cover_full)
            if ok:
                print(f"[PREVIEW] {rel} -> {cover_rel}")
            else:
                print(f"[NO PREVIEW] {rel}")
        except Exception as e:
            print(f"[ERROR PREVIEW] {rel}: {e}")


def generate_thumbnails_first(source_folder: str, sorted_files: List[Tuple[str, int]]) -> None:
    """
    Generate Thumbnails For All Images First
    Thumbnail Naming Rule:
      Server File:   Folder/IMG_4126.jpg
      Thumbnail:     Folder/IMG_4126_thumb.jpg
    """
    if BASE_THUMB_DIR is None:
        return

    print("\n=== Generating Thumbnails (Smallest Files First) ===\n")

    for rel, _size in sorted_files:
        src = os.path.join(source_folder, rel)
        ext = os.path.splitext(src)[1].lower()
        if ext not in IMAGE_EXT:
            continue

        base_no_ext = os.path.splitext(rel)[0]
        thumb_rel = base_no_ext + "_thumb.jpg"
        thumb_full = os.path.join(BASE_THUMB_DIR, thumb_rel)

        thumb_dir = os.path.dirname(thumb_full)
        ensure_images_root()
        os.makedirs(thumb_dir, exist_ok=True)
        hide_temp_path(thumb_dir)

        if os.path.exists(thumb_full):
            print(f"[SKIP] {thumb_rel} [Preview Already Exists]")
            continue

        try:
            print(f"[THUMB] {rel} -> {thumb_rel}")
            convert_thumbnail(src, thumb_full)
            set_hidden(thumb_full)
        except Exception as e:
            print(f"[ERROR THUMB] {rel}: {e}")
            remove_empty_dirs(thumb_dir)


def process_file_to_server(source_folder: str, rel_path: str) -> None:
    """
    Process A Single File Into BASE_SERVER_DIR:
      - Audio: 1:1 Copy (Same Name/Extension) - Only In Copytotemp/ffmpeglog
      - Video: Convert To .Mp4 With SAME Base Name
      - Image: Convert To .Jpg With SAME Base Name
      - Other: 1:1 Copy - Only In Copytotemp/ffmpeglog
    In Nocopytotemp Mode Nothing Is Done - Files Stay As-Is.
    """
    if BASE_SERVER_DIR is None:
        return

    # In Nocopytotemp Mode We Neither Copy Nor Convert
    if MODE == "nocopytotemp":
        return

    src = os.path.join(source_folder, rel_path)
    ext = os.path.splitext(src)[1].lower()
    base_no_ext = os.path.splitext(rel_path)[0]

    if ext in AUDIO_EXT:
        dst_rel = rel_path
    elif ext in VIDEO_EXT:
        dst_rel = base_no_ext + ".mp4"
    elif ext in IMAGE_EXT:
        dst_rel = base_no_ext + ".jpg"
    else:
        dst_rel = rel_path

    dst = os.path.join(BASE_SERVER_DIR, dst_rel)
    dst_dir = os.path.dirname(dst)
    ensure_temp_root()
    os.makedirs(dst_dir, exist_ok=True)
    hide_temp_path(dst_dir)
    
    # In Nocopytotemp Mode Do Not Mark Source Files/folders As Hidden
    is_source_dir = (MODE == "nocopytotemp" and BASE_SERVER_DIR == SOURCE_DIR)

    # Non-media Or Audio → Copy With Hash Check
    if ext in AUDIO_EXT or (ext not in VIDEO_EXT and ext not in IMAGE_EXT):
        if os.path.exists(dst):
            try:
                if sha256_file(src) == sha256_file(dst):
                    print(f"[SKIP] {rel_path} [Audio Already Exists]")
                    return
            except Exception:
                pass

        print(f"[COPY] {rel_path} -> {dst_rel}")
        copy_with_progress(src, dst, rel_path)
        if not is_source_dir:
            set_hidden(dst)
        return

    # Video
    if ext in VIDEO_EXT:
        if os.path.exists(dst):
            print(f"[OVERWRITE VIDEO] {rel_path} -> {dst_rel}")
        else:
            print(f"[VIDEO] {rel_path} -> {dst_rel}")
        convert_video(src, dst)
        if not is_source_dir:
            set_hidden(dst)
        return

    # Image
    if ext in IMAGE_EXT:
        if os.path.exists(dst):
            print(f"[OVERWRITE IMAGE] {rel_path} -> {dst_rel}")
        else:
            print(f"[IMAGE] {rel_path} -> {dst_rel}")
        convert_image_full(src, dst)
        if not is_source_dir:
            set_hidden(dst)
        return


def sync_all(source_folder: str) -> None:
    """
    Full Sync Pipeline:
      1) Build Sorted File List (By Size Asc)
      2) Generate Thumbnails
      3) Convert/Copy Files Into _server
    """
    print(f"\n=== Building Sorted File List ===")
    sorted_files = build_sorted_file_list(source_folder)
    print(f"\nFound: {len(sorted_files)} Files")

    generate_thumbnails_first(source_folder, sorted_files)
    if SHOW_AUDIO_META:
        generate_audio_covers(source_folder, sorted_files)

    if MODE == "nocopytotemp":
        print("\n=== Sync Finished ===")
        print_connect_block()
    else:
        print("\n=== Syncing Into _server (Smallest Files First) ===\n")
        for rel, _size in sorted_files:
            try:
                process_file_to_server(source_folder, rel)
            except Exception as e:
                print(f"[ERROR] {rel}: {e}")
        print("\n=== Sync Finished ===")
        print_connect_block()


# ---------------------- HTML / PLAYER ----------------------

def wrap_html(title: str, body: str) -> str:
    """
    Wrap Body Into Full HTML + CSS.
    Player Is Large (Max-Height: 95vh), But Not Fullscreen.
    """
    style = """
    <style>
        *, *::before, *::after {
            box-sizing: border-box;
        }
        :root {
            color-scheme: dark;
        }
        * {
            -webkit-tap-highlight-color: transparent;
        }
        body {
            margin: 0;
            padding: 16px;
            background-color: #1F1F1F;
            color: #eee;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            overflow-x: hidden;
            -webkit-tap-highlight-color: transparent;
            -webkit-touch-callout: none;
        }
        a {
            color: #a5c9ff;
            text-decoration: none;
            padding: 2px 2px;
            border-radius: 4px;
            -webkit-tap-highlight-color: transparent;
            -webkit-touch-callout: none;
            outline: none;
        }
        a:hover {
            background-color: transparent;
        }
        a:active {
            background-color: transparent;
        }
        a:focus {
            outline: none;
        }
        a:focus-visible {
            outline: none;
        }
        h2 {
            margin-top: 0;
            user-select: none;
            -webkit-user-select: none;
        }
        .nav-links a {
            margin-right: 12px;
        }
        .path-display {
        }
        ul {
            list-style: none;
            padding-left: 0;
        }
        li {
            margin: 4px 0;
            list-style: none;
            width: 100%;
        }
        .item-row {
            background-color: #2f2f2f;
            padding: 6px 4px;
            border-radius: 4px;
            display: table;
            width: 100%;
            border-spacing: 4px 0;
            min-height: 66px;
            height: 66px;
            box-sizing: border-box;
            user-select: none;
            -webkit-user-select: none;
            -webkit-tap-highlight-color: transparent;
            outline: none;
            touch-action: manipulation;
        }
        .item-row:hover { background-color: #343434; }
        .item-row:active { background-color: #2f2f2f; }
        html.ipad1 .item-row:hover,
        html.ipad1 .item-row:active,
        html.ipad1 .clickable-row:hover,
        html.ipad1 .clickable-row:active {
            background-color: #2f2f2f !important;
        }
        /* Disable hover/active highlighting on touch devices */
        @media (pointer: coarse) {
            .item-row:hover,
            .item-row:active {
                background-color: #2f2f2f !important;
            }
        }
        .item-row.touch-hover {
            background-color: #2f2f2f;
        }
        @media (pointer: coarse) {
            .item-row:hover {
                background-color: #2f2f2f;
            }
            .clickable-row:hover {
                background-color: #2f2f2f;
            }
            .item-row:active,
            .clickable-row:active {
                background-color: #2f2f2f;
            }
        }
        .clickable-row {
            cursor: pointer;
            -webkit-tap-highlight-color: transparent;
            outline: none;
            user-select: none;
            -webkit-user-select: none;
            -webkit-touch-callout: none;
            touch-action: manipulation;
        }
        .clickable-row:focus,
        .clickable-row:focus-visible,
        .clickable-row *:focus,
        .clickable-row *:focus-visible { outline: none; }
        .placeholder-row {
            background-color: #262626;
            border-radius: 4px;
            display: table;
            width: 100%;
            border-spacing: 4px 0;
            min-height: 66px;
            height: 66px;
            box-sizing: border-box;
            user-select: none;
            -webkit-user-select: none;
        }
        .placeholder-row:hover {
            background-color: #262626;
        }
        .item-row .file-icon,
        .item-row .file-label {
            display: table-cell;
            vertical-align: middle;
        }
        .item-row .file-icon {
            width: 28px;
            text-align: center;
            white-space: nowrap;
            vertical-align: middle;
            background-repeat: no-repeat;
            background-position: center center;
            background-size: 20px 20px;
        }
        .clickable-row {
            cursor: pointer;
        }
        .item-row .icon-folder {
            background-image: url('/thumbnail_folder.png');
            background-size: 20px 20px;
            width: 28px;
            height: 28px;
        }
        .item-row .icon-image {
            background-image: url('/thumbnail_image.png');
            background-size: 20px 20px;
            width: 28px;
            height: 28px;
        }
        .item-row .icon-video {
            background-image: url('/thumbnail_video.png');
            background-size: 20px 20px;
            width: 28px;
            height: 28px;
        }
        .item-row .icon-audio {
            background-image: url('/thumbnail_audio.png');
            background-size: 20px 20px;
            width: 28px;
            height: 28px;
        }
        .item-row .icon-file {
            background-image: url('/thumbnail_file.png');
            background-size: 20px 20px;
            width: 28px;
            height: 28px;
        }
        .control-rotate {
            background-image: url('/thumbnail_rotate.png');
        }
        .placeholder-row {
            background-color: #262626;
            border-radius: 4px;
            display: table;
            width: 100%;
            border-spacing: 4px 0;
            min-height: 66px;
            height: 66px;
            box-sizing: border-box;
            user-select: none;
            -webkit-user-select: none;
            margin: 4px 0;
        }
        .thumb-row {
            display: table;
            width: 100%;
            border-spacing: 4px 0;
        }
        .thumb-row .thumb-link,
        .thumb-row .file-label {
            display: table-cell;
            vertical-align: middle;
        }
        .thumb-row .thumb-link {
            width: 1%;
            white-space: nowrap;
        }
        .thumb-row .file-label {
            width: 99%;
        }
        .clickable-row a {
            -webkit-tap-highlight-color: transparent;
            outline: none;
        }
        .header-row {
            display: table;
            width: 100%;
        }
        .header-row h2 {
            margin: 0;
            display: table-cell;
            vertical-align: top;
        }
        .header-controls {
            display: table-cell;
            vertical-align: top;
            text-align: right;
            width: 1%;
            white-space: nowrap;
        }
        .header-controls a {
            display: inline-block;
        }
        .path-display {
            color: #b0b0b0;
            font-size: 14px;
            margin: 4px 0 8px;
            word-break: break-all;
        }
        .thumb {
            height: 54px;
            width: auto;
            max-width: 120px;
            object-fit: contain;
            border-radius: 4px;
            flex-shrink: 0;
            background-color: #222;
            display: block;
            user-select: none;
            -webkit-user-select: none;
        }
        .file-label {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 70vw;
            flex: 1;
            display: block;
            user-select: none;
            -webkit-user-select: none;
        }
        .image-viewer {
            text-align: center;
            margin-top: 16px;
            padding: 12px;
            display: -webkit-box;
            display: flex;
            -webkit-box-align: center;
            -webkit-box-pack: center;
            align-items: center;
            justify-content: center;
            position: relative;
            overflow: visible;
            width: 100%;
            max-width: 100vw;
            margin-left: auto;
            margin-right: auto;
            border-radius: 6px;
        }
        .image-viewer img {
            max-width: 90vw;
            height: auto;
            display: block;
            margin: 0 auto;
            border-radius: 4px;
            touch-action: pinch-zoom;
            transform-origin: center center;
            -webkit-transform-origin: center center;
            transition: transform 0.2s ease;
            -webkit-transition: -webkit-transform 0.2s ease;
        }
        .controls {
            position: relative;
            z-index: 2;
            margin-bottom: 32px;
            user-select: none;
            -webkit-user-select: none;
            max-width: 100%;
        }
        .controls a,
        .controls button,
        .controls .control-btn {
            display: inline-block;
            margin-right: 12px;
            padding: 6px 10px;
            border-radius: 6px;
            background-color: #2a2a2a;
            color: #a5c9ff;
            border: none;
            cursor: pointer;
            font: inherit;
            background-repeat: no-repeat;
            background-position: 8px center;
            background-size: 16px 16px;
            padding-left: 30px;
        }
        .controls a:hover,
        .controls button:hover,
        .controls .control-btn:hover {
            background-color: #333;
        }
        .controls .disabled {
            opacity: 0.5;
            cursor: default;
            pointer-events: none;
        }
        .control-back {
            background-image: url('/thumbnail_back.png');
        }
        .control-prev {
            background-image: url('/thumbnail_left.png');
        }
        .control-next {
            background-image: url('/thumbnail_right.png');
        }
        .control-rotate {
            background-image: url('/thumbnail_rotate.png');
        }
        .control-prev {
            background-image: url('/thumbnail_left.png');
        }
        .control-next {
            background-image: url('/thumbnail_right.png');
        }
        .control-mode {
            background-image: url('/thumbnail_mode.png');
        }
        .media-player {
            margin-top: 12px;
        }
        .cover-art {
            margin-top: 14px;
            text-align: center;
        }
        .cover-art img {
            width: 256px;
            height: 256px;
            max-width: 80vw;
            max-height: 80vw;
            object-fit: cover;
            border-radius: 8px;
            background-color: #151515;
            box-shadow: 0 6px 20px rgba(0,0,0,0.35);
        }
        .cover-meta {
            margin-top: 8px;
            text-align: center;
            font-weight: 600;
            color: #f0f0f0;
        }
        audio, video {
            width: 100%;
            max-height: 95vh;
            outline: none;
        }
    </style>
    """
    head = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1.0, user-scalable=yes">
        <title>{title}</title>
        <link rel="icon" type="image/x-icon" href="/favicon.ico">
        <link rel="icon" type="image/x-icon" sizes="32x32" href="/favicon.ico">
        <link rel="shortcut icon" href="/favicon.ico">
        <link rel="icon" type="image/png" sizes="any" href="/apple-touch-icon.png">
        <link rel="icon" type="image/png" sizes="180x180" href="/apple-touch-icon.png">
        <link rel="apple-touch-icon" href="/apple-touch-icon.png">
        <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
        <script>
        (function() {{
            var ua = navigator.userAgent || "";
            var isIpad = ua.indexOf("iPad") !== -1;
            // iPad 1 maxes out at iOS 5.x; disable hover highlight there to avoid the double-tap quirk.
            var isOldIpad = isIpad && /OS [0-5]_/.test(ua);
            if (isOldIpad) {{
                var root = document.documentElement;
                if (root && root.className.indexOf("ipad1") === -1) {{
                    root.className = (root.className ? root.className + " " : "") + "ipad1";
                }}
            }}
        }})();
        </script>
        {style}
    </head>
    <body>
    """
    tail = "</body></html>"
    return head + body + tail


# ---------------------- HTTP HANDLER ----------------------

class FileBrowser(BaseHTTPRequestHandler):
    """
    HTTP File Browser/Player:
      - /            → Root Listing Of BASE_SERVER_DIR
      - /Path?View=1 → HTML Player
      - /Path        → Raw File With Range
      - /__thumbs__/Rel → Thumbnails From BASE_THUMB_DIR
    """

    def do_GET(self) -> None:
        global BASE_SERVER_DIR, BASE_THUMB_DIR, BASE_COVER_DIR

        if BASE_SERVER_DIR is None:
            self.send_error(500, "Server root is not configured")
            return

        parsed = urlparse(self.path)
        path_only = parsed.path

        # Folder Icon (static Asset Near Script)
        if path_only == "/folder.png":
            icon_path = os.path.join(SCRIPT_DIR, "folder.png")
            if not os.path.isfile(icon_path):
                self.send_error(404, "Icon not found")
                return
            size = os.path.getsize(icon_path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(icon_path, "rb") as f:
                self.wfile.write(f.read())
            return

        # Thumbnail PNG Assets
        if path_only.startswith("/thumbnail_") and path_only.endswith(".png"):
            thumb_asset = path_only.lstrip("/")
            asset_path = os.path.join(DATA_DIR, thumb_asset)
            if not os.path.isfile(asset_path):
                self.send_error(404, "Icon not found")
                return
            size = os.path.getsize(asset_path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(asset_path, "rb") as f:
                self.wfile.write(f.read())
            return

        # Favicon
        if path_only == "/icon.ico":
            ico_path = os.path.join(DATA_DIR, "icon.ico")
            if not os.path.isfile(ico_path):
                self.send_error(404, "Icon not found")
                return
            size = os.path.getsize(ico_path)
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(ico_path, "rb") as f:
                self.wfile.write(f.read())
            return
        if path_only == "/favicon.ico":
            ico_path = os.path.join(DATA_DIR, "favicon.ico")
            if not os.path.isfile(ico_path):
                self.send_error(404, "Icon not found")
                return
            size = os.path.getsize(ico_path)
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(ico_path, "rb") as f:
                self.wfile.write(f.read())
            return
        if path_only in ("/apple-touch-icon.png", "/favicon.png"):
            candidates = [
                os.path.join(DATA_DIR, "apple-touch-icon.png"),
                os.path.join(DATA_DIR, "favicon.png"),
                os.path.join(DATA_DIR, "favicon.ico"),
            ]
            ico_path = None
            for cand in candidates:
                if os.path.isfile(cand):
                    ico_path = cand
                    break
            if not ico_path:
                self.send_error(404, "Icon not found")
                return
            size = os.path.getsize(ico_path)
            self.send_response(200)
            if ico_path.lower().endswith(".png"):
                self.send_header("Content-Type", "image/png")
            else:
                self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(ico_path, "rb") as f:
                self.wfile.write(f.read())
            return

        # Thumbnails
        if path_only.startswith("/__thumbs__/"):
            if BASE_THUMB_DIR is None:
                self.send_error(404, "Thumbnails not configured")
                return

            rel_thumb = unquote(path_only[len("/__thumbs__/"):])
            thumb_full = os.path.normpath(os.path.join(BASE_THUMB_DIR, rel_thumb))
            thumb_root_norm = os.path.normpath(BASE_THUMB_DIR)

            if not thumb_full.startswith(thumb_root_norm):
                self.send_error(403, "Forbidden")
                return

            if not os.path.exists(thumb_full):
                self.send_error(404, "Thumbnail not found")
                return

            self.send_thumbnail(thumb_full)
            return

        # Audio Previews
        if path_only.startswith("/__preview__/"):
            if BASE_COVER_DIR is None:
                self.send_error(404, "Previews not configured")
                return

            rel_cover = unquote(path_only[len("/__preview__/"):])
            cover_full = os.path.normpath(os.path.join(BASE_COVER_DIR, rel_cover))
            cover_root_norm = os.path.normpath(BASE_COVER_DIR)

            if not cover_full.startswith(cover_root_norm):
                self.send_error(403, "Forbidden")
                return

            if not os.path.exists(cover_full):
                self.send_error(404, "Preview not found")
                return

            self.send_thumbnail(cover_full)
            return

        # Normal Files Under BASE_SERVER_DIR
        rel = unquote(path_only)
        if rel.startswith("/"):
            rel = rel[1:]

        full = os.path.normpath(os.path.join(BASE_SERVER_DIR, rel))
        base_norm = os.path.normpath(BASE_SERVER_DIR)

        if not full.startswith(base_norm):
            full = base_norm
            rel = ""

        if os.path.isdir(full):
            self.send_dir(full, rel)
        else:
            # ?view=1 - Use HTML Player
            if "view=1" in parsed.query:
                self.send_player(full, rel)
            else:
                self.send_file(full)

    # ---------- Thumbnails ----------

    def send_thumbnail(self, filepath: str) -> None:
        ext = os.path.splitext(filepath)[1].lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "image/jpeg")

        size = os.path.getsize(filepath)

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.end_headers()

        with open(filepath, "rb") as f:
            self.wfile.write(f.read())

    # ---------- Directory Listing ----------

    def send_dir(self, directory: str, rel: str) -> None:
        try:
            items = sorted(os.listdir(directory))
        except Exception:
            self.send_error(404, "Folder not accessible")
            return

        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)
        view_mode = query_params.get("mode", ["paged"])[0]
        page_param = query_params.get("page", ["1"])[0]
        try:
            page = max(1, int(page_param))
        except Exception:
            page = 1
        page_size = 10
        is_paged = (view_mode == "paged")
        toggle_target = "listmode" if is_paged else "paged"
        toggle_label = "List" if is_paged else "Paged"
        page_query = f"&page={page}" if is_paged else ""

        body_parts: List[str] = []
        title_text = "/" if rel == "" else rel
        body_parts.append(
            "<div class='header-row'>"
            f"<h2>{title_text}</h2>"
            f"<div class='controls header-controls'><a class='control-btn control-mode' href='/{quote(rel)}?mode={toggle_target}'>{toggle_label}</a></div>"
            "</div>"
        )

        if rel != "":
            parent = os.path.dirname(rel)
            parent_base = "/" + quote(parent) if parent != "" else "/"
            parent_query = f"?mode={'paged' if is_paged else 'listmode'}"
            parent_url = parent_base + parent_query
            body_parts.append(
                f"<div class='controls nav-links'><a class='control-btn control-back' href='{parent_url}'>Back To Parent Folder</a></div>"
            )

        total_items = len(items)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if is_paged:
            if page > total_pages:
                page = total_pages
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            items_to_show = items[start_idx:end_idx]
        else:
            items_to_show = items

        body_parts.append("<ul>")

        for name in items_to_show:
            full_path = os.path.join(directory, name)
            rel_path = os.path.join(rel, name).replace("\\", "/")
            rel_path_quoted = quote(rel_path)
            ext = os.path.splitext(name)[1].lower()

            if os.path.isdir(full_path):
                link = f"/{rel_path_quoted}?mode={view_mode}"
                body_parts.append(
                    f"<li class='item-row clickable-row' data-href='{link}'><span class='file-icon icon-folder'></span><a class='file-label' href='{link}'>{name}</a></li>"
                )
            else:
                if ext in IMAGE_EXT or ext in VIDEO_EXT or ext in AUDIO_EXT:
                    view_link = f"/{rel_path_quoted}?view=1&mode={view_mode}{page_query}"
                    if ext in IMAGE_EXT:
                        if view_mode != "listmode":
                            base_no_ext = os.path.splitext(rel_path)[0]
                            thumb_rel = base_no_ext + "_thumb.jpg"
                            thumb_src = f"/__thumbs__/{quote(thumb_rel)}"
                            body_parts.append(
                                f"<li class='thumb-row item-row clickable-row' data-href='{view_link}'>"
                                f"<a class='thumb-link' href='{view_link}'><img class='thumb' src='{thumb_src}' alt='thumb'></a>"
                                f"<a class='file-label' href='{view_link}'>{name}</a>"
                                "</li>"
                            )
                        else:
                            body_parts.append(
                                f"<li class='item-row clickable-row' data-href='{view_link}'><span class='file-icon icon-image'></span><a class='file-label' href='{view_link}'>{name}</a></li>"
                            )
                    elif ext in VIDEO_EXT:
                        body_parts.append(
                            f"<li class='item-row clickable-row' data-href='{view_link}'><span class='file-icon icon-video'></span><a class='file-label' href='{view_link}'>{name}</a></li>"
                        )
                    else:  # Audio
                        body_parts.append(
                            f"<li class='item-row clickable-row' data-href='{view_link}'><span class='file-icon icon-audio'></span><a class='file-label' href='{view_link}'>{name}</a></li>"
                        )
                else:
                    raw_link = f"/{rel_path_quoted}?mode={view_mode}{page_query}"
                    body_parts.append(
                        f"<li class='item-row clickable-row' data-href='{raw_link}'><span class='file-icon icon-file'></span><a class='file-label' href='{raw_link}'>{name}</a></li>"
                    )

        if is_paged:
            placeholders = page_size - len(items_to_show)
            for _ in range(placeholders):
                body_parts.append("<li class='placeholder-row item-row'>&nbsp;</li>")

        body_parts.append("</ul>")

        if is_paged and total_pages > 1:
            nav_parts: List[str] = []
            if page > 1:
                prev_page = page - 1
                nav_parts.append(f"<a class='control-btn control-prev' href='/{quote(rel)}?mode=paged&page={prev_page}'>Prev</a>")
            else:
                nav_parts.append("<span class='control-btn control-prev disabled'>Prev</span>")

            if page < total_pages:
                next_page = page + 1
                nav_parts.append(f"<a class='control-btn control-next' href='/{quote(rel)}?mode=paged&page={next_page}'>Next</a>")
            else:
                nav_parts.append("<span class='control-btn control-next disabled'>Next</span>")

            nav_parts.append(f"<span>Page {page}/{total_pages} | Files {len(items_to_show)}/{page_size}</span>")
            body_parts.append(f"<div class='controls'>{' '.join(nav_parts)}</div>")

        
        body_parts.append(
            """
<script>
(function() {
  var rows = document.querySelectorAll('.clickable-row');
  if (!rows || rows.length === 0) return;
  var go = function(href) {
    if (!href) return;
    window.location.href = href;
  };
  for (var i = 0; i < rows.length; i++) {
    (function(row) {
      var href = row.getAttribute('data-href');
      if (!href) return;
      var touchActive = false;
      var moved = false;
      var startX = 0, startY = 0;
      var maxMove = 8; // px tolerance to avoid triggering on scroll/pinch
      var handler = function(ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (touchActive && moved) {
          touchActive = false;
          moved = false;
          return;
        }
        go(href);
      };
      row.addEventListener('click', handler, false);
      row.addEventListener('touchstart', function(ev) {
        if (!ev.touches || ev.touches.length !== 1) {
          touchActive = false;
          moved = true;
          return;
        }
        touchActive = true;
        moved = false;
        startX = ev.touches[0].clientX;
        startY = ev.touches[0].clientY;
      }, false);
      row.addEventListener('touchmove', function(ev) {
        if (!touchActive || !ev.touches || ev.touches.length !== 1) {
          moved = true;
          return;
        }
        var dx = ev.touches[0].clientX - startX;
        var dy = ev.touches[0].clientY - startY;
        if (Math.sqrt(dx * dx + dy * dy) > maxMove) {
          moved = true;
        }
      }, false);
      row.addEventListener('touchend', function(ev) {
        if (!touchActive || moved) {
          touchActive = false;
          moved = false;
          return;
        }
        touchActive = false;
        if (ev && ev.preventDefault) ev.preventDefault();
        if (ev && ev.stopPropagation) ev.stopPropagation();
        handler(ev);
      }, false);
    })(rows[i]);
  }
})();
</script>
            """
        )
        html = wrap_html(f"/{rel}", "".join(body_parts))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    # ---------- Raw File With Range ----------

    def send_file(self, filepath: str) -> None:
        if not os.path.exists(filepath):
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(filepath)
        ext = os.path.splitext(filepath)[1].lower()

        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".m4v": "video/quicktime",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".aac": "audio/mp4",
            ".m4a": "audio/mp4",
        }.get(ext, "application/octet-stream")

        range_header = self.headers.get("Range")

        if range_header:
            try:
                bytes_range = range_header.replace("bytes=", "").split("-")
                start = int(bytes_range[0]) if bytes_range[0] else 0
                end = int(bytes_range[1]) if bytes_range[1] else file_size - 1
                end = min(end, file_size - 1)
            except Exception:
                start = 0
                end = file_size - 1

            chunk_size = (end - start) + 1

            self.send_response(206)
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(chunk_size))
            self.end_headers()

            with open(filepath, "rb") as f:
                f.seek(start)
                self.wfile.write(f.read(chunk_size))
        else:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

            with open(filepath, "rb") as f:
                self.wfile.write(f.read())

    # ---------- Player Via ?view=1 ----------

    def send_player(self, filepath: str, rel: str) -> None:
        if not os.path.exists(filepath):
            self.send_error(404, "File not found")
            return

        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)
        view_mode = query_params.get("mode", [None])[0]
        current_page = query_params.get("page", [None])[0]

        ext = os.path.splitext(filepath)[1].lower()
        basename = os.path.basename(filepath)
        display_name = os.path.splitext(basename)[0]
        file_url = "/" + quote(rel.replace("\\", "/"))
        if view_mode:
            file_url += f"?mode={view_mode}"
        if current_page:
            file_url += ("&" if "?" in file_url else "?") + f"page={current_page}"

        display_path = shorten_path_display(rel.replace("\\", "/"))

        directory = os.path.dirname(filepath)
        rel_dir = os.path.dirname(rel)

        cover_url = None
        tags: Dict[str, str] = {}
        if ext in AUDIO_EXT and SHOW_AUDIO_META and BASE_COVER_DIR:
            cover_rel = cover_rel_path(rel.replace("\\", "/"))
            cover_full = os.path.join(BASE_COVER_DIR, cover_rel)
            if os.path.exists(cover_full):
                cover_url = "/__preview__/" + quote(cover_rel.replace("\\", "/"))
                tags = ffprobe_audio_tags(filepath)

        try:
            items = sorted(os.listdir(directory))
        except Exception:
            items = []

        images = [
            f for f in items
            if os.path.splitext(f)[1].lower() in IMAGE_EXT
        ]

        prev_file = None
        next_file = None

        if basename in images:
            idx = images.index(basename)
            if idx > 0:
                prev_file = images[idx - 1]
            if idx < len(images) - 1:
                next_file = images[idx + 1]

        base_folder_url = "/" + quote(rel_dir) if rel_dir else "/"
        folder_url = base_folder_url + (f"?mode={view_mode}" if view_mode else "")
        if current_page:
            folder_url += ("&" if "?" in folder_url else "?") + f"page={current_page}"

        body_parts: List[str] = []
        body_parts.append(f"<h2>{display_name}</h2>")
        body_parts.append(f"<div class='path-display'>{display_path}</div>")
        body_parts.append("<div class='controls' id='image-controls'>")
        body_parts.append(f"<a class='control-btn control-back' href='{folder_url}'>Back To File List</a>")

        if ext in IMAGE_EXT:
            if prev_file:
                prev_rel = f"{rel_dir}/{prev_file}" if rel_dir else prev_file
                prev_url = f"/{quote(prev_rel)}?view=1"
                if view_mode:
                    prev_url += f"&mode={view_mode}"
                if current_page:
                    prev_url += f"&page={current_page}"
                prev_html = f"<a class='control-btn control-prev' href='{prev_url}'>Previous Image</a>"
            else:
                prev_html = "<span class='control-btn control-prev disabled'>Previous Image</span>"
            body_parts.append(prev_html)

            if next_file:
                next_rel = f"{rel_dir}/{next_file}" if rel_dir else next_file
                next_url = f"/{quote(next_rel)}?view=1"
                if view_mode:
                    next_url += f"&mode={view_mode}"
                if current_page:
                    next_url += f"&page={current_page}"
                next_html = f"<a class='control-btn control-next' href='{next_url}'>Next Image</a>"
            else:
                next_html = "<span class='control-btn control-next disabled'>Next Image</span>"
            body_parts.append(next_html)

        if ext in IMAGE_EXT:
            body_parts.append("<button id='rotate-btn' type='button' class='control-btn control-rotate'>Rotate Image</button>")

        body_parts.append("</div>")

        if ext in IMAGE_EXT:
            body_parts.append("<div class='image-viewer'>")
            body_parts.append(f"<img id='viewer-image' src='{file_url}' alt='{basename}'>")
            body_parts.append("</div>")
            body_parts.append(
                """
<script>
(function() {
  var img = document.getElementById('viewer-image');
  var btn = document.getElementById('rotate-btn');
  var controls = document.getElementById('image-controls');
  var container = document.querySelector('.image-viewer');
  if (!img || !btn) return;
  var rotation = 0;
  var busy = false;
  var naturalW = img.naturalWidth || img.clientWidth || 1;
  var naturalH = img.naturalHeight || img.clientHeight || 1;
  img.onload = function() {
    naturalW = img.naturalWidth || img.clientWidth || naturalW;
    naturalH = img.naturalHeight || img.clientHeight || naturalH;
    applyLayout();
  };
  var applyLayout = function() {
    var controlsRect = controls ? controls.getBoundingClientRect() : null;
    var viewerWidth = controlsRect ? controlsRect.width : (window.innerWidth - 32);
    if (viewerWidth < 120) viewerWidth = 120;
    var w = naturalW || img.clientWidth || 1;
    var h = naturalH || img.clientHeight || 1;
    var longestSide = Math.max(w, h);

    var boxW = viewerWidth;
    var boxH = Math.min(longestSide, viewerWidth);
    if (boxH < 1) boxH = 1;

    var scale = Math.min(1, viewerWidth / longestSide);
    if (scale <= 0 || !isFinite(scale)) scale = 1;

    var targetW = Math.round(w * scale);
    var targetH = Math.round(h * scale);

    if (container) {
      container.style.width = Math.round(boxW) + 'px';
      container.style.height = Math.round(boxH) + 'px';
      container.style.alignItems = 'center';
      container.style.justifyContent = 'center';
    }
    img.style.width = targetW + 'px';
    img.style.height = targetH + 'px';
    img.style.maxWidth = Math.round(boxW) + 'px';
    img.style.maxHeight = Math.round(boxH) + 'px';
    img.style.marginLeft = img.style.marginRight = "0";
    img.style.transformOrigin = 'center center';
    img.style.transformOrigin = 'center center';
  };
  applyLayout();
  window.addEventListener('resize', applyLayout);
  window.addEventListener('orientationchange', function() {
    setTimeout(applyLayout, 80);
  });
  var resetToZero = function() {
    img.style.transition = 'none';
    img.style.webkitTransition = 'none';
    img.style.transform = 'rotate(0deg)';
    img.style.webkitTransform = 'rotate(0deg)';
    rotation = 0;
    img.offsetHeight;
    img.style.transition = 'transform 0.2s ease';
    img.style.webkitTransition = '-webkit-transform 0.2s ease';
    busy = false;
    applyLayout();
  };
  btn.onclick = function(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    if (busy) return;
    rotation += 90;
    applyLayout();
    if (rotation === 360) {
      busy = true;
      img.style.transform = 'rotate(360deg)';
      img.style.webkitTransform = 'rotate(360deg)';
      setTimeout(resetToZero, 220);
      return;
    }
    if (rotation > 360) {
      rotation = 0;
    }
    img.style.transform = 'rotate(' + rotation + 'deg)';
    img.style.webkitTransform = 'rotate(' + rotation + 'deg)';
  };
})();
</script>
                """
            )
        elif ext in AUDIO_EXT:
            body_parts.append("<div class='media-player'>")
            body_parts.append("<audio controls autoplay>")
            body_parts.append(f"<source src='{file_url}'>")
            body_parts.append("Your browser does not support the audio element.")
            body_parts.append("</audio></div>")
            if SHOW_AUDIO_META and cover_url:
                artist = tags.get("artist") or tags.get("album_artist")
                title = tags.get("title")
                album = tags.get("album")
                genre = tags.get("genre")
                year = tags.get("date") or tags.get("year")
                track = tags.get("track") or tags.get("tracknumber")

                meta_blocks: List[str] = []
                if artist or title:
                    if artist and title:
                        meta_blocks.append(f"{html.escape(artist)} - {html.escape(title)}")
                    elif title:
                        meta_blocks.append(html.escape(title))
                    else:
                        meta_blocks.append(html.escape(artist))
                if album:
                    meta_blocks.append(f"Album: {html.escape(album)}")
                if year:
                    meta_blocks.append(f"Year: {html.escape(year)}")
                if track:
                    meta_blocks.append(f"Track: {html.escape(track)}")
                if genre:
                    meta_blocks.append(f"Genre: {html.escape(genre)}")

                body_parts.append(f"<div class='cover-art'><img src='{cover_url}' alt='Cover art'></div>")
                for block in meta_blocks:
                    body_parts.append(f"<div class='cover-meta'>{block}</div>")
        elif ext in VIDEO_EXT:
            body_parts.append("<div class='media-player'>")
            body_parts.append("<video controls autoplay>")
            body_parts.append(f"<source src='{file_url}'>")
            body_parts.append("Your browser does not support the video tag.")
            body_parts.append("</video></div>")

        page_html = wrap_html(basename, "".join(body_parts))

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page_html.encode("utf-8"))


# ---------------------- THREADED SERVER ----------------------

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """HTTPServer With Threads; Stops Cleanly On Ctrl+C."""
    daemon_threads = True
    allow_reuse_address = True


def run_server_forever() -> None:
    """Start HTTP Server In Main Thread (Ctrl+C Stops It)."""
    if BASE_SERVER_DIR is None:
        print("BASE_SERVER_DIR Is Not Configured. Exiting.")
        return

    server_address = ("0.0.0.0", PORT)
    httpd = ThreadedHTTPServer(server_address, FileBrowser)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Server...")
        httpd.shutdown()
        httpd.server_close()
        print("Server Stopped.")


# ---------------------- MAIN ----------------------

def main() -> None:
    global SOURCE_DIR, MODE, SHOW_FFMPEG_OUTPUT, BASE_SERVER_DIR, PORT

    # If Launched In A Dedicated Console With Preset Mode/port/folder, Skip GUI.
    env_mode = os.environ.get("IPAD_SERVER_MODE")
    env_port = os.environ.get("IPAD_SERVER_PORT")
    env_folder = os.environ.get("IPAD_SERVER_FOLDER")
    if env_mode and env_port and env_folder:
        if not apply_mode_from_key(env_mode):
            print(f'Unsupported Mode From Env: {env_mode}')
            return
        try:
            PORT = int(env_port)
        except Exception:
            print(f'Invalid Port From Env: {env_port}')
            return
        SOURCE_DIR = env_folder
        ensure_console_allocated()
        ensure_children_killed_on_close()
    else:
        default_mode_key = "nocopytotemp"
        if len(sys.argv) > 1:
            arg_key = sys.argv[1].lower()
            if arg_key in MODE_CHOICES:
                default_mode_key = arg_key
            else:
                print(f'Unknown Mode In Args: {arg_key}')
                print('Opening Mode Selector Window; Pick One From The List.')

        selection = prompt_mode_and_port(default_mode_key, PORT)
        if selection is None:
            print('Launch Cancelled. Exit.')
            return

        selected_mode_key, selected_port = selection
        if not apply_mode_from_key(selected_mode_key):
            print(f'Unsupported Mode: {selected_mode_key}')
            return

        folder = choose_source_folder()
        if not folder:
            print('No Folder Selected. Exit.')
            return

        # Spawn A New Console And Exit This GUI Instance.
        if launch_in_new_console(selected_mode_key, selected_port, folder):
            return

        PORT = selected_port
        SOURCE_DIR = folder
        ensure_console_allocated()
        ensure_children_killed_on_close()
        ensure_dirs()
        if MODE == 'nocopytotemp':
            BASE_SERVER_DIR = SOURCE_DIR

    print('\n=== Starting Media Server ===')
    print(f"\nMode: {MODE} ({serving_mode_message()})")
    if SHOW_FFMPEG_OUTPUT:
        print('ffmpeg Output: Enabled')
    else:
        print('ffmpeg Output: Disabled')
    if SHOW_AUDIO_META:
        print('Audiofile Meta: Enabled (Cover Art + Audio Tags)')
    else:
        print('Audiofile Meta: Disabled')

    # In Env Branch Ensure SOURCE_DIR Is Set; If Not, Ask Now (Fallback).
    if not SOURCE_DIR:
        folder = choose_source_folder()
        if not folder:
            print('No Folder Selected. Exit.')
            return
        SOURCE_DIR = folder

    ensure_dirs()

    if MODE == 'nocopytotemp':
        BASE_SERVER_DIR = SOURCE_DIR

    print(f'Serving At: http://0.0.0.0:{PORT}')
    print('Selected Folder: ' + SOURCE_DIR)
    print('Server Root: ' + str(BASE_SERVER_DIR))

    # Start Sync In Background Thread (daemon)
    sync_thread = threading.Thread(target=sync_all, args=(SOURCE_DIR,), daemon=True)
    sync_thread.start()

    # Run HTTP Server In Main Thread (so Ctrl+C Works)
    run_server_forever()

if __name__ == "__main__":
    main()


