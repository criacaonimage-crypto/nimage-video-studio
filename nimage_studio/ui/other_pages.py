"""Telas de Início, Biblioteca de recursos e Configurações."""
from __future__ import annotations

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QScrollArea, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from .. import config, media
from ..db import Database
from ..ai import CLAUDE_MODELO_PADRAO
from ..ai.claude import Claude, ClaudeErro
from ..ai.ollama import URL_PADRAO, Ollama, OllamaErro, recomendado_para
from .widgets import LibraryEditor, botao, card, label
from .workers import ProgressoDialog, PullWorker


class _HardwareThread(QThread):
    pronto = Signal(dict)

    def run(self):
        self.pronto.emit(config.hardware_profile())


# ---------------------------------------------------------------- Início
class HomePage(QWidget):
    nova_edicao = Signal()
    abrir_projeto = Signal(int)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 36, 40, 36)
        lay.setSpacing(14)
        lay.addWidget(label("Bem-vindo ao NIMAGE VIDEO STUDIO", "H1"))
        lay.addWidget(label("Coloque um vídeo, escolha um padrão, explique como quer a edição e receba o vídeo pronto.",
                            "Muted", wrap=True))
        topo = QHBoxLayout()
        b = botao("＋  Nova edição", "Primary", self.nova_edicao.emit)
        b.setFixedHeight(44); b.setFixedWidth(200)
        topo.addWidget(b)
        topo.addStretch()
        lay.addLayout(topo)

        lay.addSpacing(10)
        lay.addWidget(label("Projetos recentes", "H2"))
        self.lista = QListWidget()
        self.lista.itemDoubleClicked.connect(lambda it: self.abrir_projeto.emit(it.data(Qt.UserRole)))
        lay.addWidget(self.lista, 1)
        acoes = QHBoxLayout()
        acoes.addWidget(botao("Abrir", None, self._abrir))
        acoes.addWidget(botao("Excluir projeto", "Danger", self._excluir))
        acoes.addStretch()
        lay.addLayout(acoes)
        self.hw = label("Verificando computador…", "Muted")
        lay.addWidget(self.hw)
        self.recarregar()

    def mostrar_hardware(self, hw: dict):
        g = hw["gpu"]
        gpu = f"{g['nome']} ({g['vram_mb'] // 1024} GB)" if g else "nenhuma GPU NVIDIA detectada"
        ff = "FFmpeg OK" if config.ffmpeg_path() else "FFmpeg NÃO encontrado"
        self.hw.setText(f"Computador: {gpu} · perfil {hw['perfil']} · {ff}"
                        + (" · aceleração NVENC disponível" if hw["nvenc"] else ""))

    def recarregar(self):
        self.lista.clear()
        projs = self.db.listar_projetos()
        for p in projs:
            d = p["dados"]
            nv = len(d.get("videos", []))
            padrao = (d.get("padrao") or {}).get("nome", "—")
            it = QListWidgetItem(f"{p['nome']}\n{padrao} · {nv} vídeo(s) · atualizado em {p['atualizado'].replace('T', ' ')}")
            it.setData(Qt.UserRole, p["id"])
            self.lista.addItem(it)
        if not projs:
            it = QListWidgetItem("Nenhum projeto ainda. Clique em “Nova edição” para começar.")
            it.setFlags(Qt.NoItemFlags)
            self.lista.addItem(it)

    def _sel(self):
        it = self.lista.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _abrir(self):
        pid = self._sel()
        if pid:
            self.abrir_projeto.emit(pid)

    def _excluir(self):
        pid = self._sel()
        if pid and QMessageBox.question(self, "Excluir projeto",
                                        "Excluir este projeto? Os vídeos originais não são apagados.") == QMessageBox.Yes:
            self.db.excluir_projeto(pid)
            self.recarregar()


