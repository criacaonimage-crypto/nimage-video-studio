"""Bibliotecas da NVIDIA (cuBLAS 12 e cuDNN 9) para transcrever pela placa de vídeo no Windows.

Elas são grandes (~1,3 GB), então não vão no instalador: são baixadas uma única vez do PyPI
(repositório oficial do Python) quando o usuário ativa a opção em Configurações.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import config

PACOTES = [("nvidia-cublas-cu12", "12."), ("nvidia-cudnn-cu12", "9.")]
DLLS_NECESSARIAS = ("cublas64_12.dll", "cudnn_ops64_9.dll")


class CudaLibsErro(Exception):
    pass


def pasta() -> Path:
    return config.data_dir() / "cuda"


def instaladas() -> bool:
    return all((pasta() / d).exists() for d in DLLS_NECESSARIAS)


def _versao_chave(v: str):
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def escolher_wheel(meta: dict, prefixo: str) -> dict | None:
    """Do JSON do PyPI, pega a versão mais nova (com o prefixo) que tenha arquivo para Windows 64 bits."""
    for v in sorted(meta.get("releases", {}), key=_versao_chave, reverse=True):
        if not v.startswith(prefixo):
            continue
        for f in meta["releases"][v]:
            if f["filename"].endswith("win_amd64.whl") and not f.get("yanked"):
                return {"versao": v, "url": f["url"], "tamanho": f.get("size", 0), "nome": f["filename"]}
    return None


def extrair_dlls(wheel: Path, destino: Path) -> int:
    destino.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(wheel) as z:
        for nome in z.namelist():
            if nome.lower().endswith(".dll") and "/bin/" in nome.replace("\\", "/"):
                with z.open(nome) as src, open(destino / Path(nome).name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                n += 1
    return n


def baixar(progresso: Callable[[str, float], None], cancelado: Callable[[], bool] = lambda: False) -> None:
    if os.name != "nt":
        raise CudaLibsErro("Necessário apenas no Windows.")
    alvos = []
    for pacote, prefixo in PACOTES:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pacote}/json", timeout=30) as r:
            w = escolher_wheel(json.loads(r.read()), prefixo)
        if not w:
            raise CudaLibsErro(f"Não encontrei {pacote} para Windows.")
        alvos.append(w)
    total = sum(w["tamanho"] for w in alvos) or 1
    feito = 0
    with tempfile.TemporaryDirectory() as tmp:
        for w in alvos:
            arq = Path(tmp) / w["nome"]
            with urllib.request.urlopen(w["url"], timeout=60) as r, open(arq, "wb") as out:
                while True:
                    if cancelado():
                        raise CudaLibsErro("Download cancelado.")
                    bloco = r.read(1 << 20)
                    if not bloco:
                        break
                    out.write(bloco)
                    feito += len(bloco)
                    progresso(f"Baixando {w['nome'].split('-')[0]} ({feito / 1e9:.2f} de {total / 1e9:.2f} GB)",
                              min(feito / total, 0.99))
            progresso("Extraindo bibliotecas…", min(feito / total, 0.99))
            extrair_dlls(arq, pasta())
    if not instaladas():
        raise CudaLibsErro("Download concluído, mas as bibliotecas esperadas não foram encontradas.")
    progresso("Pronto.", 1.0)
