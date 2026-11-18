"""Reprodutor de reserva (PyAV) usado quando o player do Qt não carrega neste PC."""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

import test_entrega2 as T2  # noqa: E402

dados = T2.dados
video_com_cortes = T2.video_com_cortes


def test_player_reserva(dados, video_com_cortes, monkeypatch):
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication
    from nimage_studio.ui.preview_page import PreviewPage
    from nimage_studio import media
    monkeypatch.setenv("NIMAGE_PLAYER", "reserva")
    app = QApplication.instance() or QApplication(sys.argv)
    pv = PreviewPage(); pv.resize(1000, 700); pv.show()
    assert pv.modo_reserva and "compatibilidade" in pv.titulo.text()
    pv.definir_videos([media.probe(video_com_cortes)], abrir=str(video_com_cortes))
    assert pv.player.duration() > 23000 and pv.tela_reserva.pixmap() is not None
    pv.player.setPosition(10000)
    assert 9900 <= pv.player.position() <= 10200
    pv.player.play()
    fim = time.time() + 1.5
    while time.time() < fim:
        app.processEvents(); time.sleep(0.02)
    assert pv.player.position() > 10300 and pv.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    pv.player.pause()
    # prévia de trechos do plano (fila) funciona no modo reserva
    clipe = {"id": "T1", "video": str(video_com_cortes), "video_nome": "v", "inicio": 5.0, "fim": 6.0}
    pv.plano = {"clipes": [clipe]}
    pv._iniciar_fila([clipe])
    fim = time.time() + 2.5
    while time.time() < fim and pv._fila:
        app.processEvents(); time.sleep(0.02)
    assert not pv._fila and pv.player.position() >= 5900
    pv.close()


def test_troca_automatica_para_reserva(dados, video_com_cortes):
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication
    from nimage_studio.ui.preview_page import PreviewPage
    app = QApplication.instance() or QApplication(sys.argv)
    pv = PreviewPage()
    from PySide6.QtCore import QUrl
    pv.player.setSource(QUrl.fromLocalFile(str(video_com_cortes)))
    pv._erro_player(QMediaPlayer.Error.ResourceError, "bloqueado")   # simula falha do player do Qt
    app.processEvents()
    assert pv.modo_reserva and pv.player.duration() > 23000
    pv.close()
