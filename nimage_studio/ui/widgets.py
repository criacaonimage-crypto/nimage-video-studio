"""Componentes reutilizados entre as telas."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from .. import media
from ..patterns import BIBLIOTECA_CATEGORIAS


def label(texto: str, nome: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(texto)
    if nome:
        lb.setObjectName(nome)
    lb.setWordWrap(wrap)
    return lb


def botao(texto: str, nome: str | None = None, ao_clicar=None) -> QPushButton:
    b = QPushButton(texto)
    if nome:
        b.setObjectName(nome)
    b.setCursor(Qt.PointingHandCursor)
    if ao_clicar:
        b.clicked.connect(ao_clicar)
    return b


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


def pagina() -> QWidget:
    w = QWidget()
    w.setObjectName("Page")
    return w


def aviso_pendente(texto: str) -> QLabel:
    """Etiqueta padrão para deixar explícito o que ainda não está implementado."""
    return label(f"⏳ {texto}", "Pending", wrap=True)


class ColorButton(QPushButton):
    def __init__(self, cor: str = "#FFFFFF"):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(130)
        self.clicked.connect(self._escolher)
        self.set_cor(cor)

    def set_cor(self, cor: str):
        self._cor = QColor(cor).name().upper() if QColor(cor).isValid() else "#FFFFFF"
        txt = "#000" if QColor(self._cor).lightness() > 140 else "#FFF"
        self.setText(self._cor)
        self.setStyleSheet(f"background:{self._cor}; color:{txt}; border-radius:8px; padding:7px;")

    def cor(self) -> str:
        return self._cor

    def _escolher(self):
        c = QColorDialog.getColor(QColor(self._cor), self, "Escolher cor")
        if c.isValid():
            self.set_cor(c.name())


# ---------- leitura de vídeo em segundo plano ----------
class _Sinais(QObject):
    pronto = Signal(str, dict, str)   # caminho, info, miniatura
    erro = Signal(str, str)           # caminho, mensagem


class LeitorVideo(QRunnable):
    def __init__(self, caminho: str):
        super().__init__()
        self.caminho = caminho
        self.sinais = _Sinais()

    def run(self):
        try:
            info = media.probe(self.caminho)
            try:
                thumb = media.miniatura(self.caminho, info["duracao"])
            except media.MediaError:
                thumb = ""
            self.sinais.pronto.emit(self.caminho, info, thumb)
        except Exception as e:  # noqa: BLE001 - qualquer falha vira mensagem para o usuário
            self.sinais.erro.emit(self.caminho, str(e))


def ler_video_async(caminho: str, ao_pronto, ao_erro) -> LeitorVideo:
    job = LeitorVideo(caminho)
    job.sinais.pronto.connect(ao_pronto)
    job.sinais.erro.connect(ao_erro)
    QThreadPool.globalInstance().start(job)
    return job


class VideoCard(QFrame):
    remover = Signal(str)
    selecionado = Signal(str)

    def __init__(self, caminho: str):
        super().__init__()
        self.caminho = caminho
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.thumb = QLabel("…")
        self.thumb.setFixedSize(160, 90)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#000; border-radius:6px; color:#666;")
        lay.addWidget(self.thumb)
        info = QVBoxLayout()
        self.nome = label(os.path.basename(caminho), "H2")
        self.nome.setWordWrap(True)
        self.detalhes = label("Lendo arquivo…", "Muted")
        self.detalhes.setWordWrap(True)
        info.addWidget(self.nome)
        info.addWidget(self.detalhes)
        info.addStretch()
        lay.addLayout(info, 1)
        col = QVBoxLayout()
        col.addWidget(botao("Ver", "Ghost", lambda: self.selecionado.emit(self.caminho)))
        col.addWidget(botao("Remover", "Ghost", lambda: self.remover.emit(self.caminho)))
        col.addStretch()
        lay.addLayout(col)

    def preencher(self, info: dict, thumb: str):
        partes = [media.formatar_duracao(info["duracao"]), f"{info['largura']}×{info['altura']}",
                  info["formato"], f"{info['fps']:g} fps" if info["fps"] else "",
                  media.formatar_tamanho(info["tamanho_bytes"]) if info["tamanho_bytes"] else "",
                  "" if info["tem_audio"] else "sem áudio"]
        self.detalhes.setText("  ·  ".join(p for p in partes if p))
        if thumb:
            pix = QPixmap(thumb).scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.thumb.setPixmap(pix)
        else:
            self.thumb.setText("sem prévia")

    def falhou(self, msg: str):
        self.detalhes.setText(msg)
        self.detalhes.setObjectName("Error")
        self.detalhes.setStyleSheet("color:#E5484D;")
        self.thumb.setText("erro")


# ---------- biblioteca de um padrão ----------
FILTROS = {
    "videos_apoio": "Vídeos (*.mp4 *.mov *.mkv *.avi *.m4v *.webm)",
    "imagens": "Imagens (*.png *.jpg *.jpeg *.webp *.bmp)",
    "trilhas": "Áudio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
    "efeitos_sonoros": "Áudio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
    "logos": "Logos e vinhetas (*.png *.svg *.jpg *.mov *.mp4 *.webm)",
    "referencias": "Vídeos e imagens (*.mp4 *.mov *.mkv *.png *.jpg *.jpeg)",
}


class LibraryEditor(QWidget):
    """Edita as listas de arquivos da biblioteca de um padrão.

    Os arquivos não são copiados: o padrão guarda o caminho. Arquivos que não existem mais aparecem em vermelho.
    """
    alterado = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.listas: dict[str, QListWidget] = {}
        for chave, titulo in BIBLIOTECA_CATEGORIAS.items():
            topo = QHBoxLayout()
            topo.addWidget(label(titulo, "H2"))
            topo.addStretch()
            topo.addWidget(botao("Adicionar arquivos", None, lambda _=False, k=chave: self._adicionar(k)))
            topo.addWidget(botao("Remover selecionado", "Danger", lambda _=False, k=chave: self._remover(k)))
            lay.addLayout(topo)
            lst = QListWidget()
            lst.setMinimumHeight(70)
            lst.setMaximumHeight(120)
            self.listas[chave] = lst
            lay.addWidget(lst)
        lay.addStretch()

    def _item(self, caminho: str) -> QListWidgetItem:
        it = QListWidgetItem(caminho)
        if not Path(caminho).exists():
            it.setForeground(QColor("#E5484D"))
            it.setToolTip("Arquivo não encontrado neste computador")
        return it

    def _adicionar(self, chave: str):
        arqs, _ = QFileDialog.getOpenFileNames(self, "Adicionar à biblioteca", "", FILTROS[chave] + ";;Todos (*.*)")
        existentes = set(self.valores()[chave])
        for a in arqs:
            if a not in existentes:
                self.listas[chave].addItem(self._item(a))
        if arqs:
            self.alterado.emit()

    def _remover(self, chave: str):
        lst = self.listas[chave]
        for it in lst.selectedItems():
            lst.takeItem(lst.row(it))
        self.alterado.emit()

    def carregar(self, biblioteca: dict):
        for chave, lst in self.listas.items():
            lst.clear()
            for c in biblioteca.get(chave, []):
                lst.addItem(self._item(c))

    def valores(self) -> dict:
        return {k: [lst.item(i).text() for i in range(lst.count())] for k, lst in self.listas.items()}
