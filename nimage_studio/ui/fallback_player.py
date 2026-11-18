"""Reprodutor de reserva (sem som) usando PyAV, para quando o player do Qt não puder carregar seus
componentes de vídeo (por exemplo, bloqueados pelo Controle de Aplicativo Inteligente do Windows).

Imita a parte da API do QMediaPlayer usada pela tela de prévia.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QLabel


class PlayerReserva(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    FPS = 15

    def __init__(self, tela: QLabel, parent=None):
        super().__init__(parent)
        self.tela = tela
        self._url = QUrl()
        self._c = None
        self._iter = None
        self._dur = 0
        self._pos = 0
        self._estado = QMediaPlayer.PlaybackState.StoppedState
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.FPS))
        self._timer.timeout.connect(self._tick)

    # ---------- API compatível ----------
    def setSource(self, url: QUrl):
        self.stop()
        self._fechar()
        self._url = url
        if url.isEmpty():
            self.tela.clear()
            return
        try:
            import av
            self._c = av.open(url.toLocalFile())
            vs = self._c.streams.video[0]
            vs.thread_type = "AUTO"
            self._dur = int((self._c.duration or 0) / 1000)
            self.durationChanged.emit(self._dur)
            self._buscar(0)
            self._mostrar_proximo()
            self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
        except Exception as e:  # noqa: BLE001
            self.errorOccurred.emit(QMediaPlayer.Error.ResourceError, str(e))

    def source(self) -> QUrl:
        return self._url

    def play(self):
        if self._c is None:
            return
        self._estado = QMediaPlayer.PlaybackState.PlayingState
        self._timer.start()
        self.playbackStateChanged.emit(self._estado)

    def pause(self):
        self._timer.stop()
        if self._estado == QMediaPlayer.PlaybackState.PlayingState:
            self._estado = QMediaPlayer.PlaybackState.PausedState
            self.playbackStateChanged.emit(self._estado)

    def stop(self):
        self._timer.stop()
        if self._estado != QMediaPlayer.PlaybackState.StoppedState:
            self._estado = QMediaPlayer.PlaybackState.StoppedState
            self.playbackStateChanged.emit(self._estado)

    def playbackState(self):
        return self._estado

    def position(self) -> int:
        return self._pos

    def duration(self) -> int:
        return self._dur

    def setPosition(self, ms: int):
        if self._c is None:
            return
        self._buscar(max(0, int(ms)))
        self._mostrar_proximo()

    def setAudioOutput(self, *_):
        pass

    def setVideoOutput(self, *_):
        pass

    def errorString(self) -> str:
        return ""

    # ---------- interno ----------
    def _fechar(self):
        if self._c is not None:
            try:
                self._c.close()
            except Exception:  # noqa: BLE001
                pass
        self._c, self._iter = None, None

    def _buscar(self, ms: int):
        vs = self._c.streams.video[0]
        alvo = ms / 1000
        if alvo > 0.2:
            self._c.seek(int(alvo / vs.time_base), stream=vs, backward=True)
        else:
            self._c.seek(0, stream=vs)
        self._iter = self._c.decode(vs)
        self._alvo = alvo
        self._pos = ms

    def _mostrar_proximo(self):
        try:
            while True:
                f = next(self._iter)
                if f.time is not None and f.time + 1e-3 < self._alvo:
                    continue
                break
        except StopIteration:
            self.pause()
            self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
            return
        self._alvo = (f.time or self._alvo) + 1 / self.FPS
        self._pos = int((f.time or 0) * 1000)
        img = f.to_ndarray(format="rgb24")
        k = (int(getattr(f, "rotation", 0) or 0) // 90) % 4
        if k:
            img = np.ascontiguousarray(np.rot90(img, k))
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.tela.setPixmap(QPixmap.fromImage(qimg).scaled(self.tela.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation))
        self.positionChanged.emit(self._pos)

    def _tick(self):
        self._mostrar_proximo()
