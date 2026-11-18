"""Testes da 3ª entrega: renderização real com o FFmpeg (gera MP4s e confere o resultado)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nimage_studio import config, fonts, media, plan, renderer  # noqa: E402
from nimage_studio.db import Database  # noqa: E402

FF = None


def ff(*args):
    subprocess.run([config.ffmpeg_path(), "-v", "error", "-y", *args], check=True)


@pytest.fixture()
def dados(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMAGE_DATA_DIR", str(tmp_path / "dados"))
    return tmp_path / "dados"


@pytest.fixture(scope="session")
def midia(tmp_path_factory):
    d = tmp_path_factory.mktemp("m3")
    v = d / "palestra.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:d=20", "-f", "lavfi", "-i", "sine=frequency=220:d=20",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v))
    mudo = d / "broll mudo.mov"
    ff("-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mudo))
    trilha = d / "trilha energica.mp3"
    ff("-f", "lavfi", "-i", "sine=frequency=660:d=7", "-c:a", "libmp3lame", str(trilha))
    whoosh = d / "whoosh 01.wav"
    ff("-f", "lavfi", "-i", "anoisesrc=d=0.4:a=0.3", str(whoosh))
    vinheta = d / "vinheta final Arena.MOV"
    ff("-f", "lavfi", "-i", "color=c=0x8800FF:s=1920x1080:r=30:d=2", "-f", "lavfi", "-i", "sine=frequency=880:d=2",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vinheta))
    logo = d / "logo.png"
    ff("-f", "lavfi", "-i", "color=c=white:s=400x200", "-frames:v", "1", str(logo))
    return {"v": v, "mudo": mudo, "trilha": trilha, "whoosh": whoosh, "vinheta": vinheta, "logo": logo}


def _plano(midia, transicao="Corte seco", W=1080, H=1920, estilo="Palavra-chave em destaque", encerramento="Vinheta"):
    an = {"info": media.probe(midia["v"]),
          "transcricao": [{"inicio": 1.0, "fim": 4.0, "texto": "A disciplina vence o talento.",
                           "palavras": [{"p": "A", "i": 1.0, "f": 1.3}, {"p": "disciplina", "i": 1.3, "f": 2.2},
                                        {"p": "vence", "i": 2.2, "f": 2.8}, {"p": "o", "i": 2.8, "f": 3.0},
                                        {"p": "talento.", "i": 3.0, "f": 4.0}]},
                          {"inicio": 6.0, "fim": 9.0, "texto": "Quinze minutos por dia.",
                           "palavras": [{"p": w, "i": 6 + k * 0.7, "f": 6.6 + k * 0.7}
                                        for k, w in enumerate("Quinze minutos por dia.".split())]}],
          "cenas": [{"inicio": 0, "fim": 20}]}
    an2 = {"info": media.probe(midia["mudo"]), "transcricao": [], "cenas": [{"inicio": 0, "fim": 6}]}
    db = Database()
    padrao = next(p for p in db.listar_padroes() if p["slug"] == "arena-de-vendas")["dados"]
    padrao["biblioteca"]["trilhas"] = [str(midia["trilha"])]
    padrao["biblioteca"]["efeitos_sonoros"] = [str(midia["whoosh"])]
    padrao["biblioteca"]["logos"] = [str(midia["vinheta"]), str(midia["logo"])]
    proj = {"padrao": padrao, "duracao_seg": 12, "formato": {"largura": W, "altura": H}, "objetivo": "teste",
            "ajustes": {"estilo_legenda": estilo, "tom_musica": "Enérgica"}}
    us = plan.montar_unidades([an, an2])
    resp = {"titulo": "Teste", "clipes": [
        {"de": "V1-F001", "ate": "V1-F001", "motivo": "a", "destaques": ["disciplina"]},
        {"de": "V2-C001", "ate": "V2-C001", "motivo": "b-roll", "destaques": []},
        {"de": "V1-F002", "ate": "V1-F002", "motivo": "b", "destaques": []}],
        "abertura": {"tipo": "Título", "texto": "DISCIPLINA"},
        "encerramento": {"tipo": encerramento, "texto": "Siga @arenadevendas"},
        "trilha": {"usar": True, "tom": "Enérgica", "volume": 20}, "transicao": transicao, "justificativa": ""}
    pl = plan.normalizar(resp, proj, [an, an2], us, "teste")
    return pl


def _streams(arq):
    """Tipos e codecs das trilhas do arquivo (via PyAV, sem ffprobe)."""
    import av
    with av.open(str(arq)) as c:
        return {"streams": [{"codec_type": s.type, "codec_name": s.codec_context.name,
                             "pix_fmt": getattr(s.codec_context, "pix_fmt", None)} for s in c.streams]}


def test_render_completo_vertical(dados, midia, tmp_path):
    pl = _plano(midia)
    assert pl["clipes"][1]["video_nome"] == "broll mudo.mov"
    msgs = []
    out = tmp_path / "saida" / "final.mp4"
    info = renderer.renderizar(pl, str(out), {"qualidade": "Rascunho rápido"}, lambda m, f: msgs.append(f))
    assert out.exists() and (info["largura"], info["altura"]) == (1080, 1920)
    total_clipes = sum(c["duracao"] for c in pl["clipes"])
    assert total_clipes + 2.6 + 1.9 < info["duracao"] < total_clipes + 2.6 + 2.2  # título + trechos + vinheta
    s = _streams(out)
    tipos = {x["codec_type"]: x for x in s["streams"]}
    assert tipos["video"]["codec_name"] == "h264" and tipos["audio"]["codec_name"] == "aac"
    assert tipos["video"]["pix_fmt"] == "yuv420p"
    assert msgs == sorted(msgs) and msgs[-1] == 1.0
    # o vídeo decodifica inteiro sem erros (MP4 reproduzível)
    r = config.run([config.ffmpeg_path(), "-v", "error", "-i", str(out), "-f", "null", "-"], timeout=120)
    assert r.returncode == 0 and not r.stderr.strip()


@pytest.mark.parametrize("transicao", ["Suaves", "Dinâmicas"])
def test_render_transicoes_horizontal(dados, midia, tmp_path, transicao):
    pl = _plano(midia, transicao=transicao, W=1920, H=1080, estilo="Simples", encerramento="Logo")
    out = tmp_path / f"{transicao}.mp4"
    info = renderer.renderizar(pl, str(out), {"qualidade": "Rascunho rápido"})
    td = renderer.TRANSICAO_XFADE[transicao][1]
    esperado = 2.6 + sum(c["duracao"] for c in pl["clipes"]) + 2.6 - td * (len(pl["clipes"]) + 1)
    assert abs(info["duracao"] - esperado) < 0.35
    assert (info["largura"], info["altura"]) == (1920, 1080)


def test_render_formato_livre_e_sem_extras(dados, midia, tmp_path):
    pl = _plano(midia, estilo="Sem legenda", encerramento="Sem encerramento")
    pl["legendas"]["ativar"] = False
    pl["trilha"]["usar"] = False
    pl["efeitos_sonoros"] = False
    pl["abertura"]["tipo"] = "Direto ao ponto"
    out = tmp_path / "livre.mp4"
    info = renderer.renderizar(pl, str(out), {"largura": 1234, "altura": 988, "qualidade": "Rascunho rápido"})
    assert (info["largura"], info["altura"]) == (1234, 988)
    assert abs(info["duracao"] - sum(c["duracao"] for c in pl["clipes"])) < 0.3


def test_sem_executaveis_externos(dados, midia, tmp_path, monkeypatch):
    """A edição inteira roda sem ffmpeg.exe/ffprobe.exe (o Windows da Nimage bloqueia executáveis sem assinatura)."""
    import subprocess as sp
    chamadas = []
    original = sp.Popen

    def espiao(cmd, *a, **k):
        chamadas.append(cmd)
        return original(cmd, *a, **k)
    monkeypatch.setattr(sp, "Popen", espiao)
    pl = _plano(midia)
    renderer.renderizar(pl, str(tmp_path / "x.mp4"), {"qualidade": "Rascunho rápido"})
    assert not [c for c in chamadas if "ffmpeg" in str(c).lower() or "ffprobe" in str(c).lower()]


def test_quadros_no_tempo_certo(midia):
    """O trecho começa no quadro certo do original (comparado ao 1º quadro decodificado no mesmo instante)."""
    import numpy as np
    from nimage_studio import avtools
    ref = avtools.quadro_em(str(midia["v"]), 6.0)
    primeiro = next(avtools.quadros(str(midia["v"]), 6.0, 1.0, 30))
    assert float(np.abs(ref.astype(int) - primeiro.astype(int)).mean()) < 3
    assert len(list(avtools.quadros(str(midia["v"]), 2.0, 1.5, 30))) == 45


def test_video_original_ausente(dados, midia, tmp_path):
    pl = _plano(midia)
    pl["clipes"][0]["video"] = str(tmp_path / "sumiu.mp4")
    with pytest.raises(renderer.RenderErro, match="não encontrados"):
        renderer.renderizar(pl, str(tmp_path / "x.mp4"))


def test_cancelar(dados, midia, tmp_path):
    with pytest.raises(renderer.RenderCancelado):
        renderer.renderizar(_plano(midia), str(tmp_path / "c.mp4"), cancelado=lambda: True)


def test_legendas_eventos_e_desenho(midia, dados):
    import numpy as np
    from nimage_studio import textdraw
    pl = _plano(midia)
    ev = renderer.eventos_legenda([(2.6, pl["clipes"][0])], pl["legendas"])
    assert ev and ev[0]["a"] >= 2.6
    todas = [p for e in ev for p in e["palavras"]]
    assert ("DISCIPLINA", True) in todas                       # destaque em caixa alta
    simples = renderer.eventos_legenda([(0, pl["clipes"][0])], {**pl["legendas"], "estilo": "Simples"})
    assert not any(d for e in simples for _, d in e["palavras"])
    textdraw.garantir_qt()
    fam, _ = textdraw.familia("")
    x, y, bgra = textdraw.legenda(todas[:3], 1080, 1920, fam, 56, "#FFFFFF", "#8800FF", 3, "baixo", 576)
    assert bgra.shape[2] == 4 and bgra[..., 3].max() == 255 and y + bgra.shape[0] <= 1920 - 576 + 1
    roxo = (bgra[..., 0] > 200) & (bgra[..., 1] < 60) & (bgra[..., 2] > 100) & (bgra[..., 3] > 200)
    assert roxo.sum() > 50                                     # palavra destacada desenhada em #8800FF


def test_rosto_e_enquadramento():
    x, y, cw, ch = renderer.caixa_enquadrar(1920, 1080, 1080, 1920, 1.0, (0.8, 0.4))
    assert (cw, ch) == (608, 1080) and x > 900                # recorte deslocado para o rosto
    assert renderer.caixa_enquadrar(1920, 1080, 1920, 1080, 1.1, None)[2:] == (1744, 982)


def test_fontes():
    assert fonts.encontrar("Fonte Que Nao Existe 123") is None
    arq, achou = fonts.arquivo_ou_padrao("Fonte Que Nao Existe 123")
    assert arq and not achou and fonts.nome_familia(arq)


def test_detector_de_rosto_carrega(midia):
    import cv2
    det = cv2.CascadeClassifier(str(config.resources_dir() / "haarcascade_frontalface_default.xml"))
    assert not det.empty()
    assert renderer.centro_do_rosto(str(midia["v"]), 0, 5) is None   # vídeo sem rosto → centro padrão
