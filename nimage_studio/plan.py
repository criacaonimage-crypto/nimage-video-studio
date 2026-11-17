"""ETAPA 4 — Planejamento: monta o pedido para a IA, define o formato da resposta e valida o plano.

A IA não escreve tempos diretamente: ela escolhe trechos numerados (ex.: V1-F012). O programa converte
para segundos, corrige erros e só então o plano fica pronto. Isso deixa modelos locais pequenos confiáveis.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from .patterns import OPCOES

PLANO_VERSAO = 1
EXT_VIDEO_VINHETA = {".mov", ".mp4", ".webm", ".mkv", ".avi"}
EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".bmp"}

DURACAO_CLIPE = {  # sugestão de duração de cada trecho por ritmo (segundos)
    "Lento": "8 a 20", "Moderado": "5 a 12", "Rápido": "3 a 8", "Muito rápido": "2 a 5",
}


class PlanoErro(Exception):
    pass


def _mmss(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------------ unidades (trechos numerados)
def montar_unidades(analises: list[dict]) -> list[dict]:
    """Converte transcrição + cenas em trechos numerados que a IA pode escolher."""
    unidades: list[dict] = []
    for vi, an in enumerate(analises, start=1):
        dur = float(an["info"].get("duracao") or 0)
        segs = []
        for s in an.get("transcricao", []):
            if segs and (s["fim"] - s["inicio"] < 1.2) and s["inicio"] - segs[-1]["fim"] < 0.6:
                segs[-1] = {**segs[-1], "fim": s["fim"], "texto": segs[-1]["texto"] + " " + s["texto"]}
            else:
                segs.append(dict(s))
        # trechos de fala
        locais = [{"tipo": "fala", "inicio": s["inicio"], "fim": s["fim"], "texto": s["texto"]} for s in segs]
        # trechos sem fala (b-roll, plateia, vinhetas): cenas que caem fora das falas
        ocupado = sorted((s["inicio"], s["fim"]) for s in segs)
        for c in an.get("cenas", []):
            ini = c["inicio"]
            for a, b in ocupado + [(c["fim"], c["fim"])]:
                if b <= ini:
                    continue
                fim_livre = min(a, c["fim"])
                if fim_livre - ini >= 2.5:
                    locais.append({"tipo": "imagem", "inicio": ini, "fim": fim_livre,
                                   "texto": f"[imagem sem fala, {fim_livre - ini:.0f} s]"})
                ini = max(ini, b)
                if ini >= c["fim"]:
                    break
        if not locais and dur:
            locais.append({"tipo": "imagem", "inicio": 0.0, "fim": dur, "texto": f"[vídeo sem fala, {dur:.0f} s]"})
        locais.sort(key=lambda u: u["inicio"])
        nf = ni = 0
        for u in locais:
            if u["tipo"] == "fala":
                nf += 1; uid = f"V{vi}-F{nf:03d}"
            else:
                ni += 1; uid = f"V{vi}-C{ni:03d}"
            unidades.append({**u, "id": uid, "video": vi})
    return unidades


def linha_unidade(u: dict) -> str:
    return f"{u['id']} [{_mmss(u['inicio'])}–{_mmss(u['fim'])}] {u['texto']}"


# ------------------------------------------------------------------ schemas da resposta
SCHEMA_PRESELECAO = {
    "type": "object",
    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["ids"],
}

SCHEMA_PLANO = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "clipes": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "de": {"type": "string"},
                    "ate": {"type": "string"},
                    "motivo": {"type": "string"},
                    "destaques": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["de", "ate", "motivo", "destaques"],
            },
        },
        "abertura": {"type": "object", "properties": {"tipo": {"type": "string", "enum": OPCOES["abertura"]},
                                                      "texto": {"type": "string"}}, "required": ["tipo", "texto"]},
        "encerramento": {"type": "object", "properties": {"tipo": {"type": "string", "enum": OPCOES["encerramento"]},
                                                          "texto": {"type": "string"}}, "required": ["tipo", "texto"]},
        "trilha": {"type": "object", "properties": {"usar": {"type": "boolean"},
                                                    "tom": {"type": "string", "enum": OPCOES["tom_musica"]},
                                                    "volume": {"type": "integer"}},
                   "required": ["usar", "tom", "volume"]},
        "transicao": {"type": "string", "enum": OPCOES["transicoes"]},
        "justificativa": {"type": "string"},
    },
    "required": ["titulo", "clipes", "abertura", "encerramento", "trilha", "transicao", "justificativa"],
}

SISTEMA = """Você é um editor de vídeo profissional da agência Nimage. Você planeja edições a partir da \
transcrição dos vídeos, que está dividida em trechos numerados.

