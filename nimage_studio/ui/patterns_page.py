"""Gerenciador de padrões: criar, editar, duplicar, excluir, importar e exportar."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QScrollArea,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..db import Database
from ..patterns import OPCOES, completar, padrao_vazio
from .widgets import ColorButton, LibraryEditor, botao, label, pagina


def _combo(chave: str) -> QComboBox:
    c = QComboBox()
    c.addItems(OPCOES[chave])
    return c


def _set_combo(c: QComboBox, valor: str):
    i = c.findText(valor)
    if i < 0 and c.isEditable():
        c.setEditText(valor)
    else:
        c.setCurrentIndex(max(i, 0))


def _fonte_combo() -> QComboBox:
    c = QComboBox()
    c.setEditable(True)  # a fonte pode ainda não estar instalada neste PC
    c.addItem("")
    c.addItems(QFontDatabase.families())
    return c


def _rolavel(w: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(w)
    return s


class PatternsPage(QWidget):
    padroes_alterados = Signal()

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.atual_id: int | None = None
        self._carregando = False
        self.sujo = False
        self.setObjectName("Page")

        raiz = QHBoxLayout(self)
        raiz.setContentsMargins(28, 24, 28, 24)
        raiz.setSpacing(20)

        # ----- coluna da lista -----
        esq = QVBoxLayout()
        esq.addWidget(label("Padrões", "H1"))
        esq.addWidget(label("Os padrões ficam dentro do programa e podem ser ajustados aqui.", "Muted", wrap=True))
        self.lista = QListWidget()
        self.lista.setMinimumWidth(240)
        self.lista.currentItemChanged.connect(self._ao_trocar)
        esq.addWidget(self.lista, 1)
        linha = QHBoxLayout()
        linha.addWidget(botao("Novo", None, self.novo))
        linha.addWidget(botao("Duplicar", None, self.duplicar))
        linha.addWidget(botao("Excluir", "Danger", self.excluir))
        esq.addLayout(linha)
        linha2 = QHBoxLayout()
        linha2.addWidget(botao("Importar…", None, self.importar))
        linha2.addWidget(botao("Exportar…", None, self.exportar))
        esq.addLayout(linha2)
        b = botao("Exportar todos para o programa…", "Ghost", self.exportar_programa)
        b.setToolTip("Gera o arquivo padroes_embutidos.json. Coloque-o no GitHub para que a próxima "
                     "versão do programa já venha com estes padrões em todos os computadores.")
        esq.addWidget(b)
        raiz.addLayout(esq)

        # ----- editor -----
        dir_ = QVBoxLayout()
        topo = QHBoxLayout()
        self.titulo = label("", "H1")
        topo.addWidget(self.titulo)
        topo.addStretch()
        self.status = label("", "Muted")
        topo.addWidget(self.status)
        self.btn_descartar = botao("Descartar alterações", None, self.descartar)
        self.btn_salvar = botao("Salvar padrão", "Primary", self.salvar)
        topo.addWidget(self.btn_descartar)
        topo.addWidget(self.btn_salvar)
        dir_.addLayout(topo)

        self.abas = QTabWidget()
        self.abas.addTab(self._aba_geral(), "Geral")
        self.abas.addTab(self._aba_identidade(), "Identidade visual")
        self.abas.addTab(self._aba_edicao(), "Estilo de edição")
        self.abas.addTab(self._aba_audio(), "Música e som")
        self.biblioteca = LibraryEditor()
        self.biblioteca.alterado.connect(self._marcar_sujo)
        self.abas.addTab(_rolavel(self.biblioteca), "Biblioteca")
        self.abas.addTab(self._aba_regras(), "Regras")
        dir_.addWidget(self.abas, 1)
        raiz.addLayout(dir_, 1)

        self._ligar_sinais_sujo()
        self.recarregar()

    # ---------- abas ----------
    def _form(self) -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QFormLayout()
        v.addLayout(f)
        v.addStretch()
        f.setContentsMargins(18, 18, 18, 18)
        f.setVerticalSpacing(12)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return w, f

    def _aba_geral(self):
        w, f = self._form()
        self.f_nome = QLineEdit()
        self.f_desc = QPlainTextEdit()
        self.f_desc.setMaximumHeight(90)
        self.f_larg = QSpinBox(); self.f_larg.setRange(64, 7680); self.f_larg.setSingleStep(2)
        self.f_alt = QSpinBox(); self.f_alt.setRange(64, 7680); self.f_alt.setSingleStep(2)
        fmt = QHBoxLayout(); fmt.addWidget(self.f_larg); fmt.addWidget(label("×")); fmt.addWidget(self.f_alt); fmt.addStretch()
        f.addRow("Nome", self.f_nome)
        f.addRow("Descrição", self.f_desc)
        f.addRow("Formato padrão (px)", fmt)
        return w

    def _aba_identidade(self):
        w, f = self._form()
        self.f_fonte_titulo = _fonte_combo()
        self.f_fonte_texto = _fonte_combo()
        self.f_cores = {k: ColorButton() for k in ("principal", "secundaria", "fundo", "texto", "destaque")}
        nomes = {"principal": "Cor principal", "secundaria": "Cor secundária", "fundo": "Cor de fundo",
                 "texto": "Cor do texto", "destaque": "Cor de destaque"}
        self.f_tam_titulo = QSpinBox(); self.f_tam_titulo.setRange(10, 400)
        self.f_elementos = QPlainTextEdit(); self.f_elementos.setMaximumHeight(80)
        self.f_leg_estilo = _combo("estilo_legenda")
        self.f_leg_pos = _combo("posicao_legenda")
        self.f_leg_tam = QSpinBox(); self.f_leg_tam.setRange(10, 300)
        self.f_leg_cor = ColorButton()
        self.f_leg_dest = ColorButton()
        self.f_leg_caixa = QCheckBox("Palavras em destaque em CAIXA ALTA")
        f.addRow("Fonte dos títulos", self.f_fonte_titulo)
        f.addRow("Fonte dos textos", self.f_fonte_texto)
        for k, b in self.f_cores.items():
            f.addRow(nomes[k], b)
        f.addRow("Tamanho dos títulos", self.f_tam_titulo)
        f.addRow("Elementos gráficos", self.f_elementos)
        f.addRow(label("Legendas", "H2"))
        f.addRow("Estilo", self.f_leg_estilo)
        f.addRow("Posição", self.f_leg_pos)
        f.addRow("Tamanho", self.f_leg_tam)
        f.addRow("Cor", self.f_leg_cor)
        f.addRow("Cor do destaque", self.f_leg_dest)
        f.addRow("", self.f_leg_caixa)
        return _rolavel(w)

    def _aba_edicao(self):
        w, f = self._form()
        self.f_ritmo = _combo("ritmo")
        self.f_cortes = _combo("cortes")
        self.f_duracao = QSpinBox(); self.f_duracao.setRange(5, 4 * 3600); self.f_duracao.setSuffix(" s")
        self.f_trans = _combo("transicoes")
        self.f_abertura = _combo("abertura")
        self.f_encerra = _combo("encerramento")
        self.f_intens = _combo("intensidade")
        f.addRow("Ritmo", self.f_ritmo)
        f.addRow("Quantidade de cortes", self.f_cortes)
        f.addRow("Duração desejada", self.f_duracao)
        f.addRow("Transições", self.f_trans)
        f.addRow("Abertura", self.f_abertura)
        f.addRow("Encerramento", self.f_encerra)
        f.addRow("Intensidade / emoção", self.f_intens)
        return w

    def _aba_audio(self):
        w, f = self._form()
        self.f_tom_musica = _combo("tom_musica")
        self.f_vol = QSpinBox(); self.f_vol.setRange(0, 100); self.f_vol.setSuffix(" %")
        self.f_duck = QCheckBox("Abaixar a música automaticamente quando houver fala")
        self.f_sfx = QCheckBox("Usar efeitos sonoros (whoosh, impacto…) nas transições")
        f.addRow("Tom da música", self.f_tom_musica)
        f.addRow("Volume da música", self.f_vol)
        f.addRow("", self.f_duck)
        f.addRow("", self.f_sfx)
        return w

    def _aba_regras(self):
        w, f = self._form()
        self.f_regras = {}
        for k, t in (("obrigatorios", "Elementos obrigatórios"), ("preferencias", "Preferências"),
                     ("opcionais", "Elementos opcionais"), ("restricoes", "Restrições")):
            e = QPlainTextEdit(); e.setMaximumHeight(80)
            self.f_regras[k] = e
            f.addRow(t, e)
        self.f_exemplos = QListWidget(); self.f_exemplos.setMaximumHeight(110)
        lin = QHBoxLayout()
        lin.addWidget(botao("Adicionar vídeo aprovado", None, self._add_exemplo))
        lin.addWidget(botao("Remover", "Danger", self._rem_exemplo))
        lin.addStretch()
        box = QVBoxLayout(); box.addWidget(self.f_exemplos); box.addLayout(lin)
        f.addRow("Exemplos aprovados", box)
        return _rolavel(w)

    def _add_exemplo(self):
        arqs, _ = QFileDialog.getOpenFileNames(self, "Vídeos aprovados", "", "Vídeos (*.mp4 *.mov *.mkv *.avi *.m4v)")
        for a in arqs:
            self.f_exemplos.addItem(a)
        if arqs:
            self._marcar_sujo()

    def _rem_exemplo(self):
        for it in self.f_exemplos.selectedItems():
            self.f_exemplos.takeItem(self.f_exemplos.row(it))
        self._marcar_sujo()

    # ---------- estado sujo ----------
    def _ligar_sinais_sujo(self):
        for w in self.findChildren(QLineEdit):
            w.textEdited.connect(self._marcar_sujo)
        for w in self.findChildren(QPlainTextEdit):
            w.textChanged.connect(self._marcar_sujo)
        for w in self.findChildren(QComboBox):
            w.currentTextChanged.connect(self._marcar_sujo)
        for w in self.findChildren(QSpinBox):
            w.valueChanged.connect(self._marcar_sujo)
        for w in self.findChildren(QCheckBox):
            w.toggled.connect(self._marcar_sujo)
        for w in self.findChildren(ColorButton):
            w.clicked.connect(lambda: self._marcar_sujo())

    def _marcar_sujo(self, *_):
        if self._carregando:
            return
        self.sujo = True
        self.status.setText("Alterações não salvas")
        self.btn_salvar.setEnabled(True)
        self.btn_descartar.setEnabled(True)

    def _limpar_sujo(self):
        self.sujo = False
        self.status.setText("")
        self.btn_salvar.setEnabled(False)
        self.btn_descartar.setEnabled(False)

    def confirmar_saida(self) -> bool:
        """Pergunta o que fazer se houver alterações não salvas. True = pode sair."""
        if not self.sujo:
            return True
        r = QMessageBox.question(self, "Alterações não salvas", "Salvar as alterações deste padrão?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.salvar()
        if r == QMessageBox.Discard:
            self.descartar()
            return True
        return False

    # ---------- carregar / coletar ----------
    def recarregar(self, selecionar: int | None = None):
        self._carregando = True
        sel = selecionar or self.atual_id
        self.lista.clear()
        alvo = None
        for p in self.db.listar_padroes():
            it = QListWidgetItem(p["dados"]["nome"] + ("" if p["embutido"] else "   ·  personalizado"))
            it.setData(Qt.UserRole, p["id"])
            self.lista.addItem(it)
            if p["id"] == sel:
                alvo = it
        self._carregando = False
        if self.lista.count():
            self.lista.setCurrentItem(alvo or self.lista.item(0))
        else:
            self.atual_id = None
            self.titulo.setText("Nenhum padrão")
        self.padroes_alterados.emit()

    def _ao_trocar(self, atual, anterior):
        if self._carregando or atual is None:
            return
        novo_id = atual.data(Qt.UserRole)
        if novo_id == self.atual_id:
            return
        if not self.confirmar_saida():
            self._carregando = True
            self.lista.setCurrentItem(anterior)
            self._carregando = False
            return
        self.atual_id = novo_id
        self._preencher(self.db.obter_padrao(novo_id))

    def _preencher(self, p: dict):
        self._carregando = True
        p = completar(p)
        self.titulo.setText(p["nome"])
        self.f_nome.setText(p["nome"])
        self.f_desc.setPlainText(p["descricao"])
        self.f_larg.setValue(p["formato_padrao"]["largura"])
        self.f_alt.setValue(p["formato_padrao"]["altura"])
        i = p["identidade"]
        _set_combo(self.f_fonte_titulo, i["fonte_titulo"])
        _set_combo(self.f_fonte_texto, i["fonte_texto"])
        for k, b in self.f_cores.items():
            b.set_cor(i["cores"][k])
        self.f_tam_titulo.setValue(i["tamanho_titulo"])
        self.f_elementos.setPlainText(i["elementos_graficos"])
        lg = i["legenda"]
        _set_combo(self.f_leg_estilo, lg["estilo"]); _set_combo(self.f_leg_pos, lg["posicao"])
        self.f_leg_tam.setValue(lg["tamanho"]); self.f_leg_cor.set_cor(lg["cor"]); self.f_leg_dest.set_cor(lg["cor_destaque"])
        self.f_leg_caixa.setChecked(lg["caixa_alta_destaque"])
        e = p["edicao"]
        _set_combo(self.f_ritmo, e["ritmo"]); _set_combo(self.f_cortes, e["cortes"])
        self.f_duracao.setValue(int(e["duracao_alvo_seg"]))
        _set_combo(self.f_trans, e["transicoes"]); _set_combo(self.f_abertura, e["abertura"])
        _set_combo(self.f_encerra, e["encerramento"]); _set_combo(self.f_intens, e["intensidade"])
        a = p["audio"]
        _set_combo(self.f_tom_musica, a["tom_musica"]); self.f_vol.setValue(a["volume_musica"])
        self.f_duck.setChecked(a["abaixar_musica_na_fala"]); self.f_sfx.setChecked(a["efeitos_sonoros"])
        self.biblioteca.carregar(p["biblioteca"])
        r = p["regras"]
        for k, ed in self.f_regras.items():
            ed.setPlainText(r[k])
        self.f_exemplos.clear()
        self.f_exemplos.addItems(r["exemplos_aprovados"])
        self._carregando = False
        self._limpar_sujo()

    def coletar(self) -> dict:
        p = padrao_vazio()
        p["nome"] = self.f_nome.text().strip() or "Sem nome"
        p["descricao"] = self.f_desc.toPlainText().strip()
        p["formato_padrao"] = {"largura": self.f_larg.value(), "altura": self.f_alt.value()}
        i = p["identidade"]
        i["fonte_titulo"] = self.f_fonte_titulo.currentText().strip()
        i["fonte_texto"] = self.f_fonte_texto.currentText().strip()
        i["cores"] = {k: b.cor() for k, b in self.f_cores.items()}
        i["tamanho_titulo"] = self.f_tam_titulo.value()
        i["elementos_graficos"] = self.f_elementos.toPlainText().strip()
        i["legenda"] = {"estilo": self.f_leg_estilo.currentText(), "posicao": self.f_leg_pos.currentText(),
                        "tamanho": self.f_leg_tam.value(), "cor": self.f_leg_cor.cor(),
                        "cor_destaque": self.f_leg_dest.cor(), "caixa_alta_destaque": self.f_leg_caixa.isChecked()}
        p["edicao"] = {"ritmo": self.f_ritmo.currentText(), "cortes": self.f_cortes.currentText(),
                       "duracao_alvo_seg": self.f_duracao.value(), "transicoes": self.f_trans.currentText(),
                       "abertura": self.f_abertura.currentText(), "encerramento": self.f_encerra.currentText(),
                       "intensidade": self.f_intens.currentText()}
        p["audio"] = {"tom_musica": self.f_tom_musica.currentText(), "volume_musica": self.f_vol.value(),
                      "abaixar_musica_na_fala": self.f_duck.isChecked(), "efeitos_sonoros": self.f_sfx.isChecked()}
        p["biblioteca"] = self.biblioteca.valores()
        p["regras"] = {k: ed.toPlainText().strip() for k, ed in self.f_regras.items()}
        p["regras"]["exemplos_aprovados"] = [self.f_exemplos.item(i).text() for i in range(self.f_exemplos.count())]
        return p

    # ---------- ações ----------
    def salvar(self) -> bool:
        if self.atual_id is None:
            return False
        self.db.salvar_padrao(self.atual_id, self.coletar())
        self._limpar_sujo()
        self.recarregar(self.atual_id)
        self.status.setText("Salvo ✓")
        return True

    def descartar(self):
        if self.atual_id is not None:
            self._preencher(self.db.obter_padrao(self.atual_id))

    def novo(self):
        if not self.confirmar_saida():
            return
        pid = self.db.criar_padrao(padrao_vazio("Novo padrão"))
        self.atual_id = None
        self.recarregar(pid)
        self.abas.setCurrentIndex(0)
        self.f_nome.setFocus()
        self.f_nome.selectAll()

    def duplicar(self):
        if self.atual_id is None or not self.confirmar_saida():
            return
        pid = self.db.duplicar_padrao(self.atual_id)
        self.atual_id = None
        self.recarregar(pid)

    def excluir(self):
        if self.atual_id is None:
            return
        nome = self.db.obter_padrao(self.atual_id)["nome"]
        if QMessageBox.question(self, "Excluir padrão", f"Excluir o padrão “{nome}”?\n\n"
                                "Projetos que já usaram este padrão não são afetados.") != QMessageBox.Yes:
            return
        self.db.excluir_padrao(self.atual_id)
        self.atual_id = None
        self._limpar_sujo()
        self.recarregar()

    def importar(self):
        arq, _ = QFileDialog.getOpenFileName(self, "Importar padrão", "", "Padrão Nimage (*.json)")
        if not arq:
            return
        try:
            with open(arq, encoding="utf-8") as fh:
                dados = json.load(fh)
            itens = [x["padrao"] for x in dados["padroes"]] if "padroes" in dados else [dados]
            ultimo = None
            for d in itens:
                if not isinstance(d, dict) or "nome" not in d:
                    raise ValueError("arquivo não é um padrão do NIMAGE VIDEO STUDIO")
                ultimo = self.db.criar_padrao(d)
            self.atual_id = None
            self.recarregar(ultimo)
            QMessageBox.information(self, "Importar", f"{len(itens)} padrão(ões) importado(s).")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Importar", f"Não foi possível importar: {e}")

    def exportar(self):
        if self.atual_id is None:
            return
        p = self.coletar() if self.sujo else self.db.obter_padrao(self.atual_id)
        arq, _ = QFileDialog.getSaveFileName(self, "Exportar padrão", f"{p['nome']}.json", "Padrão Nimage (*.json)")
        if arq:
            with open(arq, "w", encoding="utf-8") as fh:
                json.dump(p, fh, ensure_ascii=False, indent=2)

    def exportar_programa(self):
        if not self.confirmar_saida():
            return
        arq, _ = QFileDialog.getSaveFileName(self, "Exportar padrões para o programa", "padroes_embutidos.json",
                                             "JSON (*.json)")
        if arq:
            with open(arq, "w", encoding="utf-8") as fh:
                json.dump(self.db.exportar_embutidos_json(), fh, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Exportado",
                                    "Arquivo gerado. Substitua nimage_studio/resources/padroes_embutidos.json "
                                    "no GitHub para que a próxima versão do programa traga estes padrões.")
