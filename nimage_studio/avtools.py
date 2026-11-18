"""Leitura e gravação de vídeo/áudio sem executáveis externos.

Tudo passa pela biblioteca PyAV (FFmpeg embutido em forma de biblioteca do Python), que o Controle de
Aplicativo Inteligente do Windows aceita. Imagens são tratadas com OpenCV/numpy.
"""
from __future__ import annotations

import wave
from fractions import Fraction
from pathlib import Path
from typing import Iterator

import numpy as np

SR = 48000          # taxa de amostragem do áudio de saída

try:  # silencia as mensagens internas do FFmpeg/x264 no console
    import av.logging as _avlog
    _avlog.set_level(_avlog.ERROR)
except Exception:  # noqa: BLE001
    pass
_encoder_cache: dict[str, bool] = {}


class AVErro(Exception):
    pass


def _abrir(caminho: str):
    import av
    try:
        return av.open(str(caminho))
    except av.error.FFmpegError as e:
        raise AVErro(f"Não foi possível abrir {Path(caminho).name}: {e}") from e


def _rot(img: np.ndarray, rotacao: int) -> np.ndarray:
    k = (int(rotacao or 0) // 90) % 4
    return np.ascontiguousarray(np.rot90(img, k)) if k else img


# ------------------------------------------------------------------ áudio
def ler_audio(caminho: str, inicio: float = 0.0, duracao: float | None = None, sr: int = SR,
              canais: int = 2) -> np.ndarray:
    """Áudio como float32 (amostras, canais). Sem trilha de áudio → silêncio (se duração informada)."""
    import av
    c = _abrir(caminho)
    try:
        if not c.streams.audio:
            n = int(round((duracao or 0) * sr))
            return np.zeros((n, canais), dtype=np.float32)
        st = c.streams.audio[0]
        st.thread_type = "AUTO"
        layout = "stereo" if canais == 2 else "mono"
        res = av.AudioResampler(format="flt", layout=layout, rate=sr)
        if inicio > 0.5:
            c.seek(int(max(inicio - 0.5, 0) / st.time_base), stream=st, backward=True)
        partes, t0 = [], None
        fim = None if duracao is None else inicio + duracao
        for frame in c.decode(st):
            if frame.time is not None and fim is not None and frame.time > fim + 0.2:
                break
            for rf in res.resample(frame):
                arr = rf.to_ndarray().reshape(-1, canais)
                if t0 is None:
                    t0 = rf.time if rf.time is not None else (frame.time or 0.0)
                partes.append(arr)
        for rf in res.resample(None):
            partes.append(rf.to_ndarray().reshape(-1, canais))
    finally:
        c.close()
    tudo = np.concatenate(partes) if partes else np.zeros((0, canais), dtype=np.float32)
    t0 = t0 or 0.0
    ini_idx = int(round((inicio - t0) * sr))
    if ini_idx < 0:
        tudo = np.concatenate([np.zeros((-ini_idx, canais), dtype=np.float32), tudo])
        ini_idx = 0
    tudo = tudo[ini_idx:]
    if duracao is not None:
        n = int(round(duracao * sr))
        tudo = tudo[:n] if len(tudo) >= n else np.concatenate([tudo, np.zeros((n - len(tudo), canais), np.float32)])
    return np.ascontiguousarray(tudo, dtype=np.float32)


def extrair_wav(caminho: str, destino: Path, sr: int = 16000) -> Path:
    """WAV mono 16 kHz para a transcrição."""
    audio = ler_audio(caminho, 0.0, None, sr=sr, canais=1)
    pcm = (np.clip(audio[:, 0], -1, 1) * 32767).astype(np.int16)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return destino


# ------------------------------------------------------------------ vídeo
def quadro_em(caminho: str, t: float) -> np.ndarray | None:
    """Um quadro (BGR) próximo do tempo t, já na orientação correta."""
    c = _abrir(caminho)
    try:
        if not c.streams.video:
            return None
        vs = c.streams.video[0]
        if t > 0.2:
            c.seek(int(t / vs.time_base), stream=vs, backward=True)
        ultimo = None
        for f in c.decode(vs):
            ultimo = f
            if f.time is None or f.time >= t - 1e-3:
                break
        if ultimo is None:
            return None
        return _rot(ultimo.to_ndarray(format="bgr24"), getattr(ultimo, "rotation", 0))
    finally:
        c.close()


def quadros(caminho: str, inicio: float, duracao: float, fps: int = 30) -> Iterator[np.ndarray]:
    """Gera exatamente round(duracao*fps) quadros BGR (orientação corrigida), amostrados a `fps`."""
    n = max(1, int(round(duracao * fps)))
    c = _abrir(caminho)
    try:
        if not c.streams.video:
            raise AVErro(f"Não há vídeo em {Path(caminho).name}.")
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        if inicio > 0.2:
            c.seek(int(max(inicio - 0.1, 0) / vs.time_base), stream=vs, backward=True)
        k = 0
        atual, atual_img, ultimo_t = None, None, None

        def img_atual():
            nonlocal atual_img
            if atual_img is None:
                atual_img = _rot(atual.to_ndarray(format="bgr24"), getattr(atual, "rotation", 0))
            return atual_img

        for f in c.decode(vs):
            ft = f.time if f.time is not None else ((ultimo_t or 0.0) + 1 / fps)
            while atual is not None and k < n and inicio + k / fps < ft - 1e-6:
                yield img_atual()
                k += 1
            if k >= n:
                return
            atual, atual_img, ultimo_t = f, None, ft
        if atual is None:
            raise AVErro(f"Não há quadros de vídeo em {Path(caminho).name}.")
        while k < n:
            yield img_atual()
            k += 1
    finally:
        c.close()


def miniatura(caminho: str, t: float, destino: Path, largura: int = 480) -> Path:
    import cv2
    img = quadro_em(caminho, t)
    if img is None:
        img = quadro_em(caminho, 0.0)
    if img is None:
        raise AVErro("Sem quadros de vídeo.")
    h, w = img.shape[:2]
    alt = max(2, int(round(h * largura / w)))
    cv2.imwrite(str(destino), cv2.resize(img, (largura, alt), interpolation=cv2.INTER_AREA))
    return destino


# ------------------------------------------------------------------ gravação
def encoder_funciona(nome: str) -> bool:
    """Testa de verdade (o NVENC só funciona com placa NVIDIA e driver)."""
    if nome not in _encoder_cache:
        import av
        ok = False
        try:
            out = av.open(str(Path(__import__("tempfile").gettempdir()) / f"nimage_teste_{nome}.mp4"), "w")
            st = out.add_stream(nome, rate=30)
            st.width, st.height, st.pix_fmt = 256, 256, "yuv420p"
            fr = av.VideoFrame.from_ndarray(np.zeros((256, 256, 3), np.uint8), format="bgr24")
            for p in st.encode(fr):
                out.mux(p)
            for p in st.encode(None):
                out.mux(p)
            out.close()
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        _encoder_cache[nome] = ok
    return _encoder_cache[nome]


class Gravador:
    """Grava MP4 (H.264 + AAC) recebendo quadros BGR e um áudio estéreo completo (float32)."""

    def __init__(self, destino: str, largura: int, altura: int, fps: int, audio: np.ndarray, qualidade: str):
        import av
        self.av = av
        self.fps = fps
        self.out = av.open(str(destino), "w", options={"movflags": "+faststart"})
        crf, cq, preset = {"Alta": (18, 19, "medium"), "Padrão": (21, 23, "fast"),
                           "Rascunho rápido": (27, 30, "ultrafast"), "intermediario": (16, 17, "veryfast")
                           }.get(qualidade, (21, 23, "fast"))
        self.nvenc = encoder_funciona("h264_nvenc")
        if self.nvenc:
            self.vs = self.out.add_stream("h264_nvenc", rate=fps)
            self.vs.options = {"preset": "p5", "rc": "vbr", "cq": str(cq), "b": "0"}
        else:
            self.vs = self.out.add_stream("libx264", rate=fps)
            self.vs.options = {"crf": str(crf), "preset": preset}
        self.vs.width, self.vs.height, self.vs.pix_fmt = largura, altura, "yuv420p"
        self.vs.thread_type = "AUTO"
        self.as_ = self.out.add_stream("aac", rate=SR)
        self.as_.layout = "stereo"
        self.as_.bit_rate = 192000
        self.audio = np.ascontiguousarray(np.clip(audio, -1, 1).T, dtype=np.float32)  # (2, n) planar
        self.pos_audio = 0
        self.n_quadros = 0

    def _mux_audio_ate(self, t: float):
        alvo = min(self.audio.shape[1], int(t * SR))
        while self.pos_audio < alvo:
            fim = min(self.pos_audio + 1024, self.audio.shape[1])
            fr = self.av.AudioFrame.from_ndarray(np.ascontiguousarray(self.audio[:, self.pos_audio:fim]),
                                                 format="fltp", layout="stereo")
            fr.sample_rate = SR
            fr.pts = self.pos_audio
            fr.time_base = Fraction(1, SR)
            for p in self.as_.encode(fr):
                self.out.mux(p)
            self.pos_audio = fim

    def quadro(self, img: np.ndarray):
        fr = self.av.VideoFrame.from_ndarray(img, format="bgr24")
        fr.pts = self.n_quadros
        fr.time_base = Fraction(1, self.fps)
        for p in self.vs.encode(fr):
            self.out.mux(p)
        self.n_quadros += 1
        self._mux_audio_ate(self.n_quadros / self.fps + 0.5)

    def fechar(self):
        self._mux_audio_ate(self.n_quadros / self.fps)
        for p in self.vs.encode(None):
            self.out.mux(p)
        for p in self.as_.encode(None):
            self.out.mux(p)
        self.out.close()

    def abortar(self):
        try:
            self.out.close()
        except Exception:  # noqa: BLE001
            pass
