# PyInstaller spec — Echo Flow desktop shell
#
# Build:
#   .venv\Scripts\python.exe -m PyInstaller EchoFlow.spec --noconfirm
#
# Output:
#   dist/EchoFlow/EchoFlow.exe   (one-folder build; ~80–150MB)
#
# Notes
# -----
# * One-folder (not one-file) for fast startup; the .exe + DLLs ship together.
# * Dashboard templates/static and config.yaml are bundled as data files.
# * pywebview, pystray, pynput, sqlite are picked up automatically; we add
#   explicit hidden imports for Flask jinja loaders just in case.
from __future__ import annotations
from pathlib import Path

REPO = Path('.').resolve()

datas = [
    (str(REPO / 'src' / 'dashboard' / 'templates'), 'src/dashboard/templates'),
    (str(REPO / 'src' / 'dashboard' / 'static'),    'src/dashboard/static'),
    (str(REPO / 'config.yaml'),                     '.'),
    (str(REPO / 'assets' / 'icon.png'),             'assets'),
    (str(REPO / 'assets' / 'icon.ico'),             'assets'),
]

hiddenimports = [
    'jinja2.ext',
    'pkg_resources.py2_warn',
    'webview.platforms.winforms',
    'pystray._win32',
    'pynput.keyboard._win32',
    'pynput.mouse._win32',
]

a = Analysis(
    ['app.py'],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Heavy ML deps not needed for the dashboard shell.
        'torch', 'transformers', 'sentence_transformers',
        'faster_whisper', 'silero_vad', 'sounddevice',
        'numpy.tests', 'matplotlib', 'pandas',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='EchoFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                   # windowed app — no console
    icon=str(REPO / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='EchoFlow',
)
