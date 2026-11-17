"""Ponto de entrada do NIMAGE VIDEO STUDIO."""
from __future__ import annotations

import logging
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
    """`NimageVideoStudio --autoteste`: confere se as bibliotecas de análise estão dentro do executável."""
    import faster_whisper  # noqa: F401
    import ctranslate2
    import scenedetect
    from .analysis import cuda_disponivel_para_whisper
    from importlib.resources import files
    import cv2
    vad = [p.name for p in files("faster_whisper").joinpath("assets").iterdir()]
    rosto = not cv2.CascadeClassifier(str(config.resources_dir() / "haarcascade_frontalface_default.xml")).empty()
    print(f"faster-whisper OK · ctranslate2 {ctranslate2.__version__} · scenedetect {scenedetect.__version__} · "
          f"VAD {vad} · detector de rosto {rosto} · ffmpeg {config.ffmpeg_path()} · "
          f"CUDA para transcrição: {cuda_disponivel_para_whisper()}")
    if not (any(v.endswith(".onnx") for v in vad) and rosto and config.ffmpeg_path()):
        return 1
    # renderização real mínima: título, trecho com legenda e trilha (confere libass, drawtext, xfade no FFmpeg)
    import tempfile
    from pathlib import Path
    from . import renderer
    with tempfile.TemporaryDirectory() as d:
        v, m, out = Path(d) / "v.mp4", Path(d) / "m.wav", Path(d) / "saida.mp4"
        ff = config.ffmpeg_path()
        config.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=4", "-f", "lavfi",
                    "-i", "sine=d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)])
        config.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:d=5", str(m)])
        palavras = [{"p": w, "i": 0.3 + k * 0.5, "f": 0.75 + k * 0.5} for k, w in enumerate("teste de legenda ok".split())]
        clipe = {"id": "T1", "video": str(v), "video_nome": "v.mp4", "inicio": 0.0, "fim": 3.0, "duracao": 3.0,
                 "texto": "teste de legenda ok", "palavras": palavras, "destaques": ["legenda"], "zoom": 1.0}
        plano = {"formato": {"largura": 540, "altura": 960}, "clipes": [clipe, {**clipe, "id": "T2", "zoom": 1.1}],
                 "transicao": "Suaves", "abertura": {"tipo": "Título", "texto": "Autoteste"},
                 "encerramento": {"tipo": "Chamada para ação", "texto": "ok"},
                 "trilha": {"usar": True, "arquivo": str(m), "volume": 20, "abaixar_na_fala": True},
                 "legendas": {"ativar": True, "estilo": "Palavra-chave em destaque", "posicao": "Abaixo do queixo",
                              "tamanho": 54, "cor": "#FFFFFF", "cor_destaque": "#8800FF", "caixa_alta_destaque": True,
                              "fonte": ""},
                 "identidade": {"cores": {"fundo": "#10001E", "texto": "#FFFFFF", "principal": "#8800FF"},
                                "fonte_titulo": "", "tamanho_titulo": 72}}
        info = renderer.renderizar(plano, str(out), {"qualidade": "Rascunho rápido"})
        print(f"Renderização OK: {info['duracao']:.1f} s, {info['largura']}x{info['altura']}, {info['codificador']}")
    return 0


def main() -> int:
    if "--autoteste" in sys.argv:
        return autoteste()
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
