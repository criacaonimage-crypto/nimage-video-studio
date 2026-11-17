"""Integração opcional com Claude (API da Anthropic). Só envia texto (transcrição e dados dos trechos).

Os vídeos nunca são enviados. A resposta estruturada é obtida forçando o uso de uma "ferramenta"
cujo esquema é o mesmo usado pela IA local.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODELO_PADRAO = "claude-sonnet-5-5"


class ClaudeErro(Exception):
    pass


class Claude:
    def __init__(self, chave: str, modelo: str = MODELO_PADRAO, timeout: int = 300):
        self.chave = (chave or "").strip()
        self.modelo = (modelo or MODELO_PADRAO).strip()
        self.timeout = timeout

    def _post(self, corpo: dict) -> dict:
        if not self.chave:
            raise ClaudeErro("Informe a chave de API da Anthropic em Configurações.")
        req = urllib.request.Request(API_URL, data=json.dumps(corpo).encode(), method="POST", headers={
            "x-api-key": self.chave, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            corpo_erro = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(corpo_erro).get("error", {}).get("message", corpo_erro)
            except json.JSONDecodeError:
                msg = corpo_erro
            dicas = {401: "chave de API inválida", 403: "a chave não tem permissão", 404: "modelo não encontrado",
                     429: "limite de uso atingido", 529: "serviço do Claude sobrecarregado; tente de novo"}
            raise ClaudeErro(f"Claude respondeu erro {e.code} ({dicas.get(e.code, 'erro')}): {msg[:300]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ClaudeErro(f"Sem conexão com o Claude (verifique a internet): {e}") from e

    def gerar_json(self, sistema: str, usuario: str, schema: dict) -> dict:
        resp = self._post({
            "model": self.modelo,
            "max_tokens": 8000,
            "system": sistema,
            "messages": [{"role": "user", "content": usuario}],
            "tools": [{"name": "responder", "description": "Entregar a resposta no formato exigido.",
                       "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "responder"},
        })
        for bloco in resp.get("content", []):
            if bloco.get("type") == "tool_use":
                return bloco.get("input", {})
        raise ClaudeErro("O Claude não devolveu uma resposta estruturada.")

    def testar(self) -> str:
        r = self.gerar_json("Responda apenas com o campo pedido.", "Diga ok.",
                            {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]})
        return str(r.get("ok", "ok"))
