"""Desenho de legendas e cartões (título, logo, chamada) com o Qt — sem ffmpeg/libass.

Retorna imagens numpy BGR/BGRA prontas para sobrepor nos quadros do vídeo.
"""
from __future__ import annotations

import threading

import numpy as np

from . import fonts

_lock = threading.RLock()
_familias: dict[str, str] = {}


def garantir_qt():
    """Cria um QGuiApplication se o programa estiver rodando sem interface (scripts/testes)."""
    from PySide6.QtCore import QCoreApplication
    if QCoreApplication.instance() is None:
        import os
        import sys
        from PySide6.QtGui import QGuiApplication
        if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        garantir_qt._app = QGuiApplication(sys.argv[:1] or ["nimage"])  # mantém referência


def familia(nome: str) -> tuple[str, bool]:
    """Registra a fonte do padrão (ou uma comum) e devolve (família Qt, encontrou a fonte pedida?)."""
    from PySide6.QtGui import QFontDatabase
    with _lock:
        chave = nome or ""
        arquivo, achou = fonts.arquivo_ou_padrao(nome)
        if chave in _familias:
            return _familias[chave], achou
        fam = "Arial"
        if arquivo:
            fid = QFontDatabase.addApplicationFont(arquivo)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                fam = fams[0]
        _familias[chave] = fam
        return fam, achou


def _qcolor(hexcor: str):
    from PySide6.QtGui import QColor
    c = QColor(hexcor or "#FFFFFF")
    return c if c.isValid() else QColor("#FFFFFF")


def _bgra(img) -> np.ndarray:
    """QImage (ARGB32) → numpy H×W×4 na ordem B,G,R,A."""
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.bytesPerLine() * h).reshape(h, img.bytesPerLine() // 4, 4)
    return arr[:, :w].copy()


def _fonte(fam: str, px: int, peso_forte: bool = False):
    from PySide6.QtGui import QFont
    f = QFont(fam)
    f.setPixelSize(max(6, int(px)))
    if peso_forte:
        f.setWeight(QFont.Weight.Bold)
    return f


def legenda(palavras: list[tuple[str, bool]], W: int, H: int, fam: str, tamanho: int, cor: str, cor_dest: str,
            contorno: int, alinhamento: str, margem: int) -> tuple[int, int, np.ndarray]:
    """Bloco de legenda. palavras = [(texto, destacada?)]. Retorna (x, y, BGRA)."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QPainterPath, QPen
    with _lock:
        f_norm = _fonte(fam, tamanho)
        f_dest = _fonte(fam, tamanho * 1.12)
        m_norm, m_dest = QFontMetricsF(f_norm), QFontMetricsF(f_dest)
        espaco = m_norm.horizontalAdvance(" ")
        largura_max = W * 0.84
        linhas, atual, larg = [], [], 0.0
        for texto, dest in palavras:
            wpal = (m_dest if dest else m_norm).horizontalAdvance(texto)
            if atual and larg + espaco + wpal > largura_max:
                linhas.append((atual, larg))
                atual, larg = [], 0.0
            larg = larg + (espaco if atual else 0) + wpal
            atual.append((texto, dest, wpal))
        if atual:
            linhas.append((atual, larg))
        alt_linha = max(m_norm.height(), m_dest.height()) * 1.05
        pad = contorno * 2 + 4
        bw = int(min(W, max(l for _, l in linhas) + pad * 2)) if linhas else 10
        bh = int(alt_linha * len(linhas) + pad * 2)
        img = QImage(max(bw, 2), max(bh, 2), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        caneta = QPen(_qcolor("#000000"), contorno * 2)
        caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        for li, (itens, lw) in enumerate(linhas):
            x = (bw - lw) / 2
            base = pad + alt_linha * li + max(m_norm.ascent(), m_dest.ascent())
            for texto, dest, wpal in itens:
                caminho = QPainterPath()
                caminho.addText(QPointF(x, base), f_dest if dest else f_norm, texto)
                if contorno > 0:
                    p.strokePath(caminho, caneta)
                p.fillPath(caminho, _qcolor(cor_dest if dest else cor))
                x += wpal + espaco
        p.end()
        arr = _bgra(img)
    x = (W - arr.shape[1]) // 2
    if alinhamento == "centro":
        y = (H - arr.shape[0]) // 2
    elif alinhamento == "topo":
        y = margem
    else:
        y = H - margem - arr.shape[0]
    return x, max(0, min(y, H - arr.shape[0])), arr


def cartao(W: int, H: int, fundo: str, texto: str, logo: str | None, cor_texto: str, cor_principal: str,
           fonte_titulo: str, tamanho_titulo: int) -> np.ndarray:
    """Cartão de título / logo / chamada para ação (BGR)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    with _lock:
        fam, _ = familia(fonte_titulo)
        base = min(W, H) / 1080
        img = QImage(W, H, QImage.Format.Format_ARGB32)
        img.fill(_qcolor(fundo))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        y_texto = H / 2
        if logo:
            lg = QImage(logo)
            if not lg.isNull():
                lw = W * 0.45
                lh = lg.height() * lw / max(lg.width(), 1)
                ly = (H - lh) / 2 - (H * 0.08 if texto else 0)
                p.drawImage(QRectF((W - lw) / 2, ly, lw, lh), lg)
                y_texto = ly + lh + H * 0.06
        if texto:
            tam = int(tamanho_titulo * base)
            f = _fonte(fam, tam, peso_forte=True)
            p.setFont(f)
            p.setPen(_qcolor(cor_texto))
            area = QRectF(W * 0.08, (y_texto - tam * 1.5) if not logo else y_texto, W * 0.84, tam * 3)
            p.drawText(area, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), texto)
            bw, bh = W * 0.18, max(4, 8 * base)
            p.fillRect(QRectF((W - bw) / 2, area.bottom() + tam * 0.2, bw, bh), _qcolor(cor_principal))
        p.end()
        return np.ascontiguousarray(_bgra(img)[:, :, :3])
