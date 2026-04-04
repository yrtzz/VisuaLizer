# -*- mode: python ; coding: utf-8 -*-
# Запуск: pyinstaller mp3line.spec

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Собираем все нужные данные spotipy (шаблоны, сертификаты)
spotipy_datas = collect_data_files("spotipy")

a = Analysis(
    ["mp3line.py"],
    pathex=["."],
    binaries=collect_dynamic_libs("cv2"),   # OpenCV DLL
    datas=[
        ("spotify_client.py", "."),          # рядом с exe
        *spotipy_datas,
    ],
    hiddenimports=[
        "spotipy",
        "spotipy.oauth2",
        "spotipy.cache_handler",
        "sounddevice",
        "cv2",
        "numpy",
        "pygame",
        "requests",
        "urllib3",
        "certifi",
        "charset_normalizer",
        "pycaw",
        "comtypes",
        "comtypes.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "PIL"],
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
    name="MusicVisualizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="icon.ico",      # раскомментируй если есть иконка .ico
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MusicVisualizer",
)
