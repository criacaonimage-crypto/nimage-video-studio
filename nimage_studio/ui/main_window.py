"""Janela principal: barra lateral + telas."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import config
from ..db import Database
from .new_edit_page import NewEditPage
from .other_pages import HomePage, LibraryPage, SettingsPage, _HardwareThread
from .patterns_page import PatternsPage
from .preview_page import PreviewPage
from .widgets import label
from .export_dialog import ExportDialog
from .workers import AjusteWorker, PlanWorker, ProgressoDialog, RenderWorker
from .. import revisions
from ..ai import config_ia
from ..ai.ollama import Ollama
from ..plan import hash_plano, sanitizar_nome

TELAS = ["Início", "Nova edição", "Edição e prévia", "Padrões", "Biblioteca", "Configurações"]
ICONES = ["⌂", "＋", "▶", "◆", "▤", "⚙"]


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle(config.APP_NAME)
        self.resize(1360, 840)
        self.setMinimumSize(1100, 700)

        central = QWidget()
        raiz = QHBoxLayout(central)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        # ----- barra lateral -----
        side = QFrame(); side.setObjectName("Sidebar"); side.setFixedWidth(220)
        sl = QVBoxLayout(side); sl.setContentsMargins(14, 22, 14, 18); sl.setSpacing(4)
        sl.addWidget(label("NIMAGE", "Brand"))
        sl.addWidget(label("VIDEO STUDIO", "BrandSub"))
        sl.addSpacing(24)
        self.grupo = QButtonGroup(self)
        self.grupo.setExclusive(True)
        for i, (nome, ic) in enumerate(zip(TELAS, ICONES)):
            b = QPushButton(f"{ic}   {nome}")
            b.setObjectName("NavButton"); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            self.grupo.addButton(b, i)
            sl.addWidget(b)
        self.grupo.idClicked.connect(self.ir_para)
        sl.addStretch()
        sl.addWidget(label(f"v{config.APP_VERSION}", "Muted"))
        raiz.addWidget(side)

        # ----- telas -----
        self.pilha = QStackedWidget()
        self.home = HomePage(db)
        self.nova = NewEditPage(db)
        self.previa = PreviewPage()
        self.padroes = PatternsPage(db)
        self.biblioteca = LibraryPage(db)
        self.config = SettingsPage(db)
        for w in (self.home, self.nova, self.previa, self.padroes, self.biblioteca, self.config):
            self.pilha.addWidget(w)
        raiz.addWidget(self.pilha, 1)
        self.setCentralWidget(central)

        # ----- ligações -----
        self.home.nova_edicao.connect(self.nova_edicao)
        self.home.abrir_projeto.connect(self.abrir_projeto)
        self.nova.abrir_previa.connect(self.mostrar_previa)
        self.nova.projeto_salvo.connect(lambda _: self._projeto_salvo())
        self.nova.pedir_plano.connect(self.planejar)
        self.previa.refazer_plano.connect(self._refazer_plano)
        self.previa.plano_alterado.connect(self._plano_alterado)
        self.previa.gerar_video.connect(self.gerar_video)
        self.previa.exportar.connect(self.exportar)
        self.previa.pedir_ajuste.connect(self.pedir_ajuste)
        self.previa.desfazer_ajuste.connect(self.desfazer_ajuste)
        self.padroes.padroes_alterados.connect(self._padroes_mudaram)
        self.biblioteca.alterado.connect(self._padroes_mudaram_fora_do_editor)
        self.config.padroes_restaurados.connect(self._padroes_mudaram_fora_do_editor)

        self._hw = _HardwareThread(self)
        self._hw.pronto.connect(self.home.mostrar_hardware)
        self._hw.pronto.connect(self.config.mostrar_hardware)
        self._hw.start()

        self._limpar_renders_orfaos()
        self.ir_para(0)

    def _limpar_renders_orfaos(self):
        """Apaga resultados antigos que nenhum projeto usa mais (sobras de ajustes)."""
        pasta = config.data_dir() / "renders"
        if not pasta.is_dir():
            return
        usados = {str(Path((p["dados"].get("render") or {}).get("arquivo", ""))) for p in self.db.listar_projetos(100000)}
        for f in pasta.glob("*.mp4"):
            if str(f) not in usados:
                try:
                    f.unlink()
                except OSError:
                    pass

    # ---------- navegação ----------
    def ir_para(self, i: int):
        atual = self.pilha.currentIndex()
        if atual == 3 and i != 3 and not self.padroes.confirmar_saida():
            self.grupo.button(atual).setChecked(True)
            return
        if atual == 2 and i != 2:
            self.previa.parar()
        if i == 0:
            self.home.recarregar()
        if i == 2:
            self.previa.definir_videos(self.nova.videos_validos())
            self._atualizar_previa()
            self.previa.mostrar_transcricoes(self.nova.transcricoes)
        if i == 5:
            self.config.verificar_ia()
        self.pilha.setCurrentIndex(i)
        self.grupo.button(i).setChecked(True)

    def nova_edicao(self):
        if self.nova.videos or self.nova.projeto_id:
            r = QMessageBox.question(self, "Nova edição", "Começar uma nova edição? O que não foi salvo no projeto atual será perdido.")
            if r != QMessageBox.Yes:
                self.ir_para(1)
                return
        self.nova.limpar()
        self.ir_para(1)

    def abrir_projeto(self, pid: int):
        self.nova.carregar_projeto(pid)
        self.ir_para(1)

    def mostrar_previa(self, caminho: str):
        self.ir_para(2)
        self.previa.definir_videos(self.nova.videos_validos(), abrir=caminho)

    def _projeto_salvo(self):
        self.home.recarregar()
        self.statusBar().showMessage("Projeto salvo ✓", 4000)

    # ---------- IA: análise + plano ----------
    # ---------- IA: análise + plano ----------
    def _ia_pronta(self) -> bool:
        cfg = config_ia(self.db)
        if cfg["modo"] == "claude":
            if not cfg["claude_chave"]:
                QMessageBox.warning(self, "Claude", "Informe a chave de API da Anthropic em Configurações, "
                                    "ou volte para a IA local.")
                return False
            return True
        if not Ollama(cfg["ollama_url"]).disponivel():
            QMessageBox.warning(self, "IA local indisponível",
                                "Não encontrei o Ollama neste computador.\n\n"
                                "1. Instale em ollama.com (gratuito) e deixe-o aberto.\n"
                                "2. Em Configurações → Inteligência artificial, baixe o modelo recomendado.\n\n"
                                "Depois tente de novo.")
            return False
        return True

    def planejar(self):
        if not self._ia_pronta():
            return
        if self.nova.plano and QMessageBox.question(
                self, "Refazer plano", "Este projeto já tem um plano. Fazer um novo plano substitui o atual. Continuar?") != QMessageBox.Yes:
            return
        if not self.nova.salvar():
            return
        projeto = self.nova.coletar()
        self._worker = PlanWorker(projeto, self.db.get("whisper_modelo") or "Automático", config_ia(self.db))
        dlg = ProgressoDialog("Analisando e planejando", self)
        self._dlg = dlg
        dlg.btn.clicked.connect(lambda: (self._worker.cancelar(), dlg.cancelando()))
        self._worker.progresso.connect(dlg.atualizar)
        self._worker.concluido.connect(self._plano_pronto)
        self._worker.falhou.connect(self._falhou)
        self._worker.cancelado_sig.connect(lambda: (dlg.accept(), self.statusBar().showMessage("Análise cancelada.", 5000)))
        self._worker.finished.connect(dlg.accept)
        self._worker.start()
        dlg.exec()

    def _arquivo_analises(self) -> Path:
        return config.data_dir() / "projetos" / f"{self.nova.projeto_id}_analises.json"

    def _plano_pronto(self, plano: dict, analises: list):
        if self.nova.plano:
            self.nova.historico = (self.nova.historico + [self.nova.plano])[-20:]
        self.nova.plano = plano
        self.nova.transcricoes = [{"nome": a["info"]["nome"], "modelo": a.get("modelo_transcricao", ""),
                                   "segmentos": [{"inicio": s["inicio"], "fim": s["fim"], "texto": s["texto"]}
                                                 for s in a.get("transcricao", [])]} for a in analises]
        self.nova.salvar()
        revisions.salvar_analises(self._arquivo_analises(), analises)
        self.ir_para(2)
        self.previa.abas.setCurrentIndex(0)

    def _falhou(self, msg: str):
        if getattr(self, "_dlg", None):
            self._dlg.accept()
        QMessageBox.warning(self, "Não foi possível concluir", msg)

    def _refazer_plano(self):
        self.ir_para(1)
        self.planejar()

    def _plano_alterado(self, plano: dict):
        self.nova.plano = plano
        self.nova.salvar()
        self._atualizar_previa()

    def _atualizar_previa(self):
        p = self.nova.plano
        if p is not None:
            p["_pode_desfazer"] = bool(self.nova.historico)
        self.previa.mostrar_plano(p)
        self.previa.mostrar_resultado(self.nova.render, hash_plano(p))
        aj = (p or {}).get("ultimo_ajuste")
        if aj:
            txt = f"Último ajuste: “{aj['pedido']}” → {aj.get('entendimento') or ''} ({'; '.join(aj['feitas'])})"
            if aj.get("avisos"):
                txt += "  ·  ⚠ " + " ".join(aj["avisos"])
            self.previa.ultimo_ajuste.setText(txt)
        else:
            self.previa.ultimo_ajuste.setText("")

    # ---------- gerar vídeo / exportar ----------
    def _render(self, saida: str, opcoes: dict, ao_concluir) -> None:
        cfg = config_ia(self.db)
        modelo = (cfg["ollama_modelo"] if cfg["modo"] == "local" else "")
        w = RenderWorker(self.nova.plano, saida, opcoes, cfg["ollama_url"], modelo)
        self._rworker = w
        dlg = ProgressoDialog("Gerando o vídeo", self)
        self._dlg = dlg
        dlg.btn.clicked.connect(lambda: (w.cancelar(), dlg.cancelando()))
        w.progresso.connect(dlg.atualizar)
        w.concluido.connect(ao_concluir)
        w.falhou.connect(self._falhou)
        w.cancelado_sig.connect(lambda: self.statusBar().showMessage("Geração cancelada.", 5000))
        w.finished.connect(dlg.accept)
        w.start()
        dlg.exec()

    def gerar_video(self, tocar: bool = True):
        if not self.nova.plano:
            return
        if not self.nova.salvar():
            return
        pasta = config.data_dir() / "renders"
        saida = str(pasta / f"projeto_{self.nova.projeto_id}_{int(time.time())}.mp4")
        hp = hash_plano(self.nova.plano)

        def pronto(info: dict):
            antigo = (self.nova.render or {}).get("arquivo")
            info.update({"plano_hash": hp, "qualidade": "Padrão"})
            self.nova.render = info
            self.nova.salvar()
            if antigo and antigo != info["arquivo"]:
                self.previa.soltar_arquivo(antigo)   # o player segura o arquivo no Windows
                try:
                    Path(antigo).unlink(missing_ok=True)
                except OSError:
                    pass   # ainda em uso; a limpeza de renders antigos remove depois
            self._atualizar_previa()
            if tocar:
                self.previa.tocar_resultado()

        self._render(saida, {"qualidade": "Padrão"}, pronto)

    def exportar(self):
        if not self.nova.plano:
            return
        dlg = ExportDialog(self.nova.plano.get("formato", {}), self)
        if dlg.exec() != ExportDialog.Accepted:
            return
        op = dlg.opcoes()
        pasta = self.db.get("pasta_saida") or str(Path.home() / "Videos")
        nome = sanitizar_nome(self.nova.nome.text().strip() or self.nova.plano.get("titulo", "video")) + ".mp4"
        destino, _ = QFileDialog.getSaveFileName(self, "Salvar vídeo como", str(Path(pasta) / nome), "Vídeo MP4 (*.mp4)")
        if not destino:
            return
        if not destino.lower().endswith(".mp4"):
            destino += ".mp4"
        r = self.nova.render or {}
        fmt = self.nova.plano.get("formato", {})
        igual = (r.get("plano_hash") == hash_plano(self.nova.plano) and Path(r.get("arquivo", "")).exists()
                 and op["qualidade"] == r.get("qualidade") and (op["largura"], op["altura"]) == (r.get("largura"), r.get("altura")))
        if igual:
            shutil.copy(r["arquivo"], destino)
            self._exportado(destino)
        else:
            self._render(destino, op, lambda info: self._exportado(info["arquivo"]))
        self.db.set("pasta_saida", str(Path(destino).parent))

    def _exportado(self, arquivo: str):
        self.statusBar().showMessage(f"Exportado: {arquivo}", 10000)
        QMessageBox.information(self, "Vídeo exportado", f"Vídeo salvo em:\n{arquivo}")

    # ---------- ajustes em linguagem natural ----------
    def pedir_ajuste(self, pedido: str):
        if not self.nova.plano or not self._ia_pronta():
            return
        analises = revisions.carregar_analises(self._arquivo_analises())
        if not analises:
            QMessageBox.warning(self, "Ajuste", "Não encontrei a análise deste projeto. Clique em “Refazer plano”.")
            return
        w = AjusteWorker(pedido, self.nova.plano, self.nova.coletar(), analises, config_ia(self.db))
        self._aworker = w
        dlg = ProgressoDialog("Aplicando ajuste", self)
        self._dlg = dlg
        dlg.atualizar("Aplicando ajuste", f"A IA está interpretando: “{pedido}”…", 0.3)
        dlg.btn.setEnabled(False)
        w.concluido.connect(self._ajuste_pronto)
        w.falhou.connect(self._falhou)
        w.finished.connect(dlg.accept)
        w.start()
        dlg.exec()

    def _ajuste_pronto(self, plano: dict):
        self._dlg.accept()
        self.nova.historico = (self.nova.historico + [self.nova.plano])[-20:]
        self.nova.plano = plano
        self.nova.salvar()
        self.previa.ajuste.clear()
        self._atualizar_previa()
        self.gerar_video()   # refaz o vídeo; trechos que não mudaram vêm do cache

    def desfazer_ajuste(self):
        if not self.nova.historico:
            return
        self.nova.plano = self.nova.historico.pop()
        self.nova.salvar()
        self._atualizar_previa()
        self.statusBar().showMessage("Ajuste desfeito. Clique em “Gerar vídeo” para atualizar o resultado.", 6000)

    def _padroes_mudaram(self):
        self.nova.recarregar_padroes()
        self.biblioteca.recarregar()

    def _padroes_mudaram_fora_do_editor(self):
        self.nova.recarregar_padroes()
        self.padroes.atual_id = None
        self.padroes.recarregar()

    def closeEvent(self, e):
        if self.pilha.currentIndex() == 3 and not self.padroes.confirmar_saida():
            e.ignore()
            return
        self.previa.player.stop()
        self._encerrar_tarefas()
        e.accept()

    def _encerrar_tarefas(self):
        """Espera as tarefas em segundo plano terminarem (fechar com tarefa rodando derruba o programa)."""
        from PySide6.QtCore import QThreadPool
        for nome in ("_worker", "_rworker", "_aworker"):
            t = getattr(self, nome, None)
            if t is not None and t.isRunning():
                if hasattr(t, "cancelar"):
                    t.cancelar()
                t.wait(15000)
        for t in (self._hw, getattr(self.config, "_pull", None), getattr(self.config, "_cuda_w", None)):
            if t is not None and t.isRunning():
                t.wait(15000)
        QThreadPool.globalInstance().waitForDone(15000)
