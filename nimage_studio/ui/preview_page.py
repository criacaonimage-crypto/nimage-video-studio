"""Edição e pré-visualização: player, plano de edição (com prévia sequencial dos trechos) e transcrição."""
from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSlider,
                               QStackedWidget, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import media
from .widgets import botao, label


def _mmss(s: float) -> str:
    return media.formatar_duracao(s)


class PreviewPage(QWidget):
    plano_alterado = Signal(dict)   # usuário removeu/reordenou trechos ou aprovou
    refazer_plano = Signal()
    gerar_video = Signal()
    exportar = Signal()
    pedir_ajuste = Signal(str)
    desfazer_ajuste = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.plano: dict | None = None
        self.resultado: str | None = None
        self._fila: list[dict] = []     # trechos em reprodução sequencial
        self._idx = -1
        self._busca_pendente: float | None = None

        raiz = QHBoxLayout(self)
        raiz.setContentsMargins(28, 24, 28, 24)
        raiz.setSpacing(20)

        # ----- player -----
        centro = QVBoxLayout()
        centro.addWidget(label("Edição e pré-visualização", "H1"))
        self.titulo = label("Selecione um vídeo", "Muted")
        centro.addWidget(self.titulo)
        self.video = QVideoWidget()
        self.video.setMinimumSize(480, 270)
        self.video.setStyleSheet("background:#000; border-radius:12px;")
        self.tela_reserva = QLabel()
        self.tela_reserva.setAlignment(Qt.AlignCenter)
        self.tela_reserva.setMinimumSize(480, 270)
        self.tela_reserva.setStyleSheet("background:#000; border-radius:12px;")
        self.pilha_video = QStackedWidget()
        self.pilha_video.addWidget(self.video)
        self.pilha_video.addWidget(self.tela_reserva)
        centro.addWidget(self.pilha_video, 1)
        self.modo_reserva = False

        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self._ligar_player()

        ctrl = QHBoxLayout()
        self.btn_play = botao("▶  Reproduzir", "Primary", self.alternar)
        self.btn_play.setFixedWidth(140)
        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(botao("−5 s", None, lambda: self.player.setPosition(max(0, self.player.position() - 5000))))
        ctrl.addWidget(botao("+5 s", None, lambda: self.player.setPosition(self.player.position() + 5000)))
        self.barra = QSlider(Qt.Horizontal)
        self.barra.sliderMoved.connect(self.player.setPosition)
        ctrl.addWidget(self.barra, 1)
        self.tempo = label("0:00 / 0:00", "Muted")
        ctrl.addWidget(self.tempo)
        ctrl.addWidget(label("🔊"))
        self.vol = QSlider(Qt.Horizontal); self.vol.setRange(0, 100); self.vol.setValue(80); self.vol.setFixedWidth(90)
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)
        ctrl.addWidget(self.vol)
        centro.addLayout(ctrl)
        if os.environ.get("NIMAGE_PLAYER") == "reserva":
            self._usar_reserva()

        # resultado editado
        self.res_status = label("Nenhum vídeo gerado ainda.", "Muted", wrap=True)
        centro.addWidget(self.res_status)
        res = QHBoxLayout()
        self.b_ver_res = botao("▶  Assistir resultado", None, self.tocar_resultado)
        self.b_gerar = botao("Gerar vídeo", "Primary", self.gerar_video.emit)
        self.b_exportar = botao("Exportar MP4 (Salvar como…)", None, self.exportar.emit)
        for b in (self.b_gerar, self.b_ver_res, self.b_exportar):
            res.addWidget(b)
        res.addStretch()
        centro.addLayout(res)

        # ajuste em linguagem natural
        aj = QHBoxLayout()
        self.ajuste = QLineEdit()
        self.ajuste.setPlaceholderText("Peça um ajuste: “Deixe o começo mais impactante”, “Abaixe a música”, "
                                       "“Retire o trecho T3”, “Faça uma versão mais curta”…")
        self.ajuste.returnPressed.connect(self._enviar_ajuste)
        aj.addWidget(self.ajuste, 1)
        self.b_ajuste = botao("Aplicar ajuste", "Primary", self._enviar_ajuste)
        self.b_desfazer = botao("Desfazer", None, self.desfazer_ajuste.emit)
        aj.addWidget(self.b_ajuste); aj.addWidget(self.b_desfazer)
        centro.addLayout(aj)
        self.ultimo_ajuste = label("", "Muted", wrap=True)
        centro.addWidget(self.ultimo_ajuste)
        raiz.addLayout(centro, 3)

        # ----- lateral -----
        self.abas = QTabWidget()
        self.abas.setMinimumWidth(400)
        self.abas.addTab(self._aba_plano(), "Plano de edição")
        self.lista = QListWidget()
        self.lista.currentItemChanged.connect(lambda it, _: it and self.abrir(it.data(Qt.UserRole)))
        self.abas.addTab(self.lista, "Vídeos originais")
        self.transcricao = QPlainTextEdit(); self.transcricao.setReadOnly(True)
        self.abas.addTab(self.transcricao, "Transcrição")
        raiz.addWidget(self.abas, 2)
        self.mostrar_plano(None)

    # ---------- player ----------
    def _ligar_player(self):
        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(self._dur)
        self.player.playbackStateChanged.connect(self._estado)
        self.player.mediaStatusChanged.connect(self._status_midia)
        self.player.errorOccurred.connect(self._erro_player)

    def _erro_player(self, *_):
        if not self.modo_reserva:
            fonte = self.player.source()
            self._usar_reserva()
            if not fonte.isEmpty():
                self.player.setSource(fonte)
            return
        self.titulo.setText(f"Erro ao reproduzir: {self.player.errorString()}")

    def _usar_reserva(self):
        """Troca para o reprodutor de reserva (PyAV, sem som) quando o do Qt não funciona neste PC."""
        from .fallback_player import PlayerReserva
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass
        self.modo_reserva = True
        self.player = PlayerReserva(self.tela_reserva, self)
        self._ligar_player()
        self.pilha_video.setCurrentIndex(1)
        self.vol.setEnabled(False)
        self.vol.setToolTip("Prévia em modo de compatibilidade (sem som). O vídeo exportado tem som normalmente.")
        self.titulo.setText("Prévia em modo de compatibilidade (sem som) — o vídeo final tem som normalmente.")

    # ---------- aba do plano ----------
    def _aba_plano(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        self.p_titulo = label("", "H2", wrap=True)
        self.p_info = label("", "Muted", wrap=True)
        self.p_avisos = label("", "Pending", wrap=True)
        v.addWidget(self.p_titulo); v.addWidget(self.p_info); v.addWidget(self.p_avisos)
        self.clipes = QListWidget()
        self.clipes.setWordWrap(True)
        self.clipes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.clipes.itemClicked.connect(self._tocar_clipe_item)
        v.addWidget(self.clipes, 1)
        edit = QHBoxLayout()
        self.b_sobe = botao("↑", None, lambda: self._mover(-1)); self.b_sobe.setToolTip("Mover trecho para cima")
        self.b_desce = botao("↓", None, lambda: self._mover(1)); self.b_desce.setToolTip("Mover trecho para baixo")
        self.b_remove = botao("Remover trecho", "Danger", self._remover)
        edit.addWidget(self.b_sobe); edit.addWidget(self.b_desce); edit.addWidget(self.b_remove); edit.addStretch()
        v.addLayout(edit)
        self.p_extra = label("", "Muted", wrap=True)
        v.addWidget(self.p_extra)
        acoes = QHBoxLayout()
        self.b_previa = botao("▶  Prévia do plano", "Primary", self.tocar_plano)
        self.b_refazer = botao("Refazer plano", None, self.refazer_plano.emit)
        self.b_aprovar = botao("Aprovar plano", None, self._aprovar)
        acoes.addWidget(self.b_previa); acoes.addWidget(self.b_refazer); acoes.addWidget(self.b_aprovar)
        v.addLayout(acoes)
        v.addWidget(label("“Prévia do plano” toca os trechos direto dos originais, sem legenda nem trilha. "
                          "Para ver o vídeo final, clique em “Gerar vídeo”.", "Muted", wrap=True))
        return w

    def mostrar_plano(self, plano: dict | None):
        self.plano = copy.deepcopy(plano) if plano else None
        tem = bool(self.plano and self.plano.get("clipes"))
        for b in (self.b_previa, self.b_aprovar, self.b_sobe, self.b_desce, self.b_remove, self.b_gerar,
                  self.b_ajuste, self.ajuste):
            b.setEnabled(tem)
        self.b_desfazer.setEnabled(bool(tem and self.plano.get("_pode_desfazer")))
        self.b_refazer.setEnabled(tem)
        self.clipes.clear()
        if not tem:
            self.p_titulo.setText("Nenhum plano ainda")
            self.p_info.setText("Em Nova edição, clique em “Analisar e planejar com IA”.")
            self.p_avisos.setText(""); self.p_extra.setText("")
            return
        p = self.plano
        alvo = p.get("formato", {})
        self.p_titulo.setText(p["titulo"] + ("   ✓ aprovado" if p.get("aprovado") else ""))
        total = sum(c["duracao"] for c in p["clipes"])
        p["duracao_total"] = round(total, 2)
        self.p_info.setText(f"{len(p['clipes'])} trechos · {_mmss(total)} · {alvo.get('largura')}×{alvo.get('altura')} · "
                            f"padrão {p['padrao']} · {p['ia']}"
                            + (f"\n{p['justificativa']}" if p.get("justificativa") else ""))
        self.p_avisos.setText("\n".join(f"⚠ {a}" for a in p.get("avisos", [])))
        self.p_avisos.setVisible(bool(p.get("avisos")))
        for c in p["clipes"]:
            dest = f"\nDestaques: {', '.join(c['destaques'])}" if c.get("destaques") else ""
            it = QListWidgetItem(f"{c['id']} · {c['video_nome']} · {_mmss(c['inicio'])}–{_mmss(c['fim'])} "
                                 f"({c['duracao']:.1f} s)\n“{c['texto'] or '[imagem sem fala]'}”\n"
                                 f"Motivo: {c.get('motivo') or '—'}{dest}")
            it.setData(Qt.UserRole, c["id"])
            self.clipes.addItem(it)
        tr = p["trilha"]
        trilha = ("sem música" if not tr["usar"] else
                  f"{tr['tom'].lower()}, volume {tr['volume']}% · " + (Path(tr["arquivo"]).name if tr["arquivo"] else "sem arquivo"))
        ab, en, lg = p["abertura"], p["encerramento"], p["legendas"]
        self.p_extra.setText(
            f"Abertura: {ab['tipo']}{' · ' + ab['texto'] if ab.get('texto') else ''}\n"
            f"Encerramento: {en['tipo']}{' · ' + Path(en['arquivo']).name if en.get('arquivo') else ''}\n"
            f"Trilha: {trilha}\nTransições: {p['transicao']} · Legenda: {lg['estilo'] if lg['ativar'] else 'sem legenda'}")

    def _renumerar(self):
        for i, c in enumerate(self.plano["clipes"], start=1):
            c["id"] = f"T{i}"
        self.plano["aprovado"] = False

    def _mover(self, d: int):
        r = self.clipes.currentRow()
        cl = self.plano["clipes"]
        if r < 0 or not (0 <= r + d < len(cl)):
            return
        cl[r], cl[r + d] = cl[r + d], cl[r]
        self._renumerar()
        self.mostrar_plano(self.plano); self.clipes.setCurrentRow(r + d)
        self.plano_alterado.emit(self.plano)

    def _remover(self):
        r = self.clipes.currentRow()
        if r < 0 or len(self.plano["clipes"]) <= 1:
            return
        self.plano["clipes"].pop(r)
        self._renumerar()
        self.mostrar_plano(self.plano); self.clipes.setCurrentRow(min(r, self.clipes.count() - 1))
        self.plano_alterado.emit(self.plano)

    def _aprovar(self):
        self.plano["aprovado"] = True
        self.mostrar_plano(self.plano)
        self.plano_alterado.emit(self.plano)

    def _enviar_ajuste(self):
        t = self.ajuste.text().strip()
        if t and self.plano:
            self.pedir_ajuste.emit(t)

    def mostrar_resultado(self, render: dict | None, plano_hash: str | None):
        self.resultado = render.get("arquivo") if render and Path(render.get("arquivo", "")).exists() else None
        if not self.resultado:
            self.res_status.setText("Nenhum vídeo gerado ainda." if self.plano else "Gere um plano primeiro.")
        else:
            desat = render.get("plano_hash") != plano_hash
            txt = (f"Resultado: {media.formatar_duracao(render['duracao'])} · {render['largura']}×{render['altura']} · "
                   f"{render.get('codificador', '')}")
            if desat:
                txt += "  ·  ⚠ o plano mudou depois — clique em “Gerar vídeo” para atualizar"
            if render.get("avisos"):
                txt += "\n" + "\n".join(f"⚠ {a}" for a in render["avisos"])
            self.res_status.setText(txt)
        self.b_ver_res.setEnabled(bool(self.resultado))
        self.b_exportar.setEnabled(bool(self.plano and self.plano.get("clipes")))

    def soltar_arquivo(self, caminho: str):
        """Para o player se ele estiver com este arquivo aberto (no Windows, arquivo aberto não pode ser apagado)."""
        if self.player.source() == QUrl.fromLocalFile(caminho):
            self.player.stop()
            self.player.setSource(QUrl())

    def tocar_resultado(self):
        if self.resultado:
            self._fila, self._idx = [], -1
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(self.resultado))
            self.titulo.setText(f"Resultado editado · {Path(self.resultado).name}")
            self.player.play()

    def mostrar_transcricoes(self, transcricoes: list[dict]):
        partes = []
        for t in transcricoes or []:
            partes.append(f"■ {t['nome']}")
            partes += [f"[{_mmss(s['inicio'])}] {s['texto']}" for s in t.get("segmentos", [])] or ["(sem fala)"]
            partes.append("")
        self.transcricao.setPlainText("\n".join(partes) if partes else "A transcrição aparece aqui depois da análise.")

    # ---------- reprodução de trechos ----------
    def _tocar_clipe_item(self, it):
        c = next((c for c in self.plano["clipes"] if c["id"] == it.data(Qt.UserRole)), None)
        if c:
            self._iniciar_fila([c])

    def tocar_plano(self):
        if self.plano:
            self._iniciar_fila(self.plano["clipes"])

    def _iniciar_fila(self, clipes: list[dict]):
        self._fila, self._idx = list(clipes), -1
        self._proximo()

    def _proximo(self):
        self._idx += 1
        if self._idx >= len(self._fila):
            self.player.pause()
            self._fila, self._idx = [], -1
            return
        c = self._fila[self._idx]
        self.titulo.setText(f"Prévia do plano · {c['id']} de {len(self._fila)} trechos · {c['video_nome']}"
                            if len(self._fila) > 1 else f"{c['id']} · {c['video_nome']}")
        self.clipes.setCurrentRow(next((i for i in range(self.clipes.count())
                                        if self.clipes.item(i).data(Qt.UserRole) == c["id"]), -1))
        url = QUrl.fromLocalFile(c["video"])
        if self.player.source() != url:
            self._busca_pendente = c["inicio"]
            self.player.setSource(url)
        else:
            self.player.setPosition(int(c["inicio"] * 1000))
            self.player.play()

    def _status_midia(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._busca_pendente is not None:
            self.player.setPosition(int(self._busca_pendente * 1000))
            self._busca_pendente = None
            self.player.play()

    # ---------- vídeos originais ----------
    def definir_videos(self, videos: list[dict], abrir: str | None = None):
        self.lista.blockSignals(True)
        self.lista.clear()
        alvo = None
        for v in videos:
            it = QListWidgetItem(f"{v['nome']}\n{_mmss(v['duracao'])} · {v['largura']}×{v['altura']}")
            it.setData(Qt.UserRole, v["caminho"])
            self.lista.addItem(it)
            if v["caminho"] == abrir:
                alvo = it
        self.lista.blockSignals(False)
        if abrir and alvo:
            self.abas.setCurrentIndex(1)
            self.lista.setCurrentItem(alvo)
        elif self.player.source().isEmpty() and self.lista.count():
            self.lista.setCurrentRow(0)
        if not videos:
            self.player.stop()
            self.player.setSource(QUrl())
            self.titulo.setText("Nenhum vídeo no projeto. Importe vídeos em Nova edição.")

    def abrir(self, caminho: str):
        if not caminho:
            return
        self._fila, self._idx = [], -1
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(caminho))
        self.titulo.setText(Path(caminho).name)

    def alternar(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def parar(self):
        self.player.pause()

    def _estado(self, s):
        self.btn_play.setText("❚❚  Pausar" if s == QMediaPlayer.PlayingState else "▶  Reproduzir")

    def _pos(self, ms):
        if not self.barra.isSliderDown():
            self.barra.setValue(ms)
        self.tempo.setText(f"{_mmss(ms / 1000)} / {_mmss(self.player.duration() / 1000)}")
        if self._fila and 0 <= self._idx < len(self._fila) and self._busca_pendente is None:
            if ms / 1000 >= self._fila[self._idx]["fim"]:
                self._proximo()

    def _dur(self, ms):
        self.barra.setRange(0, ms)
