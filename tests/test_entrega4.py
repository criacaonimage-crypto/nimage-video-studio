"""Testes da 4ª entrega: ajustes em linguagem natural, Claude (API simulada) e fluxo completo até o MP4."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

import test_entrega2 as T2  # noqa: E402
from nimage_studio import analysis, config, media, plan, revisions  # noqa: E402
from nimage_studio.ai import ProvedorClaude, ProvedorErro, config_ia, provedor_de  # noqa: E402
from nimage_studio.ai import claude as claude_mod  # noqa: E402
from nimage_studio.db import Database  # noqa: E402

dados = T2.dados
ollama_falso = T2.ollama_falso
video_com_cortes = T2.video_com_cortes


def _base(db):
    an = T2._analise_fake()
    an["transcricao"] = [{**s, "palavras": [{"p": w, "i": s["inicio"], "f": s["fim"]} for w in s["texto"].split()]}
                         for s in an["transcricao"]]
    proj = T2._projeto(db, an["info"], duracao=15)
    us = plan.montar_unidades([an])
    pl = plan.normalizar(T2._resposta_ok(), proj, [an], us, "t")
    return an, proj, us, pl


def test_operacoes_basicas(dados):
    db = Database()
    an, proj, us, pl = _base(db)
    ids_texto = {c["id"]: c["texto"] for c in pl["clipes"]}
    ops = [{"acao": "mover_trecho", "trecho": "T3", "posicao": 1},
           {"acao": "remover_trecho", "trecho": "T2"},
           {"acao": "volume_musica", "valor": 8},
           {"acao": "transicao", "tipo": "Suaves"},
           {"acao": "legenda", "tipo": "Palavra por palavra"},
           {"acao": "destacar", "palavras": ["quinze minutos"]},
           {"acao": "zoom", "trecho": "T1", "ativar": True},
           {"acao": "encerramento", "tipo": "Chamada para ação", "texto": "Siga @arenadevendas"},
           {"acao": "remover_trecho", "trecho": "T99"},
           {"acao": "transicao", "tipo": "Explosão"}]
    novo, feitas, avisos = revisions.aplicar_operacoes(pl, ops, proj, [an], us)
    assert [c["texto"] for c in novo["clipes"]] == [ids_texto["T3"], ids_texto["T1"]]
    assert [c["id"] for c in novo["clipes"]] == ["T1", "T2"]
    assert novo["trilha"]["volume"] == 8 and novo["transicao"] == "Suaves"
    assert novo["legendas"]["estilo"] == "Palavra por palavra"
    assert "quinze minutos" in novo["clipes"][0]["destaques"]
    assert novo["clipes"][1]["zoom"] == 1.1 and novo["clipes"][1]["zoom_manual"]   # ids = os do plano enviado à IA
    assert novo["encerramento"] == {"tipo": "Chamada para ação", "texto": "Siga @arenadevendas", "arquivo": None}
    assert len(avisos) == 2 and len(feitas) == 8
    assert pl["trilha"]["volume"] == 20   # plano original intacto (desfazer)


def test_adicionar_substituir_encurtar(dados):
    db = Database()
    an, proj, us, pl = _base(db)
    ops = [{"acao": "adicionar_trecho", "de": "V1-F001", "ate": "V1-F001", "posicao": 1},
           {"acao": "substituir_trecho", "trecho": "T3", "de": "V1-F003", "ate": "V1-F003"},
           {"acao": "encurtar_trecho", "trecho": "T2", "cortar_fim_seg": 1.0},
           {"acao": "adicionar_trecho", "de": "V1-F004", "ate": "V1-F004"}]   # repetido → recusado
    antes_t2 = pl["clipes"][1]["duracao"]
    novo, feitas, avisos = revisions.aplicar_operacoes(pl, ops, proj, [an], us)
    assert "Bom dia" in novo["clipes"][0]["texto"]
    assert "campeão" in novo["clipes"][3]["texto"]                       # T3 substituído, mesma posição
    assert abs(novo["clipes"][2]["duracao"] - (antes_t2 - 1.0)) < 0.01   # T2 encurtado
    assert any("repetia" in a for a in avisos)
    assert novo["duracao_total"] == round(sum(c["duracao"] for c in novo["clipes"]), 2)


def test_ajustar_com_ia_e_pedido_bem_formado(dados, ollama_falso):
    db = Database()
    an, proj, us, pl = _base(db)
    T2._FakeOllama.respostas = [{"entendimento": "Vou abaixar a música.", "operacoes": [{"acao": "volume_musica", "valor": 10}]}]
    prov = provedor_de({"modo": "local", "ollama_url": ollama_falso, "ollama_modelo": "qwen2.5:7b"}, 8192)
    novo = revisions.ajustar("Abaixe a música", pl, proj, [an], prov)
    assert novo["trilha"]["volume"] == 10 and novo["ultimo_ajuste"]["entendimento"] == "Vou abaixar a música."
    pedido = T2._FakeOllama.pedidos[-1]["messages"][1]["content"]
    assert "PEDIDO DO USUÁRIO: Abaixe a música" in pedido and "volume 20" in pedido
    assert "T1 [" in pedido and "V1-F001" in pedido      # plano atual + trechos livres
    assert "V1-F004 [" not in pedido                     # trecho já usado não aparece como livre
    T2._FakeOllama.respostas = [{"entendimento": "?", "operacoes": []}]
    with pytest.raises(plan.PlanoErro):
        revisions.ajustar("faça mágica", pl, proj, [an], prov)


# ------------------------------------------------------------------ Claude (API simulada)
class _FakeClaude(BaseHTTPRequestHandler):
    respostas: list = []
    pedidos: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeClaude.pedidos.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "corpo": corpo})
        if self.headers.get("x-api-key") != "sk-ant-teste":
            b = json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}).encode()
            self.send_response(401)
        else:
            entrada = _FakeClaude.respostas.pop(0) if _FakeClaude.respostas else {"ok": "ok"}
            b = json.dumps({"content": [{"type": "tool_use", "name": "responder", "input": entrada}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


@pytest.fixture()
def claude_falso(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeClaude)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(claude_mod, "API_URL", f"http://127.0.0.1:{srv.server_port}/v1/messages")
    _FakeClaude.respostas, _FakeClaude.pedidos = [], []
    yield
    srv.shutdown()


def test_claude_plano_e_erros(dados, claude_falso):
    db = Database()
    an, proj, us, _ = _base(db)
    _FakeClaude.respostas = [T2._resposta_ok()]
    prov = provedor_de({"modo": "claude", "claude_chave": "sk-ant-teste", "claude_modelo": "claude-sonnet-5-5"}, 0)
    assert isinstance(prov, ProvedorClaude) and prov.nome == "Claude · claude-sonnet-5-5"
    resp = prov.gerar_json(plan.SISTEMA, plan.montar_pedido(proj, [an], us), plan.SCHEMA_PLANO)
    pl = plan.normalizar(resp, proj, [an], us, prov.nome)
    assert len(pl["clipes"]) == 3
    req = _FakeClaude.pedidos[-1]
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert req["corpo"]["tool_choice"] == {"type": "tool", "name": "responder"}
    assert req["corpo"]["tools"][0]["input_schema"] == plan.SCHEMA_PLANO
    assert len(json.dumps(req["corpo"])) < 30000   # só texto (transcrição e trechos), nunca o vídeo
    ruim = provedor_de({"modo": "claude", "claude_chave": "errada"}, 0)
    with pytest.raises(ProvedorErro, match="chave de API inválida"):
        ruim.gerar_json("s", "u", {"type": "object"})


def test_config_ia_padrao_e_local(dados):
    db = Database()
    cfg = config_ia(db)
    assert cfg["modo"] == "local"   # funciona sem Claude por padrão
    assert provedor_de(cfg, 12288).modelo == "qwen2.5:14b"


# ------------------------------------------------------------------ fluxo completo pela interface
def _esperar(app, cond, seg=60):
    fim = time.time() + seg
    while time.time() < fim and not cond():
        app.processEvents(); time.sleep(0.02)
    return cond()


def test_fluxo_completo_ate_mp4(dados, video_com_cortes, ollama_falso, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from nimage_studio.ui.export_dialog import ExportDialog
    from nimage_studio.ui.main_window import MainWindow
    monkeypatch.setattr(analysis, "transcrever", lambda *a, **k: [
        {"inicio": s["inicio"], "fim": s["fim"], "texto": s["texto"],
         "palavras": [{"p": w, "i": s["inicio"] + k * 0.3, "f": s["inicio"] + k * 0.3 + 0.28}
                      for k, w in enumerate(s["texto"].split())]} for s in T2._transcricao_falsa()])
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: avisos.append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    app = QApplication.instance() or QApplication(sys.argv)
    db = Database()
    db.set("ollama_url", ollama_falso); db.set("ollama_modelo", "qwen2.5:7b")
    w = MainWindow(db); w.show()
    n = w.nova
    n.adicionar_videos([str(video_com_cortes)])
    assert _esperar(app, n.btn_ia.isEnabled)
    n.padrao.setCurrentIndex(n.padrao.findText("Arena de Vendas®"))
    n.duracao.setValue(15)
    n.nome.setText("Teste final")
    T2._FakeOllama.respostas = [T2._resposta_ok()]
    n.btn_ia.click()                                         # 1) análise + plano
    assert not avisos, avisos
    pv = w.previa
    assert pv.clipes.count() == 3 and pv.b_gerar.isEnabled()
    pv.b_gerar.click()                                        # 2) gera o MP4 (FFmpeg de verdade)
    assert not avisos, avisos
    r1 = n.render
    assert r1 and Path(r1["arquivo"]).exists() and (r1["largura"], r1["altura"]) == (1080, 1920)
    assert "Resultado:" in pv.res_status.text() and pv.b_ver_res.isEnabled()
    # 3) ajuste em linguagem natural → plano muda e o vídeo é refeito
    T2._FakeOllama.respostas = [{"entendimento": "Removo o trecho T2.", "operacoes": [{"acao": "remover_trecho", "trecho": "T2"}]}]
    pv.ajuste.setText("Retire o segundo trecho")
    pv.b_ajuste.click()
    assert not avisos, avisos
    assert len(n.plano["clipes"]) == 2 and "Removo o trecho T2." in pv.ultimo_ajuste.text()
    r2 = n.render
    assert r2["arquivo"] != r1["arquivo"] and not Path(r1["arquivo"]).exists()
    assert r2["duracao"] < r1["duracao"]
    # 4) desfazer
    assert pv.b_desfazer.isEnabled()
    pv.b_desfazer.click()
    assert len(n.plano["clipes"]) == 3 and "plano mudou" in pv.res_status.text()
    # 5) exportar com Salvar como (formato quadrado, qualidade rascunho)
    destino = tmp_path / "exportado" / "meu video"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destino), "MP4"))

    def fake_exec(dlg):
        dlg.formato.setCurrentText("Quadrado (1080×1080)")
        dlg.qualidade.setCurrentText("Rascunho rápido")
        return ExportDialog.Accepted
    monkeypatch.setattr(ExportDialog, "exec", fake_exec)
    pv.b_exportar.click()
    assert not avisos, avisos
    final = Path(str(destino) + ".mp4")
    info = media.probe(final)
    assert (info["largura"], info["altura"]) == (1080, 1080) and info["tem_audio"]
    # 6) tudo persiste ao reabrir
    pid = n.projeto_id
    w.close()
    w2 = MainWindow(db)
    w2.abrir_projeto(pid)
    w2.ir_para(2)
    assert w2.previa.clipes.count() == 3 and w2.nova.render and not w2.nova.historico
    w2.close()
