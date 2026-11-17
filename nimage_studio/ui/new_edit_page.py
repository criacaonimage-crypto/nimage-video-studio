"""Nova edição: importar vídeos e configurar padrão, objetivo, formato, tom e instruções."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit,
                               QMessageBox, QPlainTextEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import media
from ..db import Database
from ..patterns import FORMATOS, OPCOES, hash_padrao
from .widgets import VideoCard, botao, card, label, ler_video_async

PERSONALIZADO = "Personalizado…"
DO_PADRAO = "Formato do padrão"


class DropZone(QFrame):
    arquivos = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        t = label("Arraste vídeos para cá", "H2")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label("MP4, MOV, MKV, AVI, MTS… Os arquivos originais nunca são alterados.", "Muted")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        b = botao("Adicionar vídeos", "Primary", self._escolher)
        b.setFixedWidth(180)
        lay.addWidget(b, 0, Qt.AlignCenter)

    def _hover(self, on: bool):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self); self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        caminhos = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        # Pastas arrastadas: pega os vídeos de dentro.
        lista = []
        for c in caminhos:
            p = Path(c)
            if p.is_dir():
                lista += sorted(str(x) for x in p.iterdir() if media.eh_video(x))
            else:
                lista.append(c)
        self.arquivos.emit(lista)

    def _escolher(self):
        exts = " ".join(f"*{e}" for e in sorted(media.EXTENSOES_VIDEO))
        arqs, _ = QFileDialog.getOpenFileNames(self, "Importar vídeos", "", f"Vídeos ({exts});;Todos (*.*)")
        if arqs:
            self.arquivos.emit(arqs)


class NewEditPage(QWidget):
    abrir_previa = Signal(str)       # caminho do vídeo a mostrar
    projeto_salvo = Signal(int)
    pedir_plano = Signal()

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setObjectName("Page")
        self.projeto_id: int | None = None
        self.videos: dict[str, dict | None] = {}   # caminho -> info (None enquanto lê)
        self.cards: dict[str, VideoCard] = {}
        self._jobs = set()
        self.snapshot_padrao: dict | None = None
        self._carregando = False
        self.plano: dict | None = None
        self.transcricoes: list[dict] = []
        self.render: dict | None = None
        self.historico: list[dict] = []

        raiz = QHBoxLayout(self)
        raiz.setContentsMargins(28, 24, 28, 24)
        raiz.setSpacing(24)

        # ----- coluna esquerda: vídeos -----
        esq = QVBoxLayout()
        esq.addWidget(label("Nova edição", "H1"))
        esq.addWidget(label("1. Importe os vídeos", "H2"))
        self.drop = DropZone()
        self.drop.arquivos.connect(self.adicionar_videos)
        esq.addWidget(self.drop)
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(8)
        self.cards_box.addStretch()
        cont = QWidget(); cont.setLayout(self.cards_box)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(cont)
        esq.addWidget(scroll, 1)
        self.resumo = label("Nenhum vídeo importado.", "Muted")
        esq.addWidget(self.resumo)
        raiz.addLayout(esq, 5)

        # ----- coluna direita: configuração -----
        dir_ = QVBoxLayout()
        dir_.addWidget(label("2. Configure a edição", "H2"))
        box = card()
        f = QFormLayout(box)
        f.setContentsMargins(18, 18, 18, 18)
        f.setVerticalSpacing(10)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.nome = QLineEdit(); self.nome.setPlaceholderText("Ex.: Corte palestra Ben – Kick-off GWM")
        self.padrao = QComboBox(); self.padrao.currentIndexChanged.connect(self._ao_trocar_padrao)
        self.aviso_snapshot = label("", "Muted", wrap=True)
        self.objetivo = QLineEdit(); self.objetivo.setPlaceholderText("Ex.: Reels de divulgação do Grande Dia")
        self.duracao = QSpinBox(); self.duracao.setRange(5, 4 * 3600); self.duracao.setSuffix(" s")
        self.formato = QComboBox()
        self.formato.addItems([DO_PADRAO, *FORMATOS.keys(), PERSONALIZADO])
        self.formato.currentTextChanged.connect(self._ao_trocar_formato)
        self.larg = QSpinBox(); self.larg.setRange(64, 7680); self.larg.setSingleStep(2)
        self.alt = QSpinBox(); self.alt.setRange(64, 7680); self.alt.setSingleStep(2)
        fmt = QHBoxLayout(); fmt.addWidget(self.larg); fmt.addWidget(label("×")); fmt.addWidget(self.alt); fmt.addStretch()
        self.intensidade = QComboBox(); self.intensidade.addItems(OPCOES["intensidade"])
        self.ritmo = QComboBox(); self.ritmo.addItems(OPCOES["ritmo"])
        self.musica = QComboBox(); self.musica.addItems(OPCOES["tom_musica"])
        self.legenda = QComboBox(); self.legenda.addItems(OPCOES["estilo_legenda"])

        f.addRow("Nome do projeto", self.nome)
        f.addRow("Padrão", self.padrao)
        f.addRow("", self.aviso_snapshot)
        f.addRow("Objetivo do vídeo", self.objetivo)
        f.addRow("Duração desejada", self.duracao)
        f.addRow("Formato de saída", self.formato)
        f.addRow("Tamanho (px)", fmt)
        f.addRow("Tom da edição", self.intensidade)
        f.addRow("Ritmo", self.ritmo)
        f.addRow("Música", self.musica)
        f.addRow("Legenda", self.legenda)
        dir_.addWidget(box)

        dir_.addWidget(label("3. Explique como você quer o resultado", "H2"))
        self.instrucoes = QPlainTextEdit()
        self.instrucoes.setPlaceholderText(
            "Escreva livremente. Ex.: “Quero um Reels de 45 segundos com os melhores momentos da fala do Ben "
            "sobre liderança. Comece com a frase mais forte, legenda com palavras-chave em destaque, "
            "música enérgica baixa e vinheta da Arena no final.”")
        self.instrucoes.setMinimumHeight(140)
        dir_.addWidget(self.instrucoes, 1)

        acoes = QHBoxLayout()
        acoes.addWidget(botao("Salvar projeto", None, self.salvar))
        acoes.addStretch()
        self.btn_ia = botao("Analisar e planejar com IA", "Primary", self._pedir_plano)
        self.btn_ia.setEnabled(False)
        self.btn_ia.setToolTip("Transcreve as falas, identifica as cenas e pede à IA local um plano de edição.")
        acoes.addWidget(self.btn_ia)
        dir_.addLayout(acoes)
        dir_.addWidget(label("A IA transcreve, identifica as cenas e monta o plano. Depois você confere, "
                             "ajusta e gera o vídeo na tela Edição e prévia.", "Muted", wrap=True))
        raiz.addLayout(dir_, 4)

        self.recarregar_padroes()
        self._ao_trocar_formato(DO_PADRAO)

    # ---------- padrões ----------
    def recarregar_padroes(self):
        atual = self.padrao.currentData()
        self._carregando = True
        self.padrao.clear()
        for p in self.db.listar_padroes():
            self.padrao.addItem(p["dados"]["nome"], p["id"])
        i = self.padrao.findData(atual)
        self.padrao.setCurrentIndex(i if i >= 0 else 0)
        self._carregando = False
        if atual is None or i < 0:
            self._ao_trocar_padrao()
        else:
            self._atualizar_aviso_snapshot()

    def _ao_trocar_padrao(self, *_):
        if self._carregando:
            return
        pid = self.padrao.currentData()
        p = self.db.obter_padrao(pid) if pid is not None else None
        if not p:
            return
        self.snapshot_padrao = copy.deepcopy(p)
        self.duracao.setValue(int(p["edicao"]["duracao_alvo_seg"]))
        self.intensidade.setCurrentText(p["edicao"]["intensidade"])
        self.ritmo.setCurrentText(p["edicao"]["ritmo"])
        self.musica.setCurrentText(p["audio"]["tom_musica"])
        self.legenda.setCurrentText(p["identidade"]["legenda"]["estilo"])
        self.formato.setCurrentText(DO_PADRAO)
        self._ao_trocar_formato(DO_PADRAO)
        self._atualizar_aviso_snapshot()

    def _atualizar_aviso_snapshot(self):
        pid = self.padrao.currentData()
        atual = self.db.obter_padrao(pid) if pid is not None else None
        if self.snapshot_padrao and atual and hash_padrao(atual) != hash_padrao(self.snapshot_padrao):
            self.aviso_snapshot.setText("Este projeto usa a versão do padrão de quando foi criado. "
                                        "Escolha o padrão de novo para usar a versão atual.")
        else:
            self.aviso_snapshot.setText("")
        self.aviso_snapshot.setVisible(bool(self.aviso_snapshot.text()))

    def _ao_trocar_formato(self, texto: str):
        livre = texto == PERSONALIZADO
        self.larg.setEnabled(livre); self.alt.setEnabled(livre)
        if texto == DO_PADRAO and self.snapshot_padrao:
            fp = self.snapshot_padrao["formato_padrao"]
            self.larg.setValue(fp["largura"]); self.alt.setValue(fp["altura"])
        elif texto in FORMATOS:
            w, h = FORMATOS[texto]
            self.larg.setValue(w); self.alt.setValue(h)

    # ---------- vídeos ----------
    def adicionar_videos(self, caminhos: list[str]):
        ignorados = []
        for c in caminhos:
            if c in self.videos:
                continue
            if not media.eh_video(c):
                ignorados.append(Path(c).name)
                continue
            self.videos[c] = None
            vc = VideoCard(c)
            vc.remover.connect(self.remover_video)
            vc.selecionado.connect(self.abrir_previa.emit)
            self.cards[c] = vc
            self.cards_box.insertWidget(self.cards_box.count() - 1, vc)
            self._jobs.add(ler_video_async(c, self._video_pronto, self._video_erro))
        if ignorados:
            QMessageBox.information(self, "Arquivos ignorados", "Não são vídeos:\n" + "\n".join(ignorados))
        self._atualizar_resumo()

    def _video_pronto(self, caminho: str, info: dict, thumb: str):
        if caminho in self.videos:
            info["miniatura"] = thumb
            self.videos[caminho] = info
            self.cards[caminho].preencher(info, thumb)
            if not self.nome.text().strip():
                self.nome.setText(Path(caminho).stem)
        self._atualizar_resumo()

    def _video_erro(self, caminho: str, msg: str):
        if caminho in self.cards:
            self.cards[caminho].falhou(msg)
            self.videos[caminho] = {"erro": msg, "caminho": caminho}
        self._atualizar_resumo()

    def remover_video(self, caminho: str):
        self.videos.pop(caminho, None)
        vc = self.cards.pop(caminho, None)
        if vc:
            vc.setParent(None); vc.deleteLater()
        self._atualizar_resumo()

    def _atualizar_resumo(self):
        ok = [v for v in self.videos.values() if v and "erro" not in v]
        if not self.videos:
            self.resumo.setText("Nenhum vídeo importado.")
            self.btn_ia.setEnabled(False)
            return
        total = sum(v["duracao"] for v in ok)
        lendo = sum(1 for v in self.videos.values() if v is None)
        txt = f"{len(ok)} vídeo(s) · {media.formatar_duracao(total)} no total"
        if lendo:
            txt += f" · lendo {lendo}…"
        self.resumo.setText(txt)
        self.btn_ia.setEnabled(bool(ok))

    def _pedir_plano(self):
        if any(v is None for v in self.videos.values()):
            QMessageBox.information(self, "Aguarde", "Ainda estou lendo os vídeos. Tente de novo em instantes.")
            return
        self.pedir_plano.emit()

    def videos_validos(self) -> list[dict]:
        return [v for v in self.videos.values() if v and "erro" not in v]

    # ---------- projeto ----------
    def coletar(self) -> dict:
        return {
            "videos": self.videos_validos(),
            "padrao_id": self.padrao.currentData(),
            "padrao": self.snapshot_padrao,
            "objetivo": self.objetivo.text().strip(),
            "duracao_seg": self.duracao.value(),
            "formato": {"nome": self.formato.currentText(), "largura": self.larg.value(), "altura": self.alt.value()},
            "ajustes": {"intensidade": self.intensidade.currentText(), "ritmo": self.ritmo.currentText(),
                        "tom_musica": self.musica.currentText(), "estilo_legenda": self.legenda.currentText()},
            "instrucoes": self.instrucoes.toPlainText().strip(),
            "status": ("editado" if self.render else "planejado") if self.plano else "rascunho",
            "plano": self.plano,
            "transcricoes": self.transcricoes,
            "render": self.render,
            "historico": self.historico,
        }

    def salvar(self) -> int | None:
        if any(v is None for v in self.videos.values()):
            QMessageBox.information(self, "Aguarde", "Ainda estou lendo os vídeos. Tente de novo em instantes.")
            return None
        nome = self.nome.text().strip() or "Projeto sem nome"
        self.projeto_id = self.db.salvar_projeto(nome, self.coletar(), self.projeto_id)
        self.projeto_salvo.emit(self.projeto_id)
        return self.projeto_id

    def limpar(self):
        for c in list(self.cards):
            self.remover_video(c)
        self.projeto_id = None
        self.nome.clear(); self.objetivo.clear(); self.instrucoes.clear()
        self.snapshot_padrao = None
        self.plano, self.transcricoes, self.render, self.historico = None, [], None, []
        self.padrao.setCurrentIndex(0)
        self._ao_trocar_padrao()

    def carregar_projeto(self, pid: int):
        proj = self.db.obter_projeto(pid)
        if not proj:
            return
        self.limpar()
        d = proj["dados"]
        self.projeto_id = pid
        self.nome.setText(proj["nome"])
        self._carregando = True
        i = self.padrao.findData(d.get("padrao_id"))
        if i >= 0:
            self.padrao.setCurrentIndex(i)
        self._carregando = False
        self.snapshot_padrao = d.get("padrao") or self.db.obter_padrao(d.get("padrao_id"))
        self.objetivo.setText(d.get("objetivo", ""))
        self.duracao.setValue(int(d.get("duracao_seg", 60)))
        fmt = d.get("formato", {})
        self.formato.setCurrentText(fmt.get("nome", DO_PADRAO))
        self.larg.setValue(fmt.get("largura", 1920)); self.alt.setValue(fmt.get("altura", 1080))
        aj = d.get("ajustes", {})
        self.intensidade.setCurrentText(aj.get("intensidade", "Equilibrada"))
        self.ritmo.setCurrentText(aj.get("ritmo", "Moderado"))
        self.musica.setCurrentText(aj.get("tom_musica", "Inspiradora"))
        self.legenda.setCurrentText(aj.get("estilo_legenda", "Simples"))
        self.instrucoes.setPlainText(d.get("instrucoes", ""))
        self.plano = d.get("plano")
        self.transcricoes = d.get("transcricoes") or []
        self.render = d.get("render")
        self.historico = d.get("historico") or []
        faltando = [v["caminho"] for v in d.get("videos", []) if not Path(v["caminho"]).exists()]
        self.adicionar_videos([v["caminho"] for v in d.get("videos", []) if Path(v["caminho"]).exists()])
        self._atualizar_aviso_snapshot()
        if faltando:
            QMessageBox.warning(self, "Vídeos não encontrados",
                                "Estes vídeos não foram encontrados (HD externo desconectado ou arquivo movido):\n\n"
                                + "\n".join(faltando) + "\n\nConecte o disco e abra o projeto de novo, "
                                "ou importe os arquivos novamente.")
