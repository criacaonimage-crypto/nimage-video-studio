"""Janela de exportação: formato (proporção/resolução), qualidade e "Salvar como"."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QSpinBox

from ..patterns import FORMATOS
from ..renderer import QUALIDADES
from .widgets import label

DO_PROJETO = "Formato do projeto"
PERSONALIZADO = "Personalizado…"


class ExportDialog(QDialog):
    def __init__(self, formato_projeto: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar MP4")
        self.setMinimumWidth(460)
        self.fp = formato_projeto
        f = QFormLayout(self)
        f.setContentsMargins(22, 20, 22, 18)
        f.addRow(label("Exportar vídeo", "H2"))
        self.formato = QComboBox()
        self.formato.addItems([f"{DO_PROJETO} ({formato_projeto.get('largura')}×{formato_projeto.get('altura')})",
                               *FORMATOS.keys(), PERSONALIZADO])
        self.formato.currentIndexChanged.connect(self._fmt)
        self.larg = QSpinBox(); self.larg.setRange(64, 7680); self.larg.setSingleStep(2)
        self.alt = QSpinBox(); self.alt.setRange(64, 7680); self.alt.setSingleStep(2)
        lin = QHBoxLayout(); lin.addWidget(self.larg); lin.addWidget(label("×")); lin.addWidget(self.alt); lin.addStretch()
        self.qualidade = QComboBox(); self.qualidade.addItems(list(QUALIDADES)); self.qualidade.setCurrentText("Alta")
        f.addRow("Formato / proporção", self.formato)
        f.addRow("Tamanho (px)", lin)
        f.addRow("Qualidade", self.qualidade)
        f.addRow("", label("Mudar o formato reenquadra os trechos (centralizando no rosto).", "Muted", wrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Salvar como…")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._fmt()

    def _fmt(self, *_):
        t = self.formato.currentText()
        livre = t == PERSONALIZADO
        self.larg.setEnabled(livre); self.alt.setEnabled(livre)
        if t.startswith(DO_PROJETO):
            self.larg.setValue(self.fp.get("largura", 1920)); self.alt.setValue(self.fp.get("altura", 1080))
        elif t in FORMATOS:
            w, h = FORMATOS[t]; self.larg.setValue(w); self.alt.setValue(h)

    def opcoes(self) -> dict:
        return {"largura": self.larg.value(), "altura": self.alt.value(), "qualidade": self.qualidade.currentText()}