# ---------------------------------------------------------------- Biblioteca
class LibraryPage(QWidget):
    alterado = Signal()

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setObjectName("Page")
        self.pid = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.addWidget(label("Biblioteca de recursos", "H1"))
        lay.addWidget(label("Vídeos de apoio, imagens, trilhas, efeitos sonoros, logos e referências de cada padrão. "
                            "Os arquivos continuam onde estão (pasta local, servidor ou HD externo); o programa guarda o caminho. "
                            "Caminhos em vermelho não foram encontrados neste computador.", "Muted", wrap=True))
        topo = QHBoxLayout()
        topo.addWidget(label("Padrão:"))
        self.combo = QComboBox(); self.combo.setMinimumWidth(260)
        self.combo.currentIndexChanged.connect(self._trocar)
        topo.addWidget(self.combo)
        topo.addStretch()
        self.status = label("", "Muted")
        topo.addWidget(self.status)
        topo.addWidget(botao("Salvar biblioteca", "Primary", self.salvar))
        lay.addLayout(topo)
        self.editor = LibraryEditor()
        self.editor.alterado.connect(lambda: self.status.setText("Alterações não salvas"))
        s = QScrollArea(); s.setWidgetResizable(True); s.setWidget(self.editor)
        lay.addWidget(s, 1)
        self.recarregar()

    def recarregar(self):
        atual = self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in self.db.listar_padroes():
            self.combo.addItem(p["dados"]["nome"], p["id"])
        i = self.combo.findData(atual)
        self.combo.setCurrentIndex(max(i, 0))
        self.combo.blockSignals(False)
        self._trocar()

    def _trocar(self, *_):
        self.pid = self.combo.currentData()
        p = self.db.obter_padrao(self.pid) if self.pid is not None else None
        self.editor.carregar(p["biblioteca"] if p else {})
        self.status.setText("")

    def salvar(self):
        if self.pid is None:
            return
        p = self.db.obter_padrao(self.pid)
        p["biblioteca"] = self.editor.valores()
        self.db.salvar_padrao(self.pid, p)
        self.status.setText("Salvo ✓")
        self.alterado.emit()


