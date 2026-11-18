"""Informações de vídeo (PyAV) e miniaturas (ffmpeg). Nunca altera o arquivo original."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import config

EXTENSOES_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".webm", ".wmv", ".mxf"}


class MediaError(Exception):
    pass


def eh_video(caminho: str | Path) -> bool:
    return Path(caminho).suffix.lower() in EXTENSOES_VIDEO


def probe(caminho: str | Path) -> dict:
    """Lê duração, resolução, fps, codecs e áudio. Usa a biblioteca PyAV (sem ffprobe.exe)."""
    caminho = str(caminho)
    try:
        return _probe_av(caminho)
    except MediaError:
        raise
    except Exception as e:  # noqa: BLE001
        if config.ffprobe_path():
            return _probe_ffprobe(caminho)
        raise MediaError(f"Não foi possível ler o arquivo: {e}") from e


def _probe_av(caminho: str) -> dict:
    import av
    try:
        c = av.open(caminho)
    except av.error.FFmpegError as e:
        raise MediaError(f"Não foi possível ler o arquivo: {e}") from e
    try:
        videos = [s for s in c.streams.video if not (int(s.disposition or 0) & 0x400)]  # ignora capa/thumbnail
        if not videos:
            raise MediaError("O arquivo não contém trilha de vídeo.")
        vs = videos[0]
        audio = c.streams.audio[0] if c.streams.audio else None
        largura, altura = int(vs.width or 0), int(vs.height or 0)
        rot = 0
        try:
            quadro = next(c.decode(vs))
            rot = int(getattr(quadro, "rotation", 0) or 0)
        except (StopIteration, av.error.FFmpegError):
            pass
        if abs(rot) % 180 == 90:  # vídeo de celular gravado em pé
            largura, altura = altura, largura
        fps = float(vs.average_rate) if vs.average_rate else 0.0
        duracao = c.duration / 1_000_000 if c.duration else (
            float(vs.duration * vs.time_base) if vs.duration and vs.time_base else 0.0)
        return {
            "caminho": caminho,
            "nome": os.path.basename(caminho),
            "duracao": float(duracao),
            "largura": largura,
            "altura": altura,
            "fps": round(fps, 3),
            "codec_video": vs.codec_context.name if vs.codec_context else "",
            "codec_audio": audio.codec_context.name if audio and audio.codec_context else "",
            "tem_audio": audio is not None,
            "formato": Path(caminho).suffix.lstrip(".").upper(),
            "tamanho_bytes": os.path.getsize(caminho),
        }
    finally:
        c.close()


def _probe_ffprobe(caminho: str) -> dict:
    ffprobe = config.ffprobe_path()
    r = config.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", caminho], timeout=60)
    if r.returncode != 0:
        raise MediaError(f"Não foi possível ler o arquivo: {r.stderr.strip()[:300]}")
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise MediaError("O arquivo não contém trilha de vídeo.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {})

    largura, altura = int(video.get("width", 0)), int(video.get("height", 0))
    rot = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int(video.get("tags", {}).get("rotate", rot) or 0)
    if abs(rot) % 180 == 90:  # vídeo de celular gravado em pé
        largura, altura = altura, largura

    fps = 0.0
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        pass

    duracao = float(fmt.get("duration") or video.get("duration") or 0)
    return {
        "caminho": caminho,
        "nome": os.path.basename(caminho),
        "duracao": duracao,
        "largura": largura,
        "altura": altura,
        "fps": round(fps, 3),
        "codec_video": video.get("codec_name", ""),
        "codec_audio": audio.get("codec_name", "") if audio else "",
        "tem_audio": audio is not None,
        "formato": Path(caminho).suffix.lstrip(".").upper(),
        "tamanho_bytes": int(fmt.get("size") or 0),
    }


def miniatura(caminho: str | Path, duracao: float | None = None, largura: int = 480) -> str:
    """Gera (ou reaproveita do cache) uma miniatura JPG e retorna o caminho."""
    from . import avtools
    p = Path(caminho)
    chave = hashlib.sha1(f"{p.resolve()}|{p.stat().st_mtime}|{largura}".encode()).hexdigest()[:20]
    destino = config.cache_dir() / "miniaturas" / f"{chave}.jpg"
    if destino.exists():
        return str(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, min((duracao or 0) * 0.1, 5.0))
    try:
        avtools.miniatura(str(p), t, destino, largura)
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"Não foi possível gerar miniatura: {e}") from e
    return str(destino)


def formatar_duracao(seg: float) -> str:
    seg = int(round(seg))
    h, m, s = seg // 3600, (seg % 3600) // 60, seg % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def formatar_tamanho(b: int) -> str:
    for unidade in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unidade}"
        b /= 1024
    return f"{b:.1f} TB"
