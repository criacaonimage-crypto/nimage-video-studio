"""Localiza o arquivo de uma fonte pelo nome (ex.: "Neue Plak Black") para as legendas e títulos."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

EXTS = {".ttf", ".otf", ".ttc"}


def pastas_de_fontes() -> list[Path]:
    pastas = []
    if os.name == "nt":
        pastas.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        pastas.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts")
    else:
        pastas += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                   Path.home() / ".local/share/fonts", Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [p for p in pastas if p.is_dir()]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=1)
def _indice() -> list[tuple[str, str]]:
    itens = []
    for pasta in pastas_de_fontes():
        for raiz, _d, arquivos in os.walk(pasta):
            for a in arquivos:
                if Path(a).suffix.lower() in EXTS:
                    itens.append((_norm(Path(a).stem), str(Path(raiz) / a)))
    return itens


@lru_cache(maxsize=64)
def encontrar(nome: str) -> str | None:
    """Melhor arquivo para o nome da fonte; None se nada parecido estiver instalado."""
    alvo = _norm(nome or "")
    if not alvo:
        return None
    idx = _indice()
    for chave, caminho in idx:          # 1) nome exato
        if chave == alvo:
            return caminho
    candidatos = [(chave, c) for chave, c in idx if chave.startswith(alvo) or alvo.startswith(chave)]
    if not candidatos:                   # 2) todas as palavras do nome aparecem no arquivo
        palavras = [_norm(p) for p in re.split(r"[\s\-_]+", nome) if p]
        candidatos = [(chave, c) for chave, c in idx if all(p in chave for p in palavras)]
    if not candidatos:
        return None
    return min(candidatos, key=lambda x: abs(len(x[0]) - len(alvo)))[1]


def padrao() -> str | None:
    for n in ("Arial Bold", "Arial", "Segoe UI Bold", "DejaVu Sans Bold", "Liberation Sans Bold", "DejaVu Sans"):
        f = encontrar(n)
        if f:
            return f
    idx = _indice()
    return idx[0][1] if idx else None


def arquivo_ou_padrao(nome: str) -> tuple[str | None, bool]:
    """(arquivo, encontrado?). Se a fonte do padrão não estiver instalada, usa uma fonte comum."""
    f = encontrar(nome) if nome else None
    return (f, True) if f else (padrao(), False)


def nome_familia(arquivo: str) -> str:
    """Nome completo gravado no arquivo da fonte (tabela "name", id 4; senão id 1). O libass busca por ele."""
    import struct
    try:
        with open(arquivo, "rb") as f:
            dados = f.read()
        base = 0
        if dados[:4] == b"ttcf":
            base = struct.unpack(">I", dados[12:16])[0]
        num = struct.unpack(">H", dados[base + 4:base + 6])[0]
        for i in range(num):
            tag, _cs, off, _ln = struct.unpack(">4sIII", dados[base + 12 + 16 * i: base + 28 + 16 * i])
            if tag != b"name":
                continue
            _fmt, count, str_off = struct.unpack(">HHH", dados[off:off + 6])
            achados = {}
            for j in range(count):
                pid, eid, lid, nid, ln, so = struct.unpack(">HHHHHH", dados[off + 6 + 12 * j: off + 18 + 12 * j])
                if nid not in (1, 4):
                    continue
                bruto = dados[off + str_off + so: off + str_off + so + ln]
                try:
                    txt = bruto.decode("utf-16-be") if pid in (0, 3) else bruto.decode("latin-1")
                except UnicodeDecodeError:
                    continue
                if nid not in achados or (pid == 3 and lid == 0x409):
                    achados[nid] = txt
            if achados:
                return achados.get(4) or achados.get(1)
    except (OSError, struct.error):
        pass
    return Path(arquivo).stem
