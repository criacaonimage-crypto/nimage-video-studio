"""ETAPA 6 — Revisão: interpreta um pedido em linguagem natural e altera só o necessário no plano.

A IA devolve uma lista de operações (remover T3, mover T5 para o início, abaixar a música…).
O programa aplica cada operação validando-a; operações inválidas viram aviso, o resto do plano é preservado.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import plan
from .patterns import OPCOES

ACOES = ["remover_trecho", "mover_trecho", "adicionar_trecho", "substituir_trecho", "encurtar_trecho",
         "volume_musica", "musica", "transicao", "legenda", "destacar", "zoom", "abertura", "encerramento",
         "efeitos_sonoros"]

SCHEMA_AJUSTE = {
    "type": "object",
    "properties": {
        "entendimento": {"type": "string"},
        "operacoes": {"type": "array", "items": {"type": "object", "properties": {
            "acao": {"type": "string", "enum": ACOES},
            "trecho": {"type": "string"},
            "de": {"type": "string"},
            "ate": {"type": "string"},
            "posicao": {"type": "integer"},
            "cortar_inicio_seg": {"type": "number"},
            "cortar_fim_seg": {"type": "number"},
            "valor": {"type": "number"},
            "tipo": {"type": "string"},
            "texto": {"type": "string"},
            "palavras": {"type": "array", "items": {"type": "string"}},
            "ativar": {"type": "boolean"},
        }, "required": ["acao"]}},
    },
    "required": ["entendimento", "operacoes"],
}

SISTEMA_AJUSTE = f"""Você é o editor de vídeo da Nimage e está ajustando um plano de edição que já existe.
Transforme o PEDIDO do usuário na MENOR lista de operações necessária. Tudo que o pedido não menciona deve continuar igual.

Operações (campo "acao") e campos usados:
- remover_trecho: trecho (ex.: "T3")
- mover_trecho: trecho, posicao (1 = primeiro)
- adicionar_trecho: de, ate (ids da lista de TRECHOS DISPONÍVEIS, mesmo vídeo), posicao
- substituir_trecho: trecho, de, ate
- encurtar_trecho: trecho, cortar_inicio_seg e/ou cortar_fim_seg (segundos a tirar)
- volume_musica: valor (0 a 100; o atual é informado)
- musica: ativar (true/false)
- transicao: tipo ({" | ".join(OPCOES["transicoes"])})
- legenda: tipo ({" | ".join(OPCOES["estilo_legenda"])})
- destacar: trecho, palavras (copiadas exatamente do texto do trecho)
- zoom: trecho, ativar
- abertura: tipo ({" | ".join(OPCOES["abertura"])}), texto
- encerramento: tipo ({" | ".join(OPCOES["encerramento"])}), texto
- efeitos_sonoros: ativar

