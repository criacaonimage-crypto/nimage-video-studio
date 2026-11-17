"""Cliente do Ollama (IA local) usando só a biblioteca padrão do Python."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

URL_PADRAO = "http://127.0.0.1:11434"

# Modelos sugeridos por memória da placa de vídeo. O usuário pode usar qualquer modelo instalado.
RECOMENDADOS = [
    {"modelo": "qwen2.5:14b", "vram_min": 11000, "contexto": 16384, "nota": "melhor qualidade (placa de 12 GB)"},
    {"modelo": "qwen2.5:7b", "vram_min": 6500, "contexto": 12288, "nota": "rápido (placa de 8 GB)"},
    {"modelo": "qwen2.5:3b", "vram_min": 0, "contexto": 8192, "nota": "computadores sem placa de vídeo"},
]


class OllamaErro(Exception):
    pass


def recomendado_para(vram_mb: int) -> dict:
    for r in RECOMENDADOS:
        if vram_mb >= r["vram_min"]:
            return r
    return RECOMENDADOS[-1]


def contexto_para(vram_mb: int) -> int:
    return recomendado_para(vram_mb)["contexto"]


class Ollama:
    def __init__(self, url: str = URL_PADRAO, timeout: int = 900):
        self.url = (url or URL_PADRAO).rstrip("/")
        self.timeout = timeout

    def _req(self, caminho: str, dados: dict | None = None, timeout: int | None = None):
        corpo = json.dumps(dados).encode() if dados is not None else None
        req = urllib.request.Request(self.url + caminho, data=corpo, method="POST" if dados is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode("utf-8", "replace")[:300]
            raise OllamaErro(f"Ollama respondeu erro {e.code}: {detalhe}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise OllamaErro("Não foi possível falar com o Ollama. Confira se ele está instalado e aberto "
                             f"(ícone da lhama perto do relógio do Windows). Detalhe: {e}") from e

    def disponivel(self) -> bool:
        try:
            with self._req("/api/version", timeout=3) as r:
                return r.status == 200
        except OllamaErro:
            return False

    def versao(self) -> str:
        with self._req("/api/version", timeout=5) as r:
            return json.loads(r.read()).get("version", "?")

    def modelos(self) -> list[str]:
        with self._req("/api/tags", timeout=10) as r:
            return sorted(m["name"] for m in json.loads(r.read()).get("models", []))

    def baixar(self, modelo: str, progresso: Callable[[str, float], None],
               cancelado: Callable[[], bool] = lambda: False) -> None:
        with self._req("/api/pull", {"model": modelo, "stream": True}, timeout=3600) as r:
            for linha in r:
                if cancelado():
                    raise OllamaErro("Download cancelado.")
                if not linha.strip():
                    continue
                ev = json.loads(linha)
                if "error" in ev:
                    raise OllamaErro(ev["error"])
                total, feito = ev.get("total") or 0, ev.get("completed") or 0
                progresso(ev.get("status", ""), feito / total if total else 0.0)

    def chat_json(self, modelo: str, sistema: str, usuario: str, schema: dict, contexto: int = 8192,
                  temperatura: float = 0.3) -> dict:
        """Pede uma resposta em JSON obedecendo ao schema (saída estruturada do Ollama)."""
        dados = {
            "model": modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "format": schema,
            "stream": False,
            "options": {"num_ctx": contexto, "temperature": temperatura},
            "keep_alive": "5m",
        }
        with self._req("/api/chat", dados) as r:
            resp = json.loads(r.read())
        if "error" in resp:
            raise OllamaErro(resp["error"])
        texto = resp.get("message", {}).get("content", "")
        try:
            return json.loads(texto)
        except json.JSONDecodeError as e:
            raise OllamaErro(f"A IA não devolveu um JSON válido: {texto[:200]}") from e

    def descarregar(self, modelo: str) -> None:
        """Tira o modelo da memória da GPU (antes de renderizar com NVENC, por exemplo)."""
        try:
            with self._req("/api/generate", {"model": modelo, "keep_alive": 0}, timeout=30):
                pass
        except OllamaErro:
            pass