# ---------------------------------------------------------------- Configurações
class SettingsPage(QWidget):
    padroes_restaurados = Signal()

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setObjectName("Page")
        externo = QVBoxLayout(self)
        externo.setContentsMargins(0, 0, 0, 0)
        cont = QWidget()
        lay = QVBoxLayout(cont)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        s = QScrollArea(); s.setWidgetResizable(True); s.setWidget(cont)
        externo.addWidget(s)

        lay.addWidget(label("Configurações", "H1"))

        # Computador
        c1 = card(); f1 = QFormLayout(c1); f1.setContentsMargins(18, 18, 18, 18)
        f1.addRow(label("Computador e ferramentas", "H2"))
        self.l_ffmpeg = label("", wrap=True)
        self.l_gpu = label("Verificando…", wrap=True)
        f1.addRow("FFmpeg", self.l_ffmpeg)
        f1.addRow("Placa de vídeo", self.l_gpu)
        lay.addWidget(c1)

        # Pastas
        c2 = card(); f2 = QFormLayout(c2); f2.setContentsMargins(18, 18, 18, 18)
        f2.addRow(label("Pastas", "H2"))
        self.saida = QLineEdit(db.get("pasta_saida", "") or "")
        self.saida.setPlaceholderText("Pasta sugerida ao exportar (o “Salvar como” sempre deixa escolher outra)")
        self.saida.editingFinished.connect(lambda: db.set("pasta_saida", self.saida.text().strip()))
        linha = QHBoxLayout(); linha.addWidget(self.saida, 1); linha.addWidget(botao("Escolher…", None, self._escolher_saida))
        f2.addRow("Pasta sugerida para exportar", linha)
        dados = QHBoxLayout()
        dados.addWidget(label(str(config.data_dir()), "Muted", wrap=True), 1)
        dados.addWidget(botao("Abrir pasta", None, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.data_dir())))))
        f2.addRow("Dados do programa", dados)
        lay.addWidget(c2)

        # IA
        c3 = card(); f3 = QFormLayout(c3); f3.setContentsMargins(18, 18, 18, 18); f3.setVerticalSpacing(10)
        f3.addRow(label("Inteligência artificial local (Ollama)", "H2"))
        self.ia_status = label("Clique em “Verificar”.", "Muted", wrap=True)
        lin = QHBoxLayout(); lin.addWidget(self.ia_status, 1); lin.addWidget(botao("Verificar", None, self.verificar_ia))
        f3.addRow("Situação", lin)
        self.ollama_url = QLineEdit(db.get("ollama_url", "") or "")
        self.ollama_url.setPlaceholderText(URL_PADRAO + "  (padrão)")
        self.ollama_url.editingFinished.connect(lambda: db.set("ollama_url", self.ollama_url.text().strip()))
        f3.addRow("Endereço do Ollama", self.ollama_url)
        self.modelo = QComboBox(); self.modelo.setEditable(True)
        self.modelo.setMinimumWidth(240)
        if db.get("ollama_modelo"):
            self.modelo.addItem(db.get("ollama_modelo"))
        self.modelo.currentTextChanged.connect(lambda t: db.set("ollama_modelo", t.strip()))
        f3.addRow("Modelo usado", self.modelo)
        self.recomendado = label("", "Muted", wrap=True)
        self.btn_baixar = botao("Baixar modelo recomendado", "Primary", self.baixar_modelo)
        lin2 = QHBoxLayout(); lin2.addWidget(self.recomendado, 1); lin2.addWidget(self.btn_baixar)
        f3.addRow("Recomendado", lin2)
        self.whisper = QComboBox(); self.whisper.addItems(["Automático", "large-v3", "medium", "small"])
        self.whisper.setCurrentText(db.get("whisper_modelo") or "Automático")
        self.whisper.currentTextChanged.connect(lambda t: db.set("whisper_modelo", t))
        f3.addRow("Transcrição (Whisper)", self.whisper)
        self.cuda_status = label("", "Muted", wrap=True)
        self.btn_cuda = botao("Ativar (baixa ~1,3 GB, uma vez)", None, self.baixar_cuda)
        lin3 = QHBoxLayout(); lin3.addWidget(self.cuda_status, 1); lin3.addWidget(self.btn_cuda)
        f3.addRow("Transcrição pela placa de vídeo", lin3)
        self._atualizar_cuda()
        f3.addRow("", label("Automático: large-v3 na placa de vídeo (melhor para português) ou small no processador. "
                            "O modelo é baixado na primeira análise (precisa de internet uma vez).", "Muted", wrap=True))
        lay.addWidget(c3)

        # Claude (opcional)
        c5 = card(); f5 = QFormLayout(c5); f5.setContentsMargins(18, 18, 18, 18); f5.setVerticalSpacing(10)
        f5.addRow(label("IA usada para planejar e ajustar", "H2"))
        self.modo = QComboBox(); self.modo.addItems(["IA local (Ollama) — padrão, gratuita", "Claude (API da Anthropic) — opcional"])
        self.modo.setCurrentIndex(1 if db.get("ia_modo") == "claude" else 0)
        self.modo.currentIndexChanged.connect(self._mudar_modo)
        f5.addRow("Usar", self.modo)
        self.claude_chave = QLineEdit(db.get("claude_chave", "") or "")
        self.claude_chave.setEchoMode(QLineEdit.Password)
        self.claude_chave.setPlaceholderText("sk-ant-…  (criada em console.anthropic.com)")
        self.claude_chave.editingFinished.connect(lambda: db.set("claude_chave", self.claude_chave.text().strip()))
        f5.addRow("Chave de API", self.claude_chave)
        self.claude_modelo = QLineEdit(db.get("claude_modelo", "") or CLAUDE_MODELO_PADRAO)
        self.claude_modelo.editingFinished.connect(lambda: db.set("claude_modelo", self.claude_modelo.text().strip()))
        f5.addRow("Modelo do Claude", self.claude_modelo)
        self.claude_status = label("", "Muted", wrap=True)
        lin5 = QHBoxLayout(); lin5.addWidget(self.claude_status, 1); lin5.addWidget(botao("Testar Claude", None, self.testar_claude))
        f5.addRow("Situação", lin5)
        f5.addRow("", label("Com o Claude, só a transcrição e os dados dos trechos saem do computador — os vídeos nunca são "
                            "enviados. O uso é cobrado pela Anthropic na conta da chave. A IA local continua disponível.",
                            "Muted", wrap=True))
        lay.addWidget(c5)
        self.vram = 0

        # Padrões
        c4 = card(); v4 = QVBoxLayout(c4); v4.setContentsMargins(18, 18, 18, 18)
        v4.addWidget(label("Padrões do programa", "H2"))
        v4.addWidget(label("Volta os padrões que vêm com o programa à versão original, inclusive os que foram excluídos. "
                           "Padrões personalizados não são afetados.", "Muted", wrap=True))
        v4.addWidget(botao("Restaurar padrões originais", "Danger", self._restaurar), 0, Qt.AlignLeft)
        lay.addWidget(c4)

        lay.addWidget(label(f"{config.APP_NAME} · versão {config.APP_VERSION}", "Muted"))
        lay.addStretch()
        self.atualizar_ferramentas()

    def atualizar_ferramentas(self):
        ff = config.ffmpeg_path()
        self.l_ffmpeg.setText(ff if ff else "NÃO encontrado — reinstale o programa ou instale o FFmpeg.")
        self.l_ffmpeg.setStyleSheet("" if ff else "color:#E5484D;")

    def mostrar_hardware(self, hw: dict):
        g = hw["gpu"]
        if g:
            self.l_gpu.setText(f"{g['nome']} · {g['vram_mb']} MB · driver {g['driver']} · perfil: {hw['perfil']}"
                               + (" · NVENC disponível" if hw["nvenc"] else " · NVENC indisponível"))
        else:
            self.l_gpu.setText("Nenhuma GPU NVIDIA detectada (ou driver desatualizado). O programa usará o processador.")
        self.vram = g.get("vram_mb", 0) if g else 0
        r = recomendado_para(self.vram)
        self.recomendado.setText(f"{r['modelo']} — {r['nota']}")
        if not self.modelo.currentText().strip():
            self.modelo.setCurrentText(r["modelo"])

    # ---------- IA ----------
    def _url(self) -> str:
        return self.ollama_url.text().strip()

    def _modelo_escolhido(self) -> str:
        return self.modelo.currentText().strip() or recomendado_para(self.vram)["modelo"]

    def verificar_ia(self) -> bool:
        o = Ollama(self._url())
        rec = recomendado_para(self.vram)["modelo"]
        try:
            versao = o.versao()
            instalados = o.modelos()
        except OllamaErro:
            self.ia_status.setText("❌ Ollama não encontrado. Instale em ollama.com (gratuito) e deixe-o aberto.")
            self.ia_status.setStyleSheet("color:#E5484D;")
            return False
        atual = self._modelo_escolhido()
        self.modelo.blockSignals(True)
        self.modelo.clear()
        self.modelo.addItems(instalados or [])
        self.modelo.setCurrentText(atual if (atual in instalados or not instalados) else (rec if rec in instalados else instalados[0]))
        self.modelo.blockSignals(False)
        self.db.set("ollama_modelo", self.modelo.currentText().strip())
        ok = self.modelo.currentText() in instalados
        self.ia_status.setText(f"✓ Ollama {versao} funcionando · {len(instalados)} modelo(s) instalado(s)"
                               + ("" if ok else f" · o modelo “{self.modelo.currentText()}” ainda não foi baixado"))
        self.ia_status.setStyleSheet("color:#3DD68C;" if ok else "color:#F2A93B;")
        self.btn_baixar.setEnabled(rec not in instalados)
        self.btn_baixar.setText("Baixar modelo recomendado" if rec not in instalados else "Modelo recomendado instalado ✓")
        return True

    def baixar_modelo(self):
        rec = recomendado_para(self.vram)["modelo"]
        if not Ollama(self._url()).disponivel():
            self.verificar_ia()
            return
        dlg = ProgressoDialog(f"Baixando {rec}", self)
        w = PullWorker(self._url(), rec)
        self._pull = w
        dlg.btn.clicked.connect(lambda: (w.cancelar(), dlg.cancelando()))
        w.progresso.connect(lambda st, f: dlg.atualizar(f"Baixando {rec}", f"{st} ({f * 100:.0f}%)" if f else st, f))
        w.falhou.connect(lambda m: QMessageBox.warning(self, "Download do modelo", m))
        w.concluido.connect(lambda: (self.modelo.setCurrentText(rec), self.db.set("ollama_modelo", rec)))
        w.finished.connect(dlg.accept)
        w.finished.connect(self.verificar_ia)
        w.start()
        dlg.exec()

    def _mudar_modo(self, i: int):
        if i == 1:
            r = QMessageBox.question(self, "Usar Claude",
                                     "Ao usar o Claude, a transcrição dos vídeos e os dados dos trechos são enviados "
                                     "para a Anthropic (os vídeos não). Confirmar?")
            if r != QMessageBox.Yes:
                self.modo.blockSignals(True); self.modo.setCurrentIndex(0); self.modo.blockSignals(False)
                return
        self.db.set("ia_modo", "claude" if i == 1 else "local")

    def testar_claude(self) -> bool:
        self.db.set("claude_chave", self.claude_chave.text().strip())
        self.db.set("claude_modelo", self.claude_modelo.text().strip())
        try:
            Claude(self.claude_chave.text(), self.claude_modelo.text()).testar()
            self.claude_status.setText("✓ Claude respondeu corretamente.")
            self.claude_status.setStyleSheet("color:#3DD68C;")
            return True
        except ClaudeErro as e:
            self.claude_status.setText(f"❌ {e}")
            self.claude_status.setStyleSheet("color:#E5484D;")
            return False

    def _atualizar_cuda(self):
        import os
        from .. import cuda_libs
        if os.name != "nt":
            self.cuda_status.setText("Não se aplica neste sistema.")
            self.btn_cuda.setEnabled(False)
        elif cuda_libs.instaladas():
            self.cuda_status.setText("✓ Ativada — a transcrição usa a placa NVIDIA (bem mais rápida).")
            self.btn_cuda.setEnabled(False)
        else:
            self.cuda_status.setText("Desativada — a transcrição usa o processador. Ative para usar a RTX "
                                     "(precisa de internet só uma vez).")

    def baixar_cuda(self):
        from .. import cuda_libs
        from PySide6.QtCore import QThread, Signal as _Sig

        class _W(QThread):
            prog = _Sig(str, float); erro = _Sig(str)
            def __init__(s): super().__init__(); s.c = False
            def run(s):
                try:
                    cuda_libs.baixar(s.prog.emit, lambda: s.c)
                except Exception as e:  # noqa: BLE001
                    s.erro.emit(str(e))

        dlg = ProgressoDialog("Bibliotecas da placa de vídeo", self)
        w = _W(); self._cuda_w = w
        dlg.btn.clicked.connect(lambda: (setattr(w, "c", True), dlg.cancelando()))
        w.prog.connect(lambda m, f: dlg.atualizar("Bibliotecas da placa de vídeo", m, f))
        w.erro.connect(lambda m: QMessageBox.warning(self, "Placa de vídeo", m))
        w.finished.connect(dlg.accept)
        w.finished.connect(self._atualizar_cuda)
        w.start()
        dlg.exec()

    def _escolher_saida(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta sugerida para exportar", self.saida.text())
        if d:
            self.saida.setText(d)
            self.db.set("pasta_saida", d)

    def _restaurar(self):
        if QMessageBox.question(self, "Restaurar padrões",
                                "Restaurar os padrões originais do programa? Alterações feitas neles serão perdidas.") == QMessageBox.Yes:
            self.db.restaurar_padroes_embutidos()
            self.padroes_restaurados.emit()
            QMessageBox.information(self, "Restaurar padrões", "Padrões originais restaurados.")
