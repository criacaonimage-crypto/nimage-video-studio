"""Tarefas demoradas em segundo plano (análise, planejamento, download de modelo) e janela de progresso."""
from __future__ import annotations

import copy
import traceback

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QVBoxLayout

from .. import analysis, config, plan, planner
from ..ai import ProvedorErro, provedor_de
from ..ai.ollama import Ollama, OllamaErro
from .widgets import botao, label


class PlanWorker(QThread):
    progresso = Signal(str, str, float)
    concluido = Signal(dict, list)      # plano, analises
    falhou = Signal(str)
    cancelado_sig = Signal()

    def __init__(self, projeto: dict, whisper_pref: str, cfg_ia: dict):
        super().__init__()
        self.projeto = copy.deepcopy(projeto)
        self.whisper_pref = whisper_pref
        self.cfg_ia = dict(cfg_ia)
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            vram = config.detect_gpu().get("vram_mb", 0)
            cancelado = lambda: self._cancelar  # noqa: E731
            analises = planner.analisar_projeto(self.projeto, self.whisper_pref, vram, self.progresso.emit, cancelado)

            prov = provedor_de(self.cfg_ia, vram)
            plano = planner.planejar(self.projeto, analises, prov, self.progresso.emit, cancelado)
            self.concluido.emit(plano, analises)
        except analysis.Cancelado:
            self.cancelado_sig.emit()
        except (analysis.AnaliseErro, plan.PlanoErro, ProvedorErro, OllamaErro) as e:
            self.falhou.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.falhou.emit(f"Erro inesperado: {e}\n\n{traceback.format_exc(limit=3)}")


class PullWorker(QThread):
    progresso = Signal(str, float)
    concluido = Signal()
    falhou = Signal(str)

    def __init__(self, url: str, modelo: str):
        super().__init__()
        self.url, self.modelo = url, modelo
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            Ollama(self.url).baixar(self.modelo, self.progresso.emit, lambda: self._cancelar)
            self.concluido.emit()
        except Exception as e:  # noqa: BLE001
            self.falhou.emit(str(e))


class ProgressoDialog(QDialog):
    """Janela de progresso com etapa, barra e botão cancelar."""

    def __init__(self, titulo: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        self.etapa = label(titulo, "H2")
        self.msg = label("Preparando…", "Muted", wrap=True)
        self.barra = QProgressBar()
        self.barra.setRange(0, 1000)
        self.barra.setTextVisible(False)
        self.barra.setFixedHeight(8)
        lay.addWidget(self.etapa)
        lay.addWidget(self.msg)
        lay.addWidget(self.barra)
        lay.addWidget(label("Você pode continuar usando o computador. Vídeos longos podem levar alguns minutos.",
                            "Muted", wrap=True))
        linha = QHBoxLayout()
        linha.addStretch()
        self.btn = botao("Cancelar", "Danger")
        linha.addWidget(self.btn)
        lay.addLayout(linha)

    def atualizar(self, etapa: str, msg: str, frac: float):
        if etapa:
            self.etapa.setText(etapa)
        self.msg.setText(msg)
        self.barra.setValue(int(max(0.0, min(1.0, frac)) * 1000))

    def cancelando(self):
        self.btn.setEnabled(False)
        self.msg.setText("Cancelando… aguarde a etapa atual terminar.")


class RenderWorker(QThread):
    progresso = Signal(str, str, float)
    concluido = Signal(dict)
    falhou = Signal(str)
    cancelado_sig = Signal()

    def __init__(self, plano: dict, saida: str, opcoes: dict, ollama_url: str = "", modelo: str = ""):
        super().__init__()
        self.plano, self.saida, self.opcoes = copy.deepcopy(plano), saida, dict(opcoes)
        self.ollama_url, self.modelo = ollama_url, modelo
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        from .. import renderer
        try:
            if self.modelo:  # libera a memória da placa de vídeo para a renderização
                Ollama(self.ollama_url).descarregar(self.modelo)
            info = renderer.renderizar(self.plano, self.saida, self.opcoes,
                                       lambda m, f: self.progresso.emit("Gerando o vídeo", m, f),
                                       lambda: self._cancelar)
            self.concluido.emit(info)
        except renderer.RenderCancelado:
            self.cancelado_sig.emit()
        except renderer.RenderErro as e:
            self.falhou.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.falhou.emit(f"Erro inesperado: {e}\n\n{traceback.format_exc(limit=3)}")


class AjusteWorker(QThread):
    concluido = Signal(dict)
    falhou = Signal(str)

    def __init__(self, pedido: str, plano: dict, projeto: dict, analises: list, cfg_ia: dict):
        super().__init__()
        self.pedido, self.plano, self.projeto = pedido, copy.deepcopy(plano), copy.deepcopy(projeto)
        self.analises, self.cfg_ia = analises, dict(cfg_ia)

    def run(self):
        from .. import revisions
        try:
            prov = provedor_de(self.cfg_ia, config.detect_gpu().get("vram_mb", 0))
            self.concluido.emit(revisions.ajustar(self.pedido, self.plano, self.projeto, self.analises, prov))
        except (plan.PlanoErro, ProvedorErro) as e:
            self.falhou.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.falhou.emit(f"Erro inesperado: {e}\n\n{traceback.format_exc(limit=3)}")
