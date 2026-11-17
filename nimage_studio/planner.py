"""Orquestra análise + planejamento de um projeto (roda em segundo plano na interface)."""
from __future__ import annotations

from typing import Callable

from . import analysis, plan

# (etapa, mensagem, fração geral 0..1)
ProgressoGeral = Callable[[str, str, float], None]


def analisar_projeto(projeto: dict, whisper_pref: str, vram_mb: int, progresso: ProgressoGeral,
                     cancelado: Callable[[], bool] = lambda: False, peso: float = 0.75) -> list[dict]:
    videos = projeto.get("videos", [])
    if not videos:
        raise plan.PlanoErro("O projeto não tem vídeos.")
    analises = []
    for i, info in enumerate(videos):
        base = i / len(videos)

        def p(msg, frac, i=i, base=base):
            progresso("Análise", f"Vídeo {i + 1} de {len(videos)} · {msg}", (base + frac / len(videos)) * peso)

        analises.append(analysis.analisar_video(info, whisper_pref, vram_mb, p, cancelado))
        if cancelado():
            raise analysis.Cancelado()
    return analises


def planejar(projeto: dict, analises: list[dict], provedor, progresso: ProgressoGeral,
             cancelado: Callable[[], bool] = lambda: False, inicio: float = 0.75) -> dict:
    unidades = plan.montar_unidades(analises)
    if not unidades:
        raise plan.PlanoErro("Não há conteúdo aproveitável nos vídeos.")
    resto = 1.0 - inicio

    def p_pre(msg, frac):
        progresso("Planejamento", msg, inicio + resto * 0.4 * frac)

    selecionadas = plan.preselecionar(unidades, projeto, provedor.gerar_json, provedor.limite_texto, p_pre)
    if cancelado():
        raise analysis.Cancelado()

    erro = ""
    for tentativa in range(2):
        progresso("Planejamento", f"A IA ({provedor.nome}) está montando o plano de edição"
                  + (" — segunda tentativa…" if tentativa else "…"), inicio + resto * (0.45 + 0.25 * tentativa))
        pedido = plan.montar_pedido(projeto, analises, selecionadas, erro)
        resp = provedor.gerar_json(plan.SISTEMA, pedido, plan.SCHEMA_PLANO)
        if cancelado():
            raise analysis.Cancelado()
        try:
            plano = plan.normalizar(resp, projeto, analises, selecionadas, provedor.nome)
            progresso("Planejamento", "Plano validado.", 1.0)
            return plano
        except plan.PlanoErro as e:
            erro = str(e)
    raise plan.PlanoErro(f"A IA não produziu um plano válido após 2 tentativas ({erro}). "
                         "Tente reescrever as instruções ou usar um modelo maior em Configurações.")
