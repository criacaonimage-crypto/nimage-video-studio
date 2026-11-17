"""Caminhos, versão e detecção de ferramentas/hardware."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "NIMAGE VIDEO STUDIO"
APP_VERSION = "1.0.0"
APP_SLUG = "NimageVideoStudio"

# No Windows, evita abrir janela de console ao chamar ffmpeg/ffprobe/nvidia-smi.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def app_dir() -> Path:
    """Pasta do programa (funciona tanto no código-fonte quanto no .exe do PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resources_dir() -> Path:
    if getattr(sys, "frozen", False):
        return app_dir() / "nimage_studio" / "resources"
    return Path(__file__).resolve().parent / "resources"


def data_dir() -> Path:
    """Pasta de dados do usuário (banco, cache de miniaturas)."""
    override = os.environ.get("NIMAGE_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_SLUG
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_SLUG
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "nimage_studio.db"


def _find_tool(name: str) -> str | None:
    exe = name + (".exe" if os.name == "nt" else "")
    bases = [app_dir()]
    if getattr(sys, "frozen", False):
        bases.insert(0, Path(sys.executable).parent)  # pasta do .exe (FFmpeg vai ao lado dele)
    for base in bases:
        for candidate in (base / "ffmpeg" / "bin" / exe, base / "ffmpeg" / exe, base / exe):
            if candidate.exists():
                return str(candidate)
    return shutil.which(name)


def ffmpeg_path() -> str | None:
    return _find_tool("ffmpeg")


def ffprobe_path() -> str | None:
    return _find_tool("ffprobe")


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )


def detect_gpu() -> dict:
    """Detecta GPU NVIDIA via nvidia-smi. Retorna {} se não houver."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return {}
    try:
        r = run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"], timeout=10)
        if r.returncode != 0 or not r.stdout.strip():
            return {}
        name, mem, driver = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return {"nome": name, "vram_mb": int(float(mem)), "driver": driver}
    except Exception:
        return {}


def ffmpeg_has_nvenc() -> bool:
    ff = ffmpeg_path()
    if not ff:
        return False
    try:
        return "h264_nvenc" in run([ff, "-hide_banner", "-encoders"], timeout=15).stdout
    except Exception:
        return False


def hardware_profile() -> dict:
    """Perfil usado pelas próximas entregas para escolher modelo de IA e codificador."""
    gpu = detect_gpu()
    vram = gpu.get("vram_mb", 0)
    if vram >= 11000:
        perfil = "GPU 12 GB"
    elif vram >= 7000:
        perfil = "GPU 8 GB"
    elif vram:
        perfil = "GPU com pouca memória"
    else:
        perfil = "Somente CPU"
    return {"gpu": gpu, "perfil": perfil, "nvenc": ffmpeg_has_nvenc()}