Dicas: "começo mais impactante" → mova para a posição 1 o trecho mais forte (ou adicione um trecho forte disponível);
"menos cortes" → remova trechos curtos ou troque por trechos mais longos; "aumente o ritmo" → remova trechos fracos/lentos
e encurte trechos longos; "versão mais curta" → remova os trechos menos importantes até chegar perto da duração pedida;
"abaixe a música" → volume_musica menor que o atual (uns 40% menor).
Use apenas ids que existem. Em "entendimento", explique em uma frase, em português, o que será feito."""


def _linha_clipe(c: dict) -> str:
    extras = []
    if c.get("zoom", 1.0) != 1.0:
        extras.append("zoom")
    if c.get("destaques"):
        extras.append("destaques: " + ", ".join(c["destaques"]))
    return (f"{c['id']} [{plan._mmss(c['inicio'])}–{plan._mmss(c['fim'])} de {c['video_nome']}, {c['duracao']:.1f} s] "
            f"“{c['texto'] or '[imagem sem fala]'}”" + (f" ({'; '.join(extras)})" if extras else ""))


def _unidades_livres(plano: dict, unidades: list[dict]) -> list[dict]:
    usados = [(c.get("video_idx"), c["inicio"], c["fim"]) for c in plano["clipes"]]
    livres = []
    for u in unidades:
        if not any(v == u["video"] and min(b, u["fim"]) - max(a, u["inicio"]) > 0.3 for v, a, b in usados):
            livres.append(u)
    return livres


def montar_pedido_ajuste(pedido: str, plano: dict, unidades: list[dict], limite_texto: int, alvo: float) -> str:
    tr = plano["trilha"]
    leg = plano["legendas"]
    cab = [
        f"PEDIDO DO USUÁRIO: {pedido}", "",
        f"DURAÇÃO ATUAL: {plano['duracao_total']:.0f} s (desejada no projeto: {alvo:.0f} s)",
        f"MÚSICA: {'sim' if tr['usar'] else 'não'}, volume {tr['volume']}",
        f"TRANSIÇÃO: {plano['transicao']} · LEGENDA: {leg['estilo'] if leg['ativar'] else 'Sem legenda'}",
        f"ABERTURA: {plano['abertura']['tipo']} · ENCERRAMENTO: {plano['encerramento']['tipo']}",
        f"EFEITOS SONOROS: {'sim' if plano.get('efeitos_sonoros') else 'não'}", "",
        "PLANO ATUAL (na ordem do vídeo):", *[_linha_clipe(c) for c in plano["clipes"]], "",
        "TRECHOS DISPONÍVEIS (ainda não usados):",
    ]
    texto = "\n".join(cab)
    linhas = []
    for u in _unidades_livres(plano, unidades):
        ln = plan.linha_unidade(u)
        if len(texto) + sum(len(x) + 1 for x in linhas) + len(ln) > limite_texto:
            linhas.append("(lista cortada por tamanho)")
            break
        linhas.append(ln)
    return texto + "\n" + "\n".join(linhas or ["(nenhum)"])


def aplicar_operacoes(plano: dict, ops: list[dict], projeto: dict, analises: list[dict],
                      unidades: list[dict]) -> tuple[dict, list[str], list[str]]:
    """Retorna (novo_plano, feitas, avisos)."""
    p = copy.deepcopy(plano)
    feitas: list[str] = []
    avisos: list[str] = []
    por_uid = {u["id"]: u for u in unidades}
    padrao = projeto["padrao"]

    def achar(tid: str) -> dict | None:
        tid = (tid or "").strip().upper()
        return next((c for c in p["clipes"] if c["id"].upper() == tid), None)

    def novo_clipe(de: str, ate: str, ignorar: dict | None = None) -> dict | None:
        de, ate = (de or "").strip(), (ate or de or "").strip()
        if de not in por_uid or ate not in por_uid:
            avisos.append(f"Trecho inexistente ({de} → {ate}).")
            return None
        usados = [(c.get("video_idx"), c["inicio"], c["fim"]) for c in p["clipes"] if c is not ignorar]
        c, motivo = plan.criar_clipe(por_uid[de], por_uid[ate], analises, usados)
        if not c:
            avisos.append(f"Não foi possível usar {de} → {ate}: {motivo}.")
            return None
        c["trechos"] = [de, ate]
        c["motivo"] = "adicionado em ajuste"
        c["id"] = f"NOVO{len(feitas) + len(avisos) + 1}"   # renumerado no fim
        return c

    for op in ops:
        acao = op.get("acao")
        c = achar(op.get("trecho", "")) if op.get("trecho") else None
        precisa_trecho = acao in ("remover_trecho", "mover_trecho", "substituir_trecho", "encurtar_trecho", "zoom")
        if precisa_trecho and not c:
            avisos.append(f"Operação “{acao}” ignorada: trecho “{op.get('trecho')}” não existe.")
            continue
        if acao == "remover_trecho":
            if len(p["clipes"]) <= 1:
                avisos.append("Não dá para remover o único trecho do vídeo.")
                continue
            p["clipes"].remove(c)
            feitas.append(f"Removido {c['id']}")
        elif acao == "mover_trecho":
            pos = max(1, min(int(op.get("posicao") or 1), len(p["clipes"])))
            p["clipes"].remove(c)
            p["clipes"].insert(pos - 1, c)
            feitas.append(f"{c['id']} movido para a posição {pos}")
        elif acao == "adicionar_trecho":
            n = novo_clipe(op.get("de"), op.get("ate"))
            if n:
                pos = op.get("posicao") or len(p["clipes"]) + 1
                pos = max(1, min(int(pos), len(p["clipes"]) + 1))
                p["clipes"].insert(pos - 1, n)
                feitas.append(f"Adicionado trecho {n['trechos'][0]} na posição {pos}")
        elif acao == "substituir_trecho":
            n = novo_clipe(op.get("de"), op.get("ate"), ignorar=c)
            if n:
                n["id"] = c["id"]
                p["clipes"][p["clipes"].index(c)] = n
                feitas.append(f"{c['id']} substituído por {n['trechos'][0]}")
        elif acao == "encurtar_trecho":
            a = max(0.0, float(op.get("cortar_inicio_seg") or 0))
            b = max(0.0, float(op.get("cortar_fim_seg") or 0))
            ini, fim = c["inicio"] + a, c["fim"] - b
            if fim - ini < 0.8:
                avisos.append(f"{c['id']} ficaria curto demais; não foi encurtado.")
                continue
            an = analises[(c.get("video_idx") or 1) - 1]
            novo = plan.montar_clipe(an, c.get("video_idx") or 1, ini, fim)
            novo.update({k: c[k] for k in ("id", "trechos", "motivo", "zoom") if k in c})
            novo["destaques"] = plan.filtrar_destaques(c.get("destaques"), novo["texto"])
            p["clipes"][p["clipes"].index(c)] = novo
            feitas.append(f"{c['id']} encurtado ({a + b:.1f} s)")
        elif acao == "volume_musica":
            v = int(max(0, min(100, float(op.get("valor") if op.get("valor") is not None else p["trilha"]["volume"]))))
            p["trilha"]["volume"] = v
            feitas.append(f"Volume da música: {v}%")
        elif acao == "musica":
            p["trilha"]["usar"] = bool(op.get("ativar", True))
            if p["trilha"]["usar"] and not p["trilha"].get("arquivo"):
                p["trilha"]["arquivo"] = plan._escolher_arquivo(padrao.get("biblioteca", {}).get("trilhas", []),
                                                                {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"})
                if not p["trilha"]["arquivo"]:
                    avisos.append("Não há trilha na biblioteca do padrão; adicione em Biblioteca.")
            feitas.append("Música ligada" if p["trilha"]["usar"] else "Música desligada")
        elif acao == "transicao":
            if op.get("tipo") in OPCOES["transicoes"]:
                p["transicao"] = op["tipo"]
                feitas.append(f"Transições: {op['tipo']}")
            else:
                avisos.append(f"Transição desconhecida: {op.get('tipo')}")
        elif acao == "legenda":
            if op.get("tipo") in OPCOES["estilo_legenda"]:
                p["legendas"]["estilo"] = op["tipo"]
                p["legendas"]["ativar"] = op["tipo"] != "Sem legenda"
                feitas.append(f"Legenda: {op['tipo']}")
            else:
                avisos.append(f"Estilo de legenda desconhecido: {op.get('tipo')}")
        elif acao == "destacar":
            palavras = op.get("palavras") or ([op["texto"]] if op.get("texto") else [])
            alvos = [c] if c else [x for x in p["clipes"] if any(w.lower() in x["texto"].lower() for w in palavras)]
            ok = False
            for x in alvos:
                novos = plan.filtrar_destaques(list(x.get("destaques", [])) + list(palavras), x["texto"])
                if novos != x.get("destaques"):
                    x["destaques"] = novos
                    ok = True
                    feitas.append(f"Destaque em {x['id']}: {', '.join(novos)}")
            if not ok:
                avisos.append(f"Não encontrei “{', '.join(palavras)}” nas falas do plano para destacar.")
        elif acao == "zoom":
            c["zoom"] = 1.1 if op.get("ativar", True) else 1.0
            c["zoom_manual"] = True
            feitas.append(f"Zoom {'ativado' if c['zoom'] != 1.0 else 'removido'} em {c['id']}")
        elif acao in ("abertura", "encerramento"):
            p[acao] = plan.resolver_elemento(acao, op.get("tipo", ""), op.get("texto", ""), padrao, avisos)
            feitas.append(f"{acao.capitalize()}: {p[acao]['tipo']}")
        elif acao == "efeitos_sonoros":
            p["efeitos_sonoros"] = bool(op.get("ativar", True))
            feitas.append("Efeitos sonoros " + ("ligados" if p["efeitos_sonoros"] else "desligados"))
        else:
            avisos.append(f"Operação desconhecida: {acao}")

    for i, c in enumerate(p["clipes"], start=1):
        c["id"] = f"T{i}"
    plan.aplicar_zooms(p["clipes"])
    p["duracao_total"] = round(sum(c["duracao"] for c in p["clipes"]), 2)
    return p, feitas, avisos


def ajustar(pedido: str, plano: dict, projeto: dict, analises: list[dict], provedor) -> dict:
    """Pede à IA as operações e devolve o plano ajustado (com resumo e avisos do ajuste)."""
    unidades = plan.montar_unidades(analises)
    texto = montar_pedido_ajuste(pedido, plano, unidades, provedor.limite_texto,
                                 float(projeto.get("duracao_seg", 60)))
    resp = provedor.gerar_json(SISTEMA_AJUSTE, texto, SCHEMA_AJUSTE)
    ops = resp.get("operacoes") or []
    novo, feitas, avisos = aplicar_operacoes(plano, ops, projeto, analises, unidades)
    if not feitas:
        raise plan.PlanoErro("A IA não conseguiu transformar o pedido em uma alteração válida. "
                             "Tente ser mais específico (ex.: “remova o trecho T3”, “abaixe a música”). "
                             + (" ".join(avisos) if avisos else ""))
    novo["ultimo_ajuste"] = {"pedido": pedido, "entendimento": str(resp.get("entendimento", "")).strip(),
                             "feitas": feitas, "avisos": avisos}
    return novo


# ------------------------------------------------------------------ análises guardadas por projeto
def salvar_analises(caminho: Path, analises: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(analises, ensure_ascii=False), encoding="utf-8")


def carregar_analises(caminho: Path) -> list[dict] | None:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
