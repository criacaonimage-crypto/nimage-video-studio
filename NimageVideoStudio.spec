# -*- mode: python ; coding: utf-8 -*-
# Gera a versão Windows:  pyinstaller NimageVideoStudio.spec --noconfirm
# Sem executáveis externos: vídeo pela biblioteca PyAV (incluída via collect_all).

from PyInstaller.utils.hooks import collect_all

datas = [("nimage_studio/resources", "nimage_studio/resources")]
binaries, hiddenimports = [], []
for pacote in ("faster_whisper", "ctranslate2", "onnxruntime", "scenedetect", "tokenizers", "av"):
    d, b, h = collect_all(pacote)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(
    ["run.py"],
    pathex=[],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization", "torch", "matplotlib", "scipy", "moviepy", "pandas", "IPython"],
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
