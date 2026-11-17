"""Testes da 2ª entrega: análise, Ollama (simulado por um servidor HTTP local) e plano de edição."""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nimage_studio import analysis, config, plan, planner  # noqa: E402
from nimage_studio.ai import ProvedorOllama  # noqa: E402
from nimage_studio.ai.ollama import Ollama, OllamaErro, recomendado_para  # noqa: E402
from nimage_studio.db import Database  # noqa: E402

FALAS = [
    (0.5, 3.0, "Bom dia a todos, obrigado por estarem aqui."),
    (3.2, 7.8, "Hoje eu quero falar sobre liderança em vendas."),
    (8.0, 12.5, "O vendedor campeão não espera o cliente, ele vai buscar."),
    (12.7, 13.4, "Né?"),
    (13.6, 18.0, "A disciplina vence o talento quando o talento não tem disciplina."),
    (18.2, 22.0, "Então anota aí: quinze minutos de prospecção, quatro vezes por dia."),
]


@pytest.fixture()
def dados(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMAGE_DATA_DIR", str(tmp_path / "dados"))
    return tmp_path / "dados"


@pytest.fixture(scope="session")
def video_com_cortes(tmp_path_factory):
    """24 s: 3 cenas de cores diferentes (para a detecção de cenas) + áudio."""
    d = tmp_path_factory.mktemp("v2")
    out = d / "palestra teste.mp4"
    ff = config.ffmpeg_path()
    subprocess.run([ff, "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:size=640x360:rate=25:d=8",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:d=8",
                    "-f", "lavfi", "-i", "color=c=white:size=640x360:rate=25:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=300:d=24",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                    "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(out)], check=True)
    return out


def _transcricao_falsa(*_a, **_k):
    return [{"inicio": a, "fim": b, "texto": t,
             "palavras": [{"p": w, "i": a, "f": b} for w in t.split()]} for a, b, t in FALAS]


# ------------------------------------------------------------------ servidor Ollama simulado
class _FakeOllama(BaseHTTPRequestHandler):
    respostas: list = []
    pedidos: list = []

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/version":
            self._json({"version": "0.9.0-teste"})
        elif self.path == "/api/tags":
            self._json({"models": [{"name": "qwen2.5:7b"}, {"name": "qwen2.5:14b"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.pedidos.append(corpo)
        if self.path == "/api/chat":
            resp = _FakeOllama.respostas.pop(0) if _FakeOllama.respostas else {"clipes": []}
            self._json({"message": {"role": "assistant", "content": json.dumps(resp)}, "done": True})
        elif self.path == "/api/pull":
            linhas = [{"status": "pulling", "total": 100, "completed": 50}, {"status": "success"}]
            b = "\n".join(json.dumps(x) for x in linhas).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
        else:
            self._json({"error": "not found"}, 404)


@pytest.fixture()
def ollama_falso():
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    _FakeOllama.respostas, _FakeOllama.pedidos = [], []
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _resposta_ok(**extra):
    r = {"titulo": "Disciplina em vendas",
         "clipes": [{"de": "V1-F004", "ate": "V1-F004", "motivo": "frase de impacto", "destaques": ["disciplina", "inventada"]},
                    {"de": "V1-F002", "ate": "V1-F002", "motivo": "contexto", "destaques": ["liderança"]},
                    {"de": "V1-F005", "ate": "V1-F005", "motivo": "chamada prática", "destaques": []}],
         "abertura": {"tipo": "Frase de impacto", "texto": ""},
         "encerramento": {"tipo": "Vinheta", "texto": ""},
         "trilha": {"usar": True, "tom": "Enérgica", "volume": 20},
         "transicao": "Dinâmicas", "justificativa": "Abre com a frase mais forte."}
    r.update(extra)
    return r


def _projeto(db, video_info, duracao=15):
    arena = next(p for p in db.listar_padroes() if p["slug"] == "arena-de-vendas")["dados"]
    return {"videos": [video_info], "padrao": arena, "objetivo": "Reels de liderança",
            "duracao_seg": duracao, "formato": {"largura": 1080, "altura": 1920},
            "ajustes": {"intensidade": "Enérgica", "ritmo": "Rápido", "tom_musica": "Enérgica",
                        "estilo_legenda": "Palavra-chave em destaque"},
            "instrucoes": "Comece com a frase mais forte."}


# ------------------------------------------------------------------ análise
def test_analise_com_cenas_e_cache(dados, video_com_cortes, monkeypatch):
    from nimage_studio import media
    monkeypatch.setattr(analysis, "transcrever", _transcricao_falsa)
    info = media.probe(video_com_cortes)
    msgs = []
    an = analysis.analisar_video(info, "Automático", 0, lambda m, f: msgs.append((m, f)))
    assert len(an["cenas"]) == 3
    assert abs(an["cenas"][1]["inicio"] - 8.0) < 0.2 and abs(an["cenas"][2]["inicio"] - 16.0) < 0.2
    assert len(an["transcricao"]) == len(FALAS)
    assert not list(config.cache_dir().glob("audio_*.wav"))  # áudio temporário apagado
    # segunda vez: vem do cache, sem chamar transcrição
    monkeypatch.setattr(analysis, "transcrever", lambda *a, **k: pytest.fail("deveria usar cache"))
    an2 = analysis.analisar_video(info, "Automático", 0, lambda m, f: None)
    assert an2["cenas"] == an["cenas"]


def test_escolha_do_whisper_por_placa():
    assert analysis.escolher_whisper("Automático", 12288) == ("large-v3", "cuda", "float16")
    assert analysis.escolher_whisper("Automático", 8192) == ("large-v3", "cuda", "int8_float16")
    assert analysis.escolher_whisper("Automático", 0) == ("small", "cpu", "int8")
    assert analysis.escolher_whisper("medium", 0)[0] == "medium"


def test_extrair_audio_real(dados, video_com_cortes, tmp_path):
    wav = analysis.extrair_audio(str(video_com_cortes), tmp_path / "a.wav")
    assert wav.stat().st_size > 16000 * 2 * 20  # ~24 s de áudio 16 kHz mono


# ------------------------------------------------------------------ unidades
def _analise_fake(dur=24.0, falas=FALAS, cenas=None):
    return {"info": {"caminho": "/x/v.mp4", "nome": "v.mp4", "duracao": dur},
            "transcricao": [{"inicio": a, "fim": b, "texto": t} for a, b, t in falas],
            "cenas": cenas or [{"inicio": 0, "fim": dur}]}


def test_unidades_juntam_falas_curtas_e_criam_trechos_sem_fala():
    an = _analise_fake(dur=40.0, cenas=[{"inicio": 0, "fim": 22}, {"inicio": 22, "fim": 40}])
    us = plan.montar_unidades([an])
    falas = [u for u in us if u["tipo"] == "fala"]
    assert len(falas) == 5  # "Né?" (0,7 s) foi unido ao trecho anterior
    assert "Né?" in falas[2]["texto"]
    imagens = [u for u in us if u["tipo"] == "imagem"]
    assert imagens and imagens[-1]["inicio"] >= 22 and imagens[-1]["id"].startswith("V1-C")


def test_video_sem_fala_vira_trechos_de_imagem():
    an = _analise_fake(dur=12, falas=[], cenas=[{"inicio": 0, "fim": 5}, {"inicio": 5, "fim": 12}])
    us = plan.montar_unidades([an])
    assert [u["id"] for u in us] == ["V1-C001", "V1-C002"]


# ------------------------------------------------------------------ normalização
def test_normalizar_valida_e_corrige(dados):
    db = Database()
    an = _analise_fake()
    proj = _projeto(db, an["info"], duracao=15)
    us = plan.montar_unidades([an])
    resp = _resposta_ok()
    resp["clipes"] += [{"de": "V9-F001", "ate": "V9-F002", "motivo": "x", "destaques": []},   # inexistente
                       {"de": "V1-F004", "ate": "V1-F004", "motivo": "repetido", "destaques": []}]  # repetido
    pl = plan.normalizar(resp, proj, [an], us, "teste")
    assert [c["trechos"][0] for c in pl["clipes"]] == ["V1-F004", "V1-F002", "V1-F005"]
    c1 = pl["clipes"][0]
    assert c1["inicio"] < 13.6 and c1["fim"] > 18.0 and "disciplina" in c1["texto"]
    assert c1["destaques"] == ["disciplina"]  # palavra inventada removida
    assert any("inexistente" in a for a in pl["avisos"]) and any("repetia" in a for a in pl["avisos"])
    assert any("trilha" in a.lower() for a in pl["avisos"])  # biblioteca vazia
    assert any("vinheta" in a.lower() for a in pl["avisos"])
    assert pl["legendas"]["cor_destaque"] == "#8800FF" and pl["formato"]["altura"] == 1920


def test_normalizar_corta_excesso_de_duracao(dados):
    db = Database()
    an = _analise_fake()
    proj = _projeto(db, an["info"], duracao=8)
    us = plan.montar_unidades([an])
    pl = plan.normalizar(_resposta_ok(), proj, [an], us, "teste")
    assert pl["duracao_total"] <= 8 * 1.25 or len(pl["clipes"]) == 1
    assert any("removido" in a for a in pl["avisos"])


def test_normalizar_sem_clipes_validos_falha(dados):
    db = Database()
    an = _analise_fake()
    with pytest.raises(plan.PlanoErro):
        plan.normalizar({"clipes": [{"de": "X", "ate": "Y"}]}, _projeto(db, an["info"]), [an],
                        plan.montar_unidades([an]), "t")


def test_trilha_e_vinheta_da_biblioteca(dados, tmp_path):
    db = Database()
    an = _analise_fake()
    proj = _projeto(db, an["info"])
    mus = tmp_path / "trilha.mp3"; mus.write_bytes(b"x")
    vin = tmp_path / "vinheta final Arena.MOV"; vin.write_bytes(b"x")
    proj["padrao"]["biblioteca"]["trilhas"] = [str(tmp_path / "apagada.mp3"), str(mus)]
    proj["padrao"]["biblioteca"]["logos"] = [str(vin)]
    pl = plan.normalizar(_resposta_ok(), proj, [an], plan.montar_unidades([an]), "t")
    assert pl["trilha"]["arquivo"] == str(mus) and pl["encerramento"]["arquivo"] == str(vin)
    proj["ajustes"]["tom_musica"] = "Sem música"
    assert plan.normalizar(_resposta_ok(), proj, [an], plan.montar_unidades([an]), "t")["trilha"]["usar"] is False


# ------------------------------------------------------------------ Ollama
def test_ollama_cliente(ollama_falso):
    o = Ollama(ollama_falso)
    assert o.disponivel() and o.versao() == "0.9.0-teste"
    assert o.modelos() == ["qwen2.5:14b", "qwen2.5:7b"]
    prog = []
    o.baixar("qwen2.5:7b", lambda s, f: prog.append(f))
    assert 0.5 in prog
    _FakeOllama.respostas = [{"ids": ["a"]}]
    assert o.chat_json("qwen2.5:7b", "s", "u", plan.SCHEMA_PRESELECAO, contexto=12288) == {"ids": ["a"]}
    ped = _FakeOllama.pedidos[-1]
    assert ped["format"] == plan.SCHEMA_PRESELECAO and ped["options"]["num_ctx"] == 12288 and ped["stream"] is False


def test_ollama_fora_do_ar():
    o = Ollama("http://127.0.0.1:9")
    assert not o.disponivel()
    with pytest.raises(OllamaErro, match="Ollama"):
        o.modelos()


def test_recomendacao_de_modelo():
    assert recomendado_para(12288)["modelo"] == "qwen2.5:14b"
    assert recomendado_para(8192)["modelo"] == "qwen2.5:7b"
    assert recomendado_para(0)["modelo"] == "qwen2.5:3b"


# ------------------------------------------------------------------ fluxo completo (análise + IA)
def test_planejar_fluxo_completo_com_retentativa(dados, video_com_cortes, ollama_falso, monkeypatch):
    from nimage_studio import media
    monkeypatch.setattr(analysis, "transcrever", _transcricao_falsa)
    db = Database()
    proj = _projeto(db, media.probe(video_com_cortes), duracao=15)
    _FakeOllama.respostas = [{"clipes": [{"de": "inventado", "ate": "x", "motivo": "", "destaques": []}]},
                             _resposta_ok()]
    etapas = []
    analises = planner.analisar_projeto(proj, "Automático", 0, lambda e, m, f: etapas.append((e, f)))
    prov = ProvedorOllama(ollama_falso, "qwen2.5:7b", 8192)
    pl = planner.planejar(proj, analises, prov, lambda e, m, f: etapas.append((e, f)))
    assert len(pl["clipes"]) == 3 and pl["ia"] == "IA local · qwen2.5:7b"
    assert len(_FakeOllama.pedidos) == 2
    segundo = _FakeOllama.pedidos[1]["messages"][1]["content"]
    assert "resposta anterior foi recusada" in segundo
    assert "V1-F004" in segundo and "RESTRIÇÕES" in segundo and "Comece com a frase mais forte." in segundo
    fr = [f for _, f in etapas]
    assert fr == sorted(fr) and fr[-1] == 1.0  # progresso só avança


def test_preselecao_em_transcricao_longa(dados, ollama_falso):
    falas = [(i * 5.0, i * 5.0 + 4.5, f"Frase número {i} sobre vendas e resultados da equipe comercial.")
             for i in range(400)]
    an = _analise_fake(dur=2000, falas=falas)
    db = Database()
    proj = _projeto(db, an["info"], duracao=30)
    us = plan.montar_unidades([an])
    _FakeOllama.respostas = [{"ids": [us[10]["id"], us[11]["id"], "falso"]}] + [{"ids": []}] * 20
    prov = ProvedorOllama(ollama_falso, "qwen2.5:7b", 8192)
    sel = plan.preselecionar(us, proj, prov.gerar_json, prov.limite_texto, lambda m, f: None)
    assert [u["id"] for u in sel] == [us[10]["id"], us[11]["id"], us[12]["id"]]
    assert len(_FakeOllama.pedidos) > 1  # leu em partes


# ------------------------------------------------------------------ interface: botão → plano na prévia
def test_fluxo_interface_ia(dados, video_com_cortes, ollama_falso, monkeypatch):
    import sys
    import time
    from PySide6.QtWidgets import QApplication, QMessageBox
    from nimage_studio.ui.main_window import MainWindow
    monkeypatch.setattr(analysis, "transcrever", _transcricao_falsa)
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: avisos.append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    app = QApplication.instance() or QApplication(sys.argv)
    db = Database()
    db.set("ollama_url", ollama_falso)
    db.set("ollama_modelo", "qwen2.5:7b")
    w = MainWindow(db); w.show()
    n = w.nova
    n.adicionar_videos([str(video_com_cortes)])
    fim = time.time() + 20
    while time.time() < fim and not n.btn_ia.isEnabled():
        app.processEvents(); time.sleep(0.02)
    assert n.btn_ia.isEnabled()
    n.padrao.setCurrentIndex(n.padrao.findText("Arena de Vendas®"))
    n.duracao.setValue(15)
    _FakeOllama.respostas = [_resposta_ok()]
    n.btn_ia.click()                                  # roda análise + IA (janela de progresso)
    assert not avisos, avisos
    assert w.pilha.currentIndex() == 2                # foi para a prévia
    pv = w.previa
    assert pv.clipes.count() == 3 and "disciplina" in pv.clipes.item(0).text()
    assert "vinheta" in pv.p_avisos.text().lower()
    assert "liderança" in pv.transcricao.toPlainText()
    # editar plano: remover trecho e aprovar → salvo no projeto
    pv.clipes.setCurrentRow(1); pv._remover()
    pv._aprovar()
    salvo = db.obter_projeto(n.projeto_id)["dados"]
    assert len(salvo["plano"]["clipes"]) == 2 and salvo["plano"]["aprovado"] and salvo["status"] == "planejado"
    assert [c["id"] for c in salvo["plano"]["clipes"]] == ["T1", "T2"]
    pv.tocar_plano()                                  # prévia sequencial inicia sem erro
    assert pv._fila and pv._idx == 0
    # reabrir projeto em nova janela: plano e transcrição voltam
    w.close()
    w2 = MainWindow(db)
    w2.abrir_projeto(salvo and n.projeto_id)
    w2.ir_para(2)
    assert w2.previa.clipes.count() == 2 and "aprovado" in w2.previa.p_titulo.text()
    # Ollama fora do ar → aviso claro, sem travar
    db.set("ollama_url", "http://127.0.0.1:9")
    w2.ir_para(1); w2.planejar()
    assert any("Ollama" in a for a in avisos)
    # Configurações → verificar IA mostra os modelos
    db.set("ollama_url", ollama_falso)
    w2.config.ollama_url.setText(ollama_falso)
    assert w2.config.verificar_ia() and w2.config.modelo.count() == 2
    w2.close()


def test_cuda_libs_escolha_e_extracao(tmp_path):
    import zipfile
    from nimage_studio import cuda_libs
    meta = {"releases": {
        "12.9.3.13": [{"filename": "x-12.9.3.13-manylinux_x86_64.whl", "url": "u0"}],
        "12.9.2.10": [{"filename": "x-12.9.2.10-py3-none-win_amd64.whl", "url": "u1", "size": 5}],
        "12.10.0.1": [{"filename": "x-12.10.0.1-py3-none-win_amd64.whl", "url": "u2", "yanked": True}],
        "13.0.0.1": [{"filename": "x-13.0.0.1-py3-none-win_amd64.whl", "url": "u3"}]}}
    assert cuda_libs.escolher_wheel(meta, "12.")["url"] == "u1"
    whl = tmp_path / "a.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("nvidia/cublas/bin/cublas64_12.dll", b"dll")
        z.writestr("nvidia/cublas/include/x.h", b"h")
    assert cuda_libs.extrair_dlls(whl, tmp_path / "out") == 1
    assert (tmp_path / "out" / "cublas64_12.dll").exists()


def test_transcricao_recebe_audio_como_array(dados, video_com_cortes, tmp_path):
    """O áudio vai para o Whisper como array (sem PyAV)."""
    import numpy as np
    wav = analysis.extrair_audio(str(video_com_cortes), tmp_path / "a.wav")
    recebido = {}

    class Palavra:
        def __init__(s, w, a, b): s.word, s.start, s.end = w, a, b

    class Seg:
        start, end, text = 0.5, 2.0, " olá mundo"
        words = [Palavra(" olá", 0.5, 1.0), Palavra(" mundo", 1.0, 2.0)]

    class FakeWM:
        def transcribe(s, audio, **kw):
            recebido["audio"] = audio
            return iter([Seg()]), None

    segs = analysis._rodar_whisper(FakeWM(), wav, 24, lambda m, f: None, lambda: False)
    a = recebido["audio"]
    assert isinstance(a, np.ndarray) and a.dtype == np.float32 and abs(len(a) / 16000 - 24) < 0.2
    assert float(np.abs(a).max()) <= 1.0
    assert segs == [{"inicio": 0.5, "fim": 2.0, "texto": "olá mundo",
                     "palavras": [{"p": "olá", "i": 0.5, "f": 1.0}, {"p": "mundo", "i": 1.0, "f": 2.0}]}]
