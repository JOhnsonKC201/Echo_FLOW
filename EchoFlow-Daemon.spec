# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Echo Flow DAEMON (src/main.py).

This bundles the full voice pipeline (faster-whisper, sounddevice, silero-vad,
sentence-transformers, etc.) plus the embedded Flask dashboard so the whole
product ships as a single installable artifact.

Build:
    pyinstaller --noconfirm --clean EchoFlow-Daemon.spec

Output:
    dist/EchoFlow-Daemon/EchoFlow-Daemon.exe   (+ supporting folder)

NOTE: This is a one-FOLDER build (not one-file) so that boot-time startup
stays snappy — the user launches this at login via the installer's Run key.

IMPORTANT (manual step before this spec is useful):
    src/main.py must be patched to resolve its user-data dir to
    %LOCALAPPDATA%\\EchoFlow\\ when sys.frozen is True (the same pattern
    that app.py already uses for the dashboard shell). Without that patch
    the frozen daemon will try to write config.yaml / history.db next to
    the exe inside Program Files-style locations and fail.
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)
import os

block_cipher = None

# ---------------------------------------------------------------------------
# Hidden imports — the heavy ML / audio / system stack the daemon pulls in
# dynamically at runtime.
# ---------------------------------------------------------------------------
hiddenimports = [
    # Speech-to-text + VAD
    "faster_whisper",
    "faster_whisper.transcribe",
    "faster_whisper.tokenizer",
    "silero_vad",
    "silero_vad.utils_vad",
    # Audio I/O
    "sounddevice",
    "soundfile",
    # Embeddings / clustering
    "sentence_transformers",
    "sentence_transformers.models",
    "sklearn.cluster._kmeans",
    "sklearn.utils._typedefs",
    "sklearn.neighbors._partition_nodes",
    "numpy",
    "numpy.core._methods",
    "numpy.lib.format",
    # Input + tray
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._win32",
    "pynput.mouse",
    "pynput.mouse._win32",
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    # Embedded dashboard + WebView shell
    "flask",
    "flask.json",
    "jinja2",
    "jinja2.ext",
    "webview",
    "webview.platforms.winforms",
    # Config / IO
    "yaml",
    "requests",
    "rich",
    # Windows integration
    "pythonnet",
    "clr",
    "win32api",
    "win32con",
    "win32gui",
    "win32com",
    "win32com.client",
    "winsdk",
    "winsdk._winrt_async",
    "winsdk._winrt_inspectable",
    "winsdk.windows.foundation",
    "winsdk.windows.ui.notifications",
    # Mobile bridge
    "zeroconf",
    "zeroconf._utils.ipaddress",
]

# Collect every submodule from the big packages so dynamic imports survive.
for pkg in (
    "faster_whisper",
    "silero_vad",
    "sentence_transformers",
    "pystray",
    "pynput",
    "webview",
    "winsdk",
):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Data files — model assets, package metadata, dashboard templates/static,
# config.yaml seed, app icons, and an empty data/ dir for first-run writes.
# ---------------------------------------------------------------------------
datas = []
for pkg in (
    "faster_whisper",
    "silero_vad",
    "sentence_transformers",
    "sounddevice",
    "soundfile",
):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# Metadata is required by sentence-transformers / huggingface-hub at runtime.
for pkg in ("sentence_transformers", "huggingface_hub", "tqdm", "filelock"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# Embedded dashboard assets and seed config.
datas += [
    ("src/dashboard/templates", "src/dashboard/templates"),
    ("src/dashboard/static", "src/dashboard/static"),
    ("config.yaml", "."),
    ("assets", "assets"),
]

# Ship an empty data/ tree so the daemon has somewhere to land its first
# write on a clean machine before the LOCALAPPDATA path is hydrated.
if not os.path.isdir("data"):
    os.makedirs("data", exist_ok=True)
datas += [("data", "data")]


a = Analysis(
    ["src/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EchoFlow-Daemon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,        # background daemon — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EchoFlow-Daemon",
)
