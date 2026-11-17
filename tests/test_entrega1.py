"""Testes da 1ª entrega. Rodar com:  python -m pytest -q

Gera vídeos de teste com o próprio FFmpeg, então não depende de arquivos externos.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nimage_studio import config, media  # noqa: E402
from nimage_studio.db import Database  # noqa: E402
from nimage_studio.patterns import completar, hash_padrao  # noqa: E402


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("videos")
    ff = config.ffmpeg_path()
    assert ff, "FFmpeg precisa estar instalado"
    h = d / "horizontal com espaço.mp4"
    v = d / "vertical_sem_audio.mov"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(h)], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=25",
                    "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True)
    txt = d / "nao_e_video.mp4"
    txt.write_text("isto não é vídeo")
    return {"h": h, "v": v, "falso": txt}


@pytest.fixture()
def dados(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMAGE_DATA_DIR", str(tmp_path / "dados"))
    return tmp_path / "dados"


# ---------------- mídia ----------------
def test_probe_horizontal(videos, dados):
    info = media.probe(videos["h"])
    assert (info["largura"], info["altura"]) == (1280, 720)
    assert 3.9 < info["duracao"] < 4.2
    assert info["tem_audio"] and info["formato"] == "MP4" and info["fps"] == 30


def test_probe_vertical_sem_audio(videos, dados):
    info = media.probe(videos["v"])
    assert (info["largura"], info["altura"]) == (720, 1280)
    assert not info["tem_audio"] and info["formato"] == "MOV"


def test_probe_arquivo_invalido(videos, dados):
    with pytest.raises(media.MediaError):
        media.probe(videos["falso"])


def test_miniatura_e_cache(videos, dados):
    t1 = media.miniatura(videos["h"], 4)
    assert Path(t1).exists() and Path(t1).stat().st_size > 1000
    assert media.miniatura(videos["h"], 4) == t1


def test_original_preservado(videos, dados):
    antes = (videos["h"].stat().st_size, videos["h"].stat().st_mtime)
    media.probe(videos["h"]); media.miniatura(videos["h"], 4)
    assert (videos["h"].stat().st_size, videos["h"].stat().st_mtime) == antes


# ---------------- padrões ----------------
NOMES = {"Arena de Vendas®", "Ben-Geder Trindade", "NEXT LEVEL", "Redes Sociais",
         "Palestras e Eventos", "Institucional e Comercial"}


def test_padroes_embutidos_criados(dados):
    db = Database()
    assert {p["dados"]["nome"] for p in db.listar_padroes()} == NOMES


def test_crud_e_persistencia_apos_reiniciar(dados):
    db = Database()
    arena = next(p for p in db.listar_padroes() if p["slug"] == "arena-de-vendas")
    novo = db.duplicar_padrao(arena["id"])
    p = db.obter_padrao(novo); p["nome"] = "Arena GWM"; p["edicao"]["ritmo"] = "Muito rápido"
    db.salvar_padrao(novo, p)
    ben = next(p for p in db.listar_padroes() if p["slug"] == "ben-geder")
    db.excluir_padrao(ben["id"])
    db.conn.close()

    db2 = Database()  # "reiniciar o programa"
    nomes = {p["dados"]["nome"] for p in db2.listar_padroes()}
    assert "Arena GWM" in nomes and "Ben-Geder Trindade" not in nomes  # excluído não volta sozinho
    assert db2.obter_padrao(novo)["edicao"]["ritmo"] == "Muito rápido"
    db2.restaurar_padroes_embutidos()
    assert "Ben-Geder Trindade" in {p["dados"]["nome"] for p in db2.listar_padroes()}


def test_edicao_local_de_embutido_e_preservada(dados):
    db = Database()
    arena = next(p for p in db.listar_padroes() if p["slug"] == "arena-de-vendas")
    d = arena["dados"]; d["descricao"] = "editado"
    db.salvar_padrao(arena["id"], d)
    db.conn.close()
    assert Database().obter_padrao(arena["id"])["descricao"] == "editado"


def test_atualizacao_do_programa(dados, monkeypatch, tmp_path):
    db = Database()
    arena = next(p for p in db.listar_padroes() if p["slug"] == "arena-de-vendas")
    ben = next(p for p in db.listar_padroes() if p["slug"] == "ben-geder")
    d = ben["dados"]; d["descricao"] = "ajuste local"
    db.salvar_padrao(ben["id"], d)
    db.conn.close()
    # Simula nova versão do programa com padrões alterados
    res = tmp_path / "res"; res.mkdir()
    emb = json.loads((config.resources_dir() / "padroes_embutidos.json").read_text(encoding="utf-8"))
    for it in emb["padroes"]:
        it["padrao"]["descricao"] = "versão nova"
    emb["padroes"].append({"slug": "novo-padrao", "padrao": {"nome": "Padrão Novo"}})
    (res / "padroes_embutidos.json").write_text(json.dumps(emb), encoding="utf-8")
    monkeypatch.setattr(config, "resources_dir", lambda: res)
    db2 = Database()
    assert db2.obter_padrao(arena["id"])["descricao"] == "versão nova"     # não editado → atualiza
    assert db2.obter_padrao(ben["id"])["descricao"] == "ajuste local"      # editado → mantém
    assert "Padrão Novo" in {p["dados"]["nome"] for p in db2.listar_padroes()}


def test_exportar_para_programa_nao_duplica(dados, monkeypatch, tmp_path):
    db = Database()
    pid = db.criar_padrao({"nome": "Meu Padrão"})
    exp = db.exportar_embutidos_json()
    res = tmp_path / "res"; res.mkdir()
    (res / "padroes_embutidos.json").write_text(json.dumps(exp), encoding="utf-8")
    db.conn.close()
    monkeypatch.setattr(config, "resources_dir", lambda: res)
    db2 = Database()
    nomes = [p["dados"]["nome"] for p in db2.listar_padroes()]
    assert nomes.count("Meu Padrão") == 1
    assert next(p for p in db2.listar_padroes() if p["id"] == pid)["embutido"]


def test_projeto_guarda_copia_do_padrao(dados):
    db = Database()
    arena = next(p for p in db.listar_padroes() if p["slug"] == "arena-de-vendas")
    proj = db.salvar_projeto("Teste", {"padrao_id": arena["id"], "padrao": arena["dados"], "videos": []})
    d = arena["dados"]; d["identidade"]["cores"]["principal"] = "#000000"
    db.salvar_padrao(arena["id"], d)
    assert db.obter_projeto(proj)["dados"]["padrao"]["identidade"]["cores"]["principal"] == "#8800FF"


def test_completar_mantem_estrutura():
    p = completar({"nome": "X", "edicao": {"ritmo": "Lento"}})
    assert p["edicao"]["ritmo"] == "Lento" and p["edicao"]["cortes"] == "Moderados"
    assert "biblioteca" in p and hash_padrao(p) == hash_padrao(completar(p))


# ---------------- interface (fluxo completo) ----------------
def _esperar(app, cond, seg=20):
    fim = time.time() + seg
    while time.time() < fim and not cond():
        app.processEvents(); time.sleep(0.02)
    return cond()


def test_fluxo_interface(videos, dados, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from nimage_studio.ui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow(Database()); w.show()
    for i in range(6):
        w.ir_para(i); app.processEvents()
        assert w.pilha.currentIndex() == i

    nova = w.nova
    w.ir_para(1)
    nova.adicionar_videos([str(videos["h"]), str(videos["v"]), str(videos["falso"]), str(videos["h"])])
    assert len(nova.videos) == 3  # duplicado ignorado
    assert _esperar(app, lambda: all(v is not None for v in nova.videos.values()))
    assert len(nova.videos_validos()) == 2
    assert "erro" in nova.videos[str(videos["falso"])]
    assert "1280×720" in nova.cards[str(videos["h"])].detalhes.text()

    # Padrão escolhido preenche os ajustes e o formato
    nova.padrao.setCurrentIndex(nova.padrao.findText("Arena de Vendas®"))
    assert (nova.larg.value(), nova.alt.value()) == (1080, 1920)
    assert nova.intensidade.currentText() == "Enérgica"
    # Formato livre
    nova.formato.setCurrentText("Personalizado…")
    assert nova.larg.isEnabled()
    nova.larg.setValue(1234); nova.alt.setValue(988)
    nova.objetivo.setText("Reels do Grande Dia")
    nova.instrucoes.setPlainText("Comece com a frase mais forte.")
    pid = nova.salvar()
    assert pid

    # Prévia carrega os vídeos do projeto
    w.mostrar_previa(str(videos["v"]))
    assert w.previa.lista.count() == 2 and w.previa.titulo.text() == videos["v"].name

    # Reabrir projeto em janela nova (simula reiniciar)
    w.close()
    w2 = MainWindow(Database())
    w2.abrir_projeto(pid)
    n2 = w2.nova
    assert _esperar(app, lambda: n2.videos and all(v is not None for v in n2.videos.values()))
    assert len(n2.videos_validos()) == 2
    assert (n2.larg.value(), n2.alt.value()) == (1234, 988)
    assert n2.padrao.currentText() == "Arena de Vendas®"
    assert n2.instrucoes.toPlainText() == "Comece com a frase mais forte."

    # Gerenciador de padrões: editar e salvar pela interface
    w2.ir_para(3)
    pp = w2.padroes
    pp.lista.setCurrentRow(pp.lista.count() - 1)
    pp.f_nome.setText("Institucional v2"); pp._marcar_sujo()
    assert pp.btn_salvar.isEnabled()
    pp.salvar()
    assert "Institucional v2" in [n2.padrao.itemText(i) for i in range(n2.padrao.count())]
    w2.close()
