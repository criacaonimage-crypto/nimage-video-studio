"""Teste de ponta a ponta com componentes REAIS (usado no GitHub Actions, no Windows):
Whisper de verdade (modelo tiny) + detecção de cenas + Ollama de verdade + renderização + ajuste.

Uso: python ferramentas/teste_real.py VIDEO.mp4 MODELO_OLLAMA
Falhas de execução (erros de biblioteca, travamentos) derrubam o teste.
Plano "fraco" de um modelo pequeno não derruba: só é relatado.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nimage_studio import analysis, media, plan, planner, renderer, revisions  # noqa: E402
from nimage_studio.ai import ProvedorOllama  # noqa: E402
from nimage_studio.db import Database  # noqa: E402


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main(video: str, modelo: str) -> int:
    info = media.probe(video)
    log(f"Vídeo: {info['nome']} {info['duracao']:.1f}s")
    db = Database()
    padrao = next(p for p in db.listar_padroes() if p["slug"] == "redes-sociais")["dados"]
    projeto = {"videos": [info], "padrao": padrao, "objetivo": "Teste", "duracao_seg": 10,
               "formato": {"largura": 1080, "altura": 1920}, "instrucoes": "Use as melhores frases.",
               "ajustes": {"intensidade": "Enérgica", "ritmo": "Rápido", "tom_musica": "Enérgica",
                           "estilo_legenda": "Palavra-chave em destaque"}}
    an = planner.analisar_projeto(projeto, "tiny", 0, lambda e, m, f: log(m))
    log(f"Transcrição real: {len(an[0]['transcricao'])} segmentos -> {[s['texto'] for s in an[0]['transcricao']][:3]}")
    log(f"Cenas: {len(an[0]['cenas'])} · modelo {an[0]['modelo_transcricao']}")
    if not an[0]["transcricao"]:
        log("ERRO: a transcrição real veio vazia")
        return 1
    prov = ProvedorOllama("", modelo, 0)
    try:
        pl = planner.planejar(projeto, an, prov, lambda e, m, f: log(m))
    except plan.PlanoErro as e:
        log(f"AVISO (qualidade do modelo pequeno, não é falha do programa): {e}")
        return 0
    log("Plano real:\n" + plan.resumo_texto(pl))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "real.mp4"
        r = renderer.renderizar(pl, str(out), {"qualidade": "Rascunho rápido"}, lambda m, f: None)
        log(f"Render real OK: {r['duracao']:.1f}s {r['largura']}x{r['altura']} avisos={r['avisos']}")
    try:
        novo = revisions.ajustar("Abaixe a música", pl, projeto, an, prov)
        log(f"Ajuste real OK: {novo['ultimo_ajuste']}")
    except plan.PlanoErro as e:
        log(f"AVISO no ajuste (modelo pequeno): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
