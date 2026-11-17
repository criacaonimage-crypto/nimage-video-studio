"""Provedores de IA. A IA local (Ollama) é o padrão; Claude é opcional.

Todo provedor expõe: nome, limite_texto (caracteres) e gerar_json(sistema, usuario, schema) -> dict.
"""
from __future__ import annotations

from .claude import Claude, ClaudeErro, MODELO_PADRAO as CLAUDE_MODELO_PADRAO
from .ollama import Ollama, OllamaErro, contexto_para, recomendado_para


class ProvedorErro(Exception):
    pass


class ProvedorOllama:
    def __init__(self, url: str, modelo: str, vram_mb: int):
        self.cliente = Ollama(url)
        self.modelo = modelo
        self.contexto = contexto_para(vram_mb)
        # ~3,5 caracteres por token em português; reserva espaço para instruções e resposta.
        self.limite_texto = int((self.contexto - 3000) * 3.5)
        self.nome = f"IA local · {modelo}"

    def gerar_json(self, sistema: str, usuario: str, schema: dict) -> dict:
        try:
            return self.cliente.chat_json(self.modelo, sistema, usuario, schema, contexto=self.contexto)
        except OllamaErro as e:
            raise ProvedorErro(str(e)) from e


class ProvedorClaude:
    def __init__(self, chave: str, modelo: str):
        self.cliente = Claude(chave, modelo)
        self.limite_texto = 400_000
        self.nome = f"Claude · {self.cliente.modelo}"

    def gerar_json(self, sistema: str, usuario: str, schema: dict) -> dict:
        try:
            return self.cliente.gerar_json(sistema, usuario, schema)
        except ClaudeErro as e:
            raise ProvedorErro(f"{e}\n\nVocê pode voltar para a IA local em Configurações.") from e


def config_ia(db) -> dict:
    """Lê do banco as configurações de IA (passadas às tarefas em segundo plano)."""
    return {"modo": db.get("ia_modo") or "local", "ollama_url": db.get("ollama_url") or "",
            "ollama_modelo": db.get("ollama_modelo") or "", "claude_chave": db.get("claude_chave") or "",
            "claude_modelo": db.get("claude_modelo") or CLAUDE_MODELO_PADRAO}


def provedor_de(cfg: dict, vram_mb: int):
    if cfg.get("modo") == "claude":
        return ProvedorClaude(cfg.get("claude_chave", ""), cfg.get("claude_modelo", ""))
    return ProvedorOllama(cfg.get("ollama_url", ""), cfg.get("ollama_modelo") or recomendado_para(vram_mb)["modelo"],
                          vram_mb)