Regras obrigatórias:
- Use SOMENTE ids que aparecem na lista de TRECHOS. Nunca invente falas nem ids.
- Cada clipe vai do trecho "de" até o trecho "ate" (inclusive), do MESMO vídeo (mesmo prefixo V1, V2…), com "ate" igual ou depois de "de".
- Clipes não podem repetir o mesmo trecho.
- A soma das durações dos clipes deve ficar próxima da DURAÇÃO DESEJADA.
- Ordene os clipes na sequência em que devem aparecer no vídeo final (pode mudar a ordem original para contar melhor a história).
- Corte frases incompletas, repetições, gaguejos, silêncios e falas fora do assunto.
- Siga o padrão audiovisual, as regras e as instruções do usuário. Restrições do padrão nunca podem ser violadas.
- "destaques": de 0 a 3 palavras ou expressões curtas copiadas EXATAMENTE do texto do clipe, para destacar na legenda.
- "motivo": uma frase curta explicando por que o clipe entrou.
- Escreva tudo em português do Brasil."""


def _bloco_padrao(p: dict, ajustes: dict) -> str:
    e, a, r = p["edicao"], p["audio"], p["regras"]
    linhas = [
        f"Nome: {p['nome']}" + (f" — {p['descricao']}" if p.get("descricao") else ""),
        f"Ritmo: {ajustes.get('ritmo', e['ritmo'])} (clipes de {DURACAO_CLIPE.get(ajustes.get('ritmo', e['ritmo']), '5 a 12')} s, em geral)",
        f"Quantidade de cortes: {e['cortes']}",
        f"Tom / intensidade: {ajustes.get('intensidade', e['intensidade'])}",
        f"Transições: {e['transicoes']}",
        f"Abertura preferida: {e['abertura']}",
        f"Encerramento preferido: {e['encerramento']}",
        f"Música: {ajustes.get('tom_musica', a['tom_musica'])}",
    ]
    for chave, titulo in (("obrigatorios", "Elementos obrigatórios"), ("preferencias", "Preferências"),
                          ("opcionais", "Opcionais"), ("restricoes", "RESTRIÇÕES")):
        if r.get(chave):
            linhas.append(f"{titulo}: {r[chave]}")
    return "\n".join(linhas)


def montar_pedido(projeto: dict, analises: list[dict], unidades: list[dict], erro_anterior: str = "") -> str:
    p = projeto["padrao"]
    aj = projeto.get("ajustes", {})
    fmt = projeto.get("formato", {})
    videos = "\n".join(f"V{i}: {an['info']['nome']} ({_mmss(an['info']['duracao'])}, "
                       f"{'com fala' if an.get('transcricao') else 'sem fala'})"
                       for i, an in enumerate(analises, start=1))
    partes = [
        f"OBJETIVO DO VÍDEO: {projeto.get('objetivo') or 'não informado'}",
        f"DURAÇÃO DESEJADA: {projeto.get('duracao_seg', 60)} segundos",
        f"FORMATO: {fmt.get('largura')}×{fmt.get('altura')}",
        "", "PADRÃO AUDIOVISUAL:", _bloco_padrao(p, aj),
        "", "INSTRUÇÕES DO USUÁRIO:", projeto.get("instrucoes") or "(nenhuma instrução adicional)",
        "", "VÍDEOS:", videos,
        "", "TRECHOS (id [início–fim] texto):", "\n".join(linha_unidade(u) for u in unidades),
    ]
    if erro_anterior:
        partes += ["", f"ATENÇÃO — sua resposta anterior foi recusada: {erro_anterior}. Corrija e responda de novo."]
    return "\n".join(partes)


# ------------------------------------------------------------------ pré-seleção (vídeos longos)
def preselecionar(unidades: list[dict], projeto: dict, gerar_json, limite_texto: int,
                  progresso: Callable[[str, float], None]) -> list[dict]:
    """Se a transcrição não cabe de uma vez na IA, escolhe os melhores trechos por blocos."""
    total = sum(len(linha_unidade(u)) + 1 for u in unidades)
    if total <= limite_texto:
        return unidades
    blocos, atual, tam = [], [], 0
    lim_bloco = max(4000, limite_texto - 2500)
    for u in unidades:
        t = len(linha_unidade(u)) + 1
        if atual and tam + t > lim_bloco:
            blocos.append(atual); atual, tam = [], 0
        atual.append(u); tam += t
    if atual:
        blocos.append(atual)
    alvo = float(projeto.get("duracao_seg", 60))
    dur_media = max(2.0, sum(u["fim"] - u["inicio"] for u in unidades) / len(unidades))
    por_bloco = max(4, int(alvo / dur_media * 3 / len(blocos)) + 2)
    validos = {u["id"] for u in unidades}
    escolhidos: set[str] = set()
    for i, bloco in enumerate(blocos):
        progresso(f"Lendo a transcrição em partes ({i + 1} de {len(blocos)})…", i / len(blocos))
        pedido = (f"OBJETIVO: {projeto.get('objetivo') or 'não informado'}\n"
                  f"INSTRUÇÕES: {projeto.get('instrucoes') or '(nenhuma)'}\n"
                  f"Escolha até {por_bloco} ids dos trechos mais fortes e relevantes para esse objetivo.\n\n"
                  "TRECHOS:\n" + "\n".join(linha_unidade(u) for u in bloco))
        resp = gerar_json("Você seleciona os melhores trechos de uma transcrição para uma edição de vídeo. "
                          "Responda só com ids existentes.", pedido, SCHEMA_PRESELECAO)
        escolhidos |= {x for x in resp.get("ids", []) if x in validos}
    # inclui o trecho seguinte de cada escolhido, para a IA poder completar frases
    idx = {u["id"]: n for n, u in enumerate(unidades)}
    extra = {unidades[idx[x] + 1]["id"] for x in escolhidos if idx[x] + 1 < len(unidades)
             and unidades[idx[x] + 1]["video"] == unidades[idx[x]]["video"]}
    sel = [u for u in unidades if u["id"] in escolhidos | extra]
    if not sel:
        raise PlanoErro("A IA não conseguiu selecionar trechos da transcrição.")
    # se ainda não couber, corta mantendo a ordem
    while sum(len(linha_unidade(u)) + 1 for u in sel) > limite_texto and len(sel) > 5:
        sel = sel[::2] if len(sel) > 10 else sel[:-1]
    return sel


# ------------------------------------------------------------------ validação e normalização
def _texto_intervalo(analise: dict, ini: float, fim: float) -> str:
    return " ".join(s["texto"] for s in analise.get("transcricao", []) if s["fim"] > ini + 0.05 and s["inicio"] < fim - 0.05)


def _palavras_intervalo(analise: dict, ini: float, fim: float) -> list[dict]:
    out = []
    for s in analise.get("transcricao", []):
        if s["fim"] <= ini or s["inicio"] >= fim:
            continue
        for w in s.get("palavras") or []:
            if w["f"] > ini + 0.02 and w["i"] < fim - 0.02:
                out.append({"p": w["p"], "i": round(max(w["i"], ini), 2), "f": round(min(w["f"], fim), 2)})
        if not s.get("palavras"):  # sem tempo por palavra: distribui no segmento
            ps = s["texto"].split()
            d = (s["fim"] - s["inicio"]) / max(len(ps), 1)
            for k, p in enumerate(ps):
                a, b = s["inicio"] + k * d, s["inicio"] + (k + 1) * d
                if b > ini and a < fim:
                    out.append({"p": p, "i": round(max(a, ini), 2), "f": round(min(b, fim), 2)})
    return out


def aplicar_zooms(clipes: list[dict]) -> None:
    """Zoom alternado em cortes seguidos do mesmo vídeo (disfarça o "pulo" do corte)."""
    anterior = None
    for c in clipes:
        if c.get("zoom_manual"):
            anterior = c
            continue
        c["zoom"] = 1.1 if (anterior and anterior["video"] == c["video"] and anterior.get("zoom", 1.0) == 1.0) else 1.0
        anterior = c


def _escolher_arquivo(lista: list[str], exts: set[str]) -> str | None:
    for c in lista:
        if Path(c).suffix.lower() in exts and Path(c).exists():
            return c
    return None


def filtrar_destaques(lista, texto: str) -> list[str]:
    out = []
    for d in lista or []:
        d = str(d).strip()
        if d and d.lower() in texto.lower() and d not in out:
            out.append(d)
    return out[:3]


def criar_clipe(u1: dict, u2: dict, analises: list[dict], usados: list[tuple[int, float, float]]) -> tuple[dict | None, str]:
    """Clipe entre duas unidades do mesmo vídeo. `usados` evita repetir trechos (e é atualizado)."""
    if u1["video"] != u2["video"]:
        return None, "começava e terminava em vídeos diferentes"
    if u2["inicio"] < u1["inicio"]:
        u1, u2 = u2, u1
    an = analises[u1["video"] - 1]
    dur_video = float(an["info"].get("duracao") or u2["fim"])
    ini = max(0.0, u1["inicio"] - 0.12)
    fim = min(dur_video, u2["fim"] + 0.20)
    if fim - ini < 0.5:
        return None, "curto demais"
    for v, a, b in usados:
        sob = min(b, fim) - max(a, ini)
        if v == u1["video"] and sob > 0.5 * min(b - a, fim - ini):
            return None, "repetia um trecho já usado"
    usados.append((u1["video"], ini, fim))
    return montar_clipe(an, u1["video"], ini, fim), ""


def montar_clipe(an: dict, video_idx: int, ini: float, fim: float) -> dict:
    return {"video": an["info"]["caminho"], "video_nome": an["info"]["nome"], "video_idx": video_idx,
            "inicio": round(ini, 2), "fim": round(fim, 2), "duracao": round(fim - ini, 2),
            "texto": _texto_intervalo(an, ini, fim), "palavras": _palavras_intervalo(an, ini, fim),
            "destaques": [], "motivo": "", "zoom": 1.0}


def resolver_elemento(chave: str, tipo: str, texto: str, padrao: dict, avisos: list[str]) -> dict:
    """Abertura/encerramento com o arquivo certo da biblioteca do padrão."""
    bib = padrao.get("biblioteca", {})
    tipo = tipo if tipo in OPCOES[chave] else padrao["edicao"][chave]
    item = {"tipo": tipo, "texto": (texto or "").strip(), "arquivo": None}
    nome = "Abertura" if chave == "abertura" else "Encerramento"
    if tipo == "Vinheta":
        item["arquivo"] = _escolher_arquivo(bib.get("logos", []), EXT_VIDEO_VINHETA)
        if not item["arquivo"]:
            avisos.append(f"{nome} pede vinheta, mas não há vinheta em vídeo na biblioteca (Logos) do padrão.")
    elif tipo == "Logo":
        item["arquivo"] = _escolher_arquivo(bib.get("logos", []), EXT_IMAGEM)
        if not item["arquivo"]:
            avisos.append(f"{nome} pede logo, mas não há imagem de logo na biblioteca do padrão.")
    elif tipo == "Chamada para ação" and not item["texto"]:
        item["texto"] = "Siga para mais conteúdos"
    return item


def normalizar(resp: dict, projeto: dict, analises: list[dict], unidades: list[dict], modelo: str) -> dict:
    """Valida a resposta da IA e produz o plano final, com tempos em segundos. Levanta PlanoErro se inutilizável."""
    por_id = {u["id"]: u for u in unidades}
    avisos: list[str] = []
    clipes: list[dict] = []
    usados: list[tuple[int, float, float]] = []

    for n, c in enumerate(resp.get("clipes") or [], start=1):
        de, ate = str(c.get("de", "")).strip(), str(c.get("ate", "")).strip() or str(c.get("de", "")).strip()
        if de not in por_id or ate not in por_id:
            avisos.append(f"Clipe {n} ignorado: trecho inexistente ({de} → {ate}).")
            continue
        u1, u2 = por_id[de], por_id[ate]
        if u1["video"] != u2["video"]:
            avisos.append(f"Clipe {n} ignorado: começava e terminava em vídeos diferentes.")
            continue
        if u2["inicio"] < u1["inicio"]:
            u1, u2 = u2, u1
        clipe, motivo_recusa = criar_clipe(u1, u2, analises, usados)
        if not clipe:
            avisos.append(f"Clipe {n} ignorado: {motivo_recusa}.")
            continue
        clipe.update({"id": f"T{len(clipes) + 1}", "trechos": [de, ate], "motivo": str(c.get("motivo", "")).strip()})
        clipe["destaques"] = filtrar_destaques(c.get("destaques"), clipe["texto"])
        clipes.append(clipe)

    if not clipes:
        raise PlanoErro("nenhum clipe válido (use apenas ids da lista de TRECHOS, ex.: "
                        + ", ".join(u["id"] for u in unidades[:3]) + ")")

    alvo = float(projeto.get("duracao_seg", 60))
    total = sum(c["duracao"] for c in clipes)
    if total > alvo * 1.25 and len(clipes) > 1:
        while len(clipes) > 1 and sum(c["duracao"] for c in clipes) > alvo * 1.15:
            removido = clipes.pop(-2 if len(clipes) > 2 else -1)
            avisos.append(f"Clipe “{removido['texto'][:40]}…” removido para aproximar da duração desejada.")
        for i, c in enumerate(clipes, start=1):
            c["id"] = f"T{i}"
        total = sum(c["duracao"] for c in clipes)
    if total < alvo * 0.6:
        avisos.append(f"O plano tem {total:.0f} s, abaixo dos {alvo:.0f} s desejados "
                      "(pouco material relevante ou a IA escolheu pouco).")

    aplicar_zooms(clipes)
    p = projeto["padrao"]
    aj = projeto.get("ajustes", {})
    bib = p.get("biblioteca", {})

    # trilha
    tr = resp.get("trilha") or {}
    tom = aj.get("tom_musica") or tr.get("tom") or p["audio"]["tom_musica"]
    usar_trilha = tom != "Sem música" and bool(tr.get("usar", True))
    arq_trilha = _escolher_arquivo(bib.get("trilhas", []), {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"})
    if usar_trilha and not arq_trilha:
        avisos.append("Nenhuma trilha na biblioteca do padrão: o vídeo sairá sem música até vocês adicionarem trilhas.")
    volume = int(tr.get("volume") or p["audio"]["volume_musica"])
    volume = max(0, min(100, volume))

    def elemento(chave: str) -> dict:
        e = resp.get(chave) or {}
        return resolver_elemento(chave, e.get("tipo", ""), str(e.get("texto", "")), p, avisos)

    leg = p["identidade"]["legenda"]
    estilo_leg = aj.get("estilo_legenda") or leg["estilo"]
    transicao = resp.get("transicao") if resp.get("transicao") in OPCOES["transicoes"] else p["edicao"]["transicoes"]

    return {
        "versao": PLANO_VERSAO,
        "criado": datetime.now().isoformat(timespec="seconds"),
        "ia": modelo,
        "titulo": str(resp.get("titulo", "")).strip() or projeto.get("objetivo", "Edição"),
        "justificativa": str(resp.get("justificativa", "")).strip(),
        "formato": projeto.get("formato", {}),
        "padrao": p["nome"],
        "clipes": clipes,
        "duracao_total": round(sum(c["duracao"] for c in clipes), 2),
        "transicao": transicao,
        "abertura": elemento("abertura"),
        "encerramento": elemento("encerramento"),
        "trilha": {"usar": usar_trilha, "tom": tom, "volume": volume, "arquivo": arq_trilha,
                   "abaixar_na_fala": p["audio"]["abaixar_musica_na_fala"]},
        "legendas": {"ativar": estilo_leg != "Sem legenda", "estilo": estilo_leg, "posicao": leg["posicao"],
                     "tamanho": leg["tamanho"], "cor": leg["cor"], "cor_destaque": leg["cor_destaque"],
                     "caixa_alta_destaque": leg["caixa_alta_destaque"],
                     "fonte": p["identidade"]["fonte_texto"] or p["identidade"]["fonte_titulo"]},
        "efeitos_sonoros": p["audio"]["efeitos_sonoros"],
        "arquivos_sfx": [c for c in bib.get("efeitos_sonoros", []) if Path(c).exists()],
        "identidade": {"fonte_titulo": p["identidade"]["fonte_titulo"], "fonte_texto": p["identidade"]["fonte_texto"],
                       "cores": p["identidade"]["cores"], "tamanho_titulo": p["identidade"]["tamanho_titulo"]},
        "enquadramento": "rosto",
        "avisos": avisos,
        "aprovado": False,
    }


def resumo_texto(plano: dict) -> str:
    """Texto curto do plano (para logs e testes)."""
    linhas = [f"{plano['titulo']} — {plano['duracao_total']:.0f} s, {len(plano['clipes'])} clipes"]
    for c in plano["clipes"]:
        linhas.append(f"{c['id']} {c['video_nome']} {_mmss(c['inicio'])}–{_mmss(c['fim'])}: {c['texto'][:60]}")
    return "\n".join(linhas)


def sanitizar_nome(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", s).strip() or "video"


def hash_plano(plano: dict | None) -> str | None:
    if not plano:
        return None
    import hashlib
    import json as _json
    base = {k: v for k, v in plano.items() if k not in ("ultimo_ajuste", "aprovado", "_pode_desfazer", "criado")}
    return hashlib.sha1(_json.dumps(base, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
