"""Estrutura dos padrões audiovisuais e opções de cada campo.

Um padrão é um dicionário JSON. Esta é a única definição da estrutura:
a interface, o banco e (nas próximas entregas) a IA e o motor de edição usam estas chaves.
"""
from __future__ import annotations

import copy
import hashlib
import json

OPCOES = {
    "ritmo": ["Lento", "Moderado", "Rápido", "Muito rápido"],
    "cortes": ["Poucos", "Moderados", "Muitos"],
    "transicoes": ["Corte seco", "Suaves", "Dinâmicas"],
    "intensidade": ["Calma", "Equilibrada", "Enérgica", "Épica"],
    "abertura": ["Direto ao ponto", "Frase de impacto", "Vinheta", "Título"],
    "encerramento": ["Sem encerramento", "Logo", "Vinheta", "Chamada para ação"],
    "tom_musica": ["Sem música", "Suave", "Inspiradora", "Corporativa", "Enérgica", "Épica"],
    "estilo_legenda": ["Sem legenda", "Simples", "Palavra-chave em destaque", "Palavra por palavra"],
    "posicao_legenda": ["Abaixo do queixo", "Inferior", "Centro", "Superior"],
}

# Atalhos de formato. O usuário também pode digitar qualquer largura x altura.
FORMATOS = {
    "Reels / Stories (1080×1920)": (1080, 1920),
    "Horizontal Full HD (1920×1080)": (1920, 1080),
    "Post vertical Instagram (1080×1440)": (1080, 1440),
    "Quadrado (1080×1080)": (1080, 1080),
    "Horizontal 4K (3840×2160)": (3840, 2160),
}

BIBLIOTECA_CATEGORIAS = {
    "videos_apoio": "Vídeos de apoio",
    "imagens": "Imagens",
    "trilhas": "Trilhas",
    "efeitos_sonoros": "Efeitos sonoros",
    "logos": "Logos",
    "referencias": "Referências de edição",
}


def padrao_vazio(nome: str = "Novo padrão") -> dict:
    return {
        "nome": nome,
        "descricao": "",
        "formato_padrao": {"largura": 1920, "altura": 1080},
        "identidade": {
            "fonte_titulo": "",
            "fonte_texto": "",
            "cores": {"principal": "#FFFFFF", "secundaria": "#000000", "fundo": "#000000",
                      "texto": "#FFFFFF", "destaque": "#FFFFFF"},
            "tamanho_titulo": 72,
            "elementos_graficos": "",
            "legenda": {"estilo": "Palavra-chave em destaque", "posicao": "Abaixo do queixo",
                        "tamanho": 54, "cor": "#FFFFFF", "cor_destaque": "#FFFFFF",
                        "caixa_alta_destaque": True},
        },
        "edicao": {"ritmo": "Moderado", "cortes": "Moderados", "duracao_alvo_seg": 60,
                   "transicoes": "Corte seco", "abertura": "Direto ao ponto",
                   "encerramento": "Logo", "intensidade": "Equilibrada"},
        "audio": {"tom_musica": "Inspiradora", "volume_musica": 20, "abaixar_musica_na_fala": True,
                  "efeitos_sonoros": False},
        "biblioteca": {k: [] for k in BIBLIOTECA_CATEGORIAS},
        "regras": {"obrigatorios": "", "preferencias": "", "opcionais": "", "restricoes": "",
                   "exemplos_aprovados": []},
    }


def completar(padrao: dict) -> dict:
    """Garante que o padrão tenha todas as chaves (útil quando a estrutura evolui)."""
    base = padrao_vazio(padrao.get("nome", "Padrão"))

    def merge(dst: dict, src: dict) -> dict:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = v
        return dst

    return merge(base, copy.deepcopy(padrao))


def hash_padrao(padrao: dict) -> str:
    return hashlib.sha256(json.dumps(padrao, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
