# -*- mode: python ; coding: utf-8 -*-
# Gera a versão Windows:  pyinstaller NimageVideoStudio.spec --noconfirm
# O FFmpeg é copiado depois para dist/NimageVideoStudio/ffmpeg/bin (ver .github/workflows/build-windows.yml).

from PyInstaller.utils.hooks import collect_all

datas = [("nimage_studio/resources", "nimage_studio/resources")]
binaries, hiddenimports = [], []
for pacote in ("faster_whisper", "ctranslate2", "onnxruntime", "scenedetect", "tokenizers"):
    d, b, h = collect_all(pacote)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(
    ["run.py"],
    pathex=[],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization", "torch", "matplotlib", "scipy", "imageio_ffmpeg", "moviepy", "pandas", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="NimageVideoStudio",
    console=False,
    icon="nimage_studio/resources/icone.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="NimageVideoStudio")
