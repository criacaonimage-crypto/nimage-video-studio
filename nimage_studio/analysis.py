"""ETAPA 3 — Análise: extrai áudio, transcreve falas (faster-whisper) e detecta cenas (PySceneDetect).

Os resultados ficam em cache por arquivo, então analisar de novo o mesmo vídeo é instantâneo.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

from . import config

# O acelerador de download "hf_xet" (sem assinatura digital) é barrado pelo Controle de Aplicativo
# Inteligente do Windows. Desligado, o modelo de transcrição é baixado pelo método HTTP comum.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

ANALISE_VERSAO = 1
Progresso = Callable[[str, float], None]   # (mensagem, fração 0..1 da etapa)


class Cancelado(Exception):
    pass


class AnaliseErro(Exception):
    pass


# ------------------------------------------------------------------ GPU (Windows)
def preparar_dlls_cuda() -> None:
    """No Windows, os DLLs do CUDA vêm dos pacotes pip nvidia-*; adiciona-os à busca de DLLs."""
    if os.name != "nt":
        return
    bases = [Path(p) for p in sys.path if p and Path(p).is_dir()]
    if getattr(sys, "frozen", False):
        bases.append(Path(getattr(sys, "_MEIPASS", "")))
    vistos = set()
    extra = config.data_dir() / "cuda"   # baixado em Configurações (cuda_libs.py)
    if extra.is_dir():
        vistos.add(extra)
        try:
            os.add_dll_directory(str(extra))
            os.environ["PATH"] = str(extra) + os.pathsep + os.environ.get("PATH", "")
        except OSError:
            pass
    for base in bases:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin"):
            d = base / sub
            if d.is_dir() and d not in vistos:
                vistos.add(d)
                try:
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass


# ------------------------------------------------------------------ cache
def _chave(caminho: str, extra: str) -> str:
    p = Path(caminho)
    st = p.stat()
    return hashlib.sha1(f"{p.resolve()}|{st.st_size}|{st.st_mtime}|{extra}|{ANALISE_VERSAO}".encode()).hexdigest()[:24]


def _cache_path(chave: str) -> Path:
    d = config.cache_dir() / "analises"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{chave}.json"


# ------------------------------------------------------------------ áudio
def extrair_audio(caminho: str, destino: Path) -> Path:
    ff = config.ffmpeg_path()
    if not ff:
        raise AnaliseErro("FFmpeg não encontrado.")
    r = config.run([ff, "-y", "-v", "error", "-i", caminho, "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(destino)], timeout=3600)
    if r.returncode != 0 or not destino.exists():
        raise AnaliseErro(f"Falha ao extrair o áudio: {r.stderr.strip()[:300]}")
    return destino


# ------------------------------------------------------------------ transcrição
def escolher_whisper(preferencia: str, vram_mb: int) -> tuple[str, str, str]:
    """Retorna (modelo, device, compute_type) conforme o computador."""
    if vram_mb >= 7000:
        modelo = "large-v3" if preferencia in ("", "Automático") else preferencia
        return modelo, "cuda", "float16" if vram_mb >= 11000 else "int8_float16"
    modelo = "small" if preferencia in ("", "Automático") else preferencia
    return modelo, "cpu", "int8"


def cuda_disponivel_para_whisper() -> bool:
    """Confere se os DLLs do CUDA (cuBLAS/cuDNN) estão acessíveis antes de tentar a GPU.

    Sem essa checagem, a falta de um DLL pode derrubar o programa no meio da transcrição.
    """
    preparar_dlls_cuda()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:  # noqa: BLE001
        return False
    if os.name != "nt":
        return True
    import ctypes
    for dll in ("cublas64_12.dll", "cudnn_ops64_9.dll"):
        try:
            ctypes.WinDLL(dll)
        except OSError:
            return False
    return True


def _carregar_whisper(modelo: str, device: str, compute: str):
    from faster_whisper import WhisperModel
    return WhisperModel(modelo, device=device, compute_type=compute,
                        download_root=str(config.data_dir() / "modelos_whisper"))


def ler_wav(audio: Path):
    """Lê o WAV 16 kHz mono (já extraído pelo FFmpeg) como array float32.

    Assim o faster-whisper não precisa decodificar o arquivo com a biblioteca PyAV, cujas versões
    novas mudaram a API (erro "unexpected keyword argument 'metadata_errors'").
    """
    import wave
    import numpy as np
    with wave.open(str(audio), "rb") as w:
        if w.getsampwidth() != 2:
            raise AnaliseErro("Formato de áudio inesperado.")
        dados = w.readframes(w.getnframes())
        canais = w.getnchannels()
    arr = np.frombuffer(dados, dtype=np.int16).astype(np.float32) / 32768.0
    if canais > 1:
        arr = arr.reshape(-1, canais).mean(axis=1)
    return arr


def _rodar_whisper(wm, audio: Path, duracao: float, progresso: Progresso, cancelado) -> list[dict]:
    segs_out: list[dict] = []
    segmentos, _info = wm.transcribe(ler_wav(audio), language="pt", word_timestamps=True, vad_filter=True,
                                     beam_size=5, condition_on_previous_text=False)
    for s in segmentos:
        if cancelado():
            raise Cancelado()
        palavras = [{"p": w.word.strip(), "i": round(w.start, 2), "f": round(w.end, 2)}
                    for w in (s.words or []) if w.word.strip()]
        texto = s.text.strip()
        if texto:
            segs_out.append({"inicio": round(s.start, 2), "fim": round(s.end, 2), "texto": texto,
                             "palavras": palavras})
        if duracao:
            progresso(f"Transcrevendo… {int(s.end // 60)}:{int(s.end % 60):02d} de "
                      f"{int(duracao // 60)}:{int(duracao % 60):02d}", min(s.end / duracao, 1.0))
    return segs_out


def transcrever(audio: Path, modelo: str, device: str, compute: str, duracao: float,
                progresso: Progresso, cancelado: Callable[[], bool]) -> list[dict]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError as e:
        raise AnaliseErro("faster-whisper não está instalado.") from e
    if device == "cuda" and not cuda_disponivel_para_whisper():
        progresso("Transcrevendo pelo processador (mais lento). Para usar a placa de vídeo, ative-a em "
                  "Configurações → Transcrição pela placa de vídeo.", 0.0)
        device, compute = "cpu", "int8"
        modelo = "medium" if modelo.startswith("large") else modelo
    tentativas = [(modelo, device, compute)]
    if device == "cuda":
        tentativas.append(("medium" if modelo.startswith("large") else modelo, "cpu", "int8"))
    ultimo_erro = None
    for mod, dev, comp in tentativas:
        progresso(f"Carregando modelo de transcrição ({mod}, {dev.upper()})… "
                  "Na primeira vez ele é baixado da internet (até ~3 GB).", 0.0)
        wm = None
        try:
            wm = _carregar_whisper(mod, dev, comp)
            return _rodar_whisper(wm, audio, duracao, progresso, cancelado)
        except Cancelado:
            raise
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            if dev == "cuda":
                progresso("A GPU falhou na transcrição; tentando pelo processador…", 0.0)
        finally:
            del wm
            gc.collect()  # libera a memória da GPU antes da IA de planejamento
    raise AnaliseErro(f"Não foi possível transcrever: {ultimo_erro}. Na primeira vez é preciso internet "
                      "para baixar o modelo de transcrição.")


# ------------------------------------------------------------------ cenas
def _seg(tc) -> float:
    return float(tc.seconds) if hasattr(tc, "seconds") else float(tc.get_seconds())


def detectar_cenas(caminho: str, duracao: float, progresso: Progresso) -> list[dict]:
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError as e:
        raise AnaliseErro("PySceneDetect não está instalado.") from e
    progresso("Identificando cenas…", 0.0)
    video = open_video(caminho)
    sm = SceneManager()
    sm.auto_downscale = True
    sm.add_detector(ContentDetector(threshold=27.0, min_scene_len=15))

    def cb(_img, posicao):  # chamado a cada corte encontrado
        if duracao:
            seg = _seg(posicao) if hasattr(posicao, "get_seconds") or hasattr(posicao, "seconds") else float(posicao) / video.frame_rate
            progresso("Identificando cenas…", min(seg / duracao, 1.0))

    sm.detect_scenes(video, callback=cb, show_progress=False)
    cenas = [{"inicio": round(_seg(a), 2), "fim": round(_seg(b), 2)} for a, b in sm.get_scene_list()]
    if not cenas and duracao:
        cenas = [{"inicio": 0.0, "fim": round(duracao, 2)}]
    return cenas


# ------------------------------------------------------------------ orquestração
def analisar_video(info: dict, whisper_pref: str, vram_mb: int, progresso: Progresso,
                   cancelado: Callable[[], bool] = lambda: False) -> dict:
    """Analisa um vídeo e devolve {info, transcricao, cenas}. Usa cache quando possível."""
    caminho = info["caminho"]
    modelo, device, compute = escolher_whisper(whisper_pref, vram_mb)
    chave = _chave(caminho, modelo)
    cp = _cache_path(chave)
    if cp.exists():
        progresso("Análise já feita anteriormente (reaproveitada).", 1.0)
        return json.loads(cp.read_text(encoding="utf-8"))

    transcricao: list[dict] = []
    if info.get("tem_audio"):
        audio = config.cache_dir() / f"audio_{chave}.wav"
        try:
            progresso("Extraindo áudio…", 0.0)
            extrair_audio(caminho, audio)
            if cancelado():
                raise Cancelado()
            transcricao = transcrever(audio, modelo, device, compute, info.get("duracao", 0), progresso, cancelado)
        finally:
            audio.unlink(missing_ok=True)
    if cancelado():
        raise Cancelado()
    cenas = detectar_cenas(caminho, info.get("duracao", 0), progresso)
    resultado = {"info": info, "transcricao": transcricao, "cenas": cenas,
                 "modelo_transcricao": f"{modelo} ({device})"}
    cp.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    return resultado
