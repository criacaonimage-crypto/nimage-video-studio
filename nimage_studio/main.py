"""Ponto de entrada do NIMAGE VIDEO STUDIO."""
from __future__ import annotations

import logging
import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import config
from .db import Database
from .ui.main_window import MainWindow
from .ui.theme import QSS


def _log_excecoes():
    logging.basicConfig(filename=config.data_dir() / "erros.log", level=logging.ERROR,
                        format="%(asctime)s %(message)s", encoding="utf-8")

    def hook(tipo, valor, tb):
        texto = "".join(traceback.format_exception(tipo, valor, tb))
        logging.error(texto)
        sys.__excepthook__(tipo, valor, tb)
        if QApplication.instance():
            QMessageBox.critical(None, "Erro inesperado",
                                 f"{valor}\n\nDetalhes salvos em {config.data_dir() / 'erros.log'}")

    sys.excepthook = hook


def autoteste() -> int:
    """`--autoteste`: confere as bibliotecas e faz uma edição mínima de verdade (sem executáveis externos)."""
    import tempfile
    from importlib.resources import files
    from pathlib import Path

    import numpy as np
    import av
    import cv2
    import ctranslate2
    import faster_whisper  # noqa: F401
    import scenedetect

    from . import avtools, media, renderer
    from .analysis import cuda_disponivel_para_whisper, detectar_cenas, extrair_audio
    vad = [p.name for p in files("faster_whisper").joinpath("assets").iterdir()]
    rosto = hasattr(cv2, "CascadeClassifier") and not cv2.CascadeClassifier(
        str(config.resources_dir() / "haarcascade_frontalface_default.xml")).empty()
    print(f"faster-whisper OK · ctranslate2 {ctranslate2.__version__} · scenedetect {scenedetect.__version__} · "
          f"PyAV {av.__version__} · VAD {vad} · detector de rosto {rosto} · "
          f"NVENC {avtools.encoder_funciona('h264_nvenc')} · CUDA para transcrição: {cuda_disponivel_para_whisper()}",
          flush=True)
    if not (any(v.endswith(".onnx") for v in vad) and rosto):
        return 1
    with tempfile.TemporaryDirectory() as d:
        v, out = Path(d) / "v.mp4", Path(d) / "saida.mp4"
        t = np.arange(int(4 * avtools.SR)) / avtools.SR
        tom = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        g = avtools.Gravador(str(v), 640, 360, 25, np.stack([tom, tom], axis=1), "Rascunho rápido")
        for k in range(100):
            img = np.zeros((360, 640, 3), np.uint8)
            img[:, : (k * 6) % 640] = (40, 120, 220)
            g.quadro(img)
        g.fechar()
        info = media.probe(v)
        extrair_audio(str(v), Path(d) / "a.wav")
        detectar_cenas(str(v), info["duracao"], lambda m, f: None)
        media.miniatura(v, info["duracao"])
        palavras = [{"p": w, "i": 0.3 + k * 0.5, "f": 0.75 + k * 0.5} for k, w in enumerate("teste de legenda ok".split())]
        clipe = {"id": "T1", "video": str(v), "video_nome": "v.mp4", "inicio": 0.0, "fim": 3.0, "duracao": 3.0,
                 "texto": "teste de legenda ok", "palavras": palavras, "destaques": ["legenda"], "zoom": 1.0}
        plano = {"formato": {"largura": 540, "altura": 960}, "clipes": [clipe, {**clipe, "id": "T2", "zoom": 1.1}],
                 "transicao": "Suaves", "abertura": {"tipo": "Título", "texto": "Autoteste"},
                 "encerramento": {"tipo": "Chamada para ação", "texto": "ok"},
                 "trilha": {"usar": True, "arquivo": str(v), "volume": 20, "abaixar_na_fala": True},
                 "legendas": {"ativar": True, "estilo": "Palavra-chave em destaque", "posicao": "Abaixo do queixo",
                              "tamanho": 54, "cor": "#FFFFFF", "cor_destaque": "#8800FF", "caixa_alta_destaque": True,
                              "fonte": ""},
                 "identidade": {"cores": {"fundo": "#10001E", "texto": "#FFFFFF", "principal": "#8800FF"},
                                "fonte_titulo": "", "tamanho_titulo": 72}}
        info = renderer.renderizar(plano, str(out), {"qualidade": "Rascunho rápido"})
        print(f"Renderização OK: {info['duracao']:.1f} s, {info['largura']}x{info['altura']}, {info['codificador']}",
              flush=True)
    return 0


def autoteste_ui() -> int:
    """Abre a janela, passa por todas as telas e carrega um vídeo no player (sem interação)."""
    import tempfile
    from pathlib import Path

    import numpy as np
    from PySide6.QtCore import QUrl
    from . import avtools
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    with tempfile.TemporaryDirectory() as d:
        os.environ["NIMAGE_DATA_DIR"] = d
        janela = MainWindow(Database())
        janela.show()
        for i in range(6):
            janela.ir_para(i)
            app.processEvents()
        v = Path(d) / "v.mp4"
        g = avtools.Gravador(str(v), 320, 240, 25, np.zeros((avtools.SR, 2), np.float32), "Rascunho rápido")
        for _ in range(25):
            g.quadro(np.full((240, 320, 3), 128, np.uint8))
        g.fechar()
        janela.nova.adicionar_videos([str(v)])
        import time
        fim = time.time() + 20
        while time.time() < fim and any(x is None for x in janela.nova.videos.values()):
            app.processEvents()
            time.sleep(0.05)
        ok = len(janela.nova.videos_validos()) == 1
        janela.ir_para(2)
        janela.previa.abrir(str(v))
        fim = time.time() + 5
        while time.time() < fim:
            app.processEvents()
            time.sleep(0.05)
        print(f"Interface OK · vídeo lido: {ok} · player: {'reserva' if janela.previa.modo_reserva else 'Qt'}", flush=True)
        janela.close()
    return 0 if ok else 1


def main() -> int:
    if not getattr(sys, "argv", None):
        sys.argv = [sys.executable]
    if "--autoteste" in sys.argv or os.environ.get("NIMAGE_AUTOTESTE"):
        return autoteste()
    if os.environ.get("NIMAGE_AUTOTESTE_UI"):
        return autoteste_ui()
    _log_excecoes()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    icone = config.resources_dir() / "icone.png"
    if icone.exists():
        app.setWindowIcon(QIcon(str(icone)))
    janela = MainWindow(Database())
    janela.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
