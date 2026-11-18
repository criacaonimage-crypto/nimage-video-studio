"""ETAPA 5 — Execução: transforma o plano aprovado em um MP4 real, sem nenhum executável externo.

Tudo roda dentro do Python (aceito pelo Controle de Aplicativo Inteligente do Windows):
- PyAV (FFmpeg em forma de biblioteca) lê os vídeos/áudios e grava o MP4 (H.264 pela placa NVIDIA
  quando disponível, senão x264) com áudio AAC;
- OpenCV/numpy fazem recorte, enquadramento no rosto, zoom, transições e a mistura do áudio;
- o Qt desenha legendas, títulos, logo e chamada para ação com as fontes e cores do padrão.
"""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from . import avtools, config, media, textdraw

FPS = 30
SR = avtools.SR
Progresso = Callable[[str, float], None]
QUALIDADES = {"Alta": "Alta", "Padrão": "Padrão", "Rascunho rápido": "Rascunho rápido"}
TRANSICAO_XFADE = {"Suaves": ("fade", 0.45), "Dinâmicas": ("empurrar", 0.30)}
DUR_CARTAO = 2.6


class RenderErro(Exception):
    pass


class RenderCancelado(Exception):
    pass


def nvenc_funciona() -> bool:
    return avtools.encoder_funciona("h264_nvenc")


def limpar_cache(dias: int = 14) -> None:
    """Apaga sobras da versão antiga (intermediários gravados em disco)."""
    pasta = config.data_dir() / "render_cache"
    if not pasta.is_dir():
        return
    limite = time.time() - dias * 86400
    for f in pasta.glob("*.mp4"):
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
        except OSError:
            pass


def _par(n: float) -> int:
    n = int(round(n))
    return max(2, n - (n % 2))


def _bgr(hexcor: str) -> tuple[int, int, int]:
    h = (hexcor or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


# ------------------------------------------------------------------ enquadramento no rosto
def centro_do_rosto(video: str, ini: float, fim: float) -> tuple[float, float] | None:
    """Centro médio (0..1) dos rostos em alguns quadros do trecho; None se não achar."""
    try:
        import cv2
    except ImportError:
        return None
    if not hasattr(cv2, "CascadeClassifier"):
        return None
    det = cv2.CascadeClassifier(str(config.resources_dir() / "haarcascade_frontalface_default.xml"))
    if det.empty():
        return None
    pontos = []
    for k in range(5):
        t = ini + (fim - ini) * (k + 0.5) / 5
        try:
            img = avtools.quadro_em(video, t)
        except avtools.AVErro:
            img = None
        if img is None:
            continue
        h, w = img.shape[:2]
        esc = 480 / max(w, h)
        peq = cv2.resize(img, (int(w * esc), int(h * esc))) if esc < 1 else img
        cinza = cv2.cvtColor(peq, cv2.COLOR_BGR2GRAY)
        rostos = det.detectMultiScale(cinza, scaleFactor=1.15, minNeighbors=6, minSize=(24, 24))
        if len(rostos):
            x, y, rw, rh = max(rostos, key=lambda r: r[2] * r[3])
            ph, pw = cinza.shape[:2]
            pontos.append(((x + rw / 2) / pw, (y + rh / 2) / ph))
    if not pontos:
        return None
    xs, ys = sorted(p[0] for p in pontos), sorted(p[1] for p in pontos)
    return xs[len(xs) // 2], ys[len(ys) // 2]


def caixa_enquadrar(w0: int, h0: int, W: int, H: int, zoom: float,
                    centro: tuple[float, float] | None) -> tuple[int, int, int, int]:
    """Recorte (x, y, largura, altura) na proporção final, centrado no rosto se houver, com zoom."""
    ar = W / H
    if w0 / h0 > ar:
        cw, ch = h0 * ar, h0
    else:
        cw, ch = w0, w0 / ar
    cw, ch = cw / zoom, ch / zoom
    cx, cy = (centro if centro else (0.5, 0.45))
    if centro:
        cy = cy + 0.08  # rosto um pouco acima do centro do quadro
    x = min(max(cx * w0 - cw / 2, 0), w0 - cw)
    y = min(max(cy * h0 - ch / 2, 0), h0 - ch)
    cw, ch = min(_par(cw), w0 - (w0 % 2)), min(_par(ch), h0 - (h0 % 2))
    x, y = int(min(x, w0 - cw)), int(min(y, h0 - ch))
    return max(x, 0), max(y, 0), cw, ch


def _encaixar(img: np.ndarray, W: int, H: int, fundo: tuple[int, int, int]) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    esc = min(W / w, H / h)
    nw, nh = max(2, int(w * esc)), max(2, int(h * esc))
    out = np.empty((H, W, 3), np.uint8)
    out[:] = fundo
    x, y = (W - nw) // 2, (H - nh) // 2
    out[y:y + nh, x:x + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return out


# ------------------------------------------------------------------ segmentos (vídeo + áudio)
class Segmento:
    def __init__(self, tipo: str, duracao: float, audio: np.ndarray, gerar, clipe: dict | None = None):
        self.tipo, self.duracao, self.audio, self._gerar, self.clipe = tipo, duracao, audio, gerar, clipe
        self.n = max(1, int(round(duracao * FPS)))

    def quadros(self):
        return self._gerar()


def _fade_bordas(a: np.ndarray, seg: float = 0.03) -> np.ndarray:
    n = min(len(a) // 2, int(seg * SR))
    if n > 0:
        r = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        a[:n] *= r
        a[-n:] *= r[::-1]
    return a


def _seg_trecho(c: dict, W: int, H: int, enquadramento: str) -> Segmento:
    import cv2
    info = media.probe(c["video"])
    dur = max(0.2, c["fim"] - c["inicio"])
    zoom = float(c.get("zoom", 1.0) or 1.0)
    precisa = abs(info["largura"] / max(info["altura"], 1) - W / H) > 0.02 or zoom != 1.0
    centro = centro_do_rosto(c["video"], c["inicio"], c["fim"]) if (precisa and enquadramento == "rosto") else None
    caixa = caixa_enquadrar(info["largura"], info["altura"], W, H, zoom, centro)
    audio = _fade_bordas(avtools.ler_audio(c["video"], c["inicio"], dur) if info["tem_audio"]
                         else np.zeros((int(round(dur * SR)), 2), np.float32))

    def gerar():
        x, y, cw, ch = caixa
        for img in avtools.quadros(c["video"], c["inicio"], dur, FPS):
            h0, w0 = img.shape[:2]
            if (w0, h0) != (info["largura"], info["altura"]):   # segurança se a orientação divergir
                x2, y2, cw2, ch2 = caixa_enquadrar(w0, h0, W, H, zoom, centro)
                rec = img[y2:y2 + ch2, x2:x2 + cw2]
            else:
                rec = img[y:y + ch, x:x + cw]
            yield cv2.resize(rec, (W, H), interpolation=cv2.INTER_AREA if cw >= W else cv2.INTER_LINEAR)

    return Segmento("trecho", dur, audio, gerar, c)


def _seg_vinheta(arquivo: str, W: int, H: int, fundo: tuple[int, int, int]) -> Segmento:
    info = media.probe(arquivo)
    dur = info["duracao"]
    audio = avtools.ler_audio(arquivo, 0.0, dur) if info["tem_audio"] else np.zeros((int(round(dur * SR)), 2), np.float32)

    def gerar():
        for img in avtools.quadros(arquivo, 0.0, dur, FPS):
            yield _encaixar(img, W, H, fundo)

    return Segmento("vinheta", dur, audio, gerar)


def _seg_cartao(texto: str, logo: str | None, ident: dict, W: int, H: int) -> Segmento:
    cores = ident.get("cores") or {}
    base = textdraw.cartao(W, H, cores.get("fundo", "#000000"), texto, logo, cores.get("texto", "#FFFFFF"),
                           cores.get("principal", "#FFFFFF"), ident.get("fonte_titulo", ""),
                           int(ident.get("tamanho_titulo", 72)))
    n = int(round(DUR_CARTAO * FPS))
    nf = int(0.35 * FPS)

    def gerar():
        for k in range(n):
            a = min(1.0, (k + 1) / nf, (n - k) / nf)
            yield base if a >= 1 else (base.astype(np.float32) * a).astype(np.uint8)

    return Segmento("cartao", DUR_CARTAO, np.zeros((int(round(DUR_CARTAO * SR)), 2), np.float32), gerar)


# ------------------------------------------------------------------ legendas
def _grupos(palavras: list[dict], estilo: str) -> list[list[dict]]:
    maximo = {"Simples": 7, "Palavra-chave em destaque": 4, "Palavra por palavra": 1}.get(estilo, 6)
    grupos, atual = [], []
    for i, w in enumerate(palavras):
        atual.append(w)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        fim_frase = w["p"].endswith((".", "!", "?", ":", ";"))
        pausa = prox is not None and prox["i"] - w["f"] > 0.6
        if len(atual) >= maximo or fim_frase or pausa or prox is None:
            grupos.append(atual)
            atual = []
    return grupos


def eventos_legenda(itens: list[tuple[float, dict]], leg: dict) -> list[dict]:
    """itens: (início do trecho na linha do tempo final, clipe) → eventos {a, b, palavras[(texto, destaque)]}."""
    estilo = leg.get("estilo", "Simples")
    eventos = []
    for inicio_tl, c in itens:
        destaques = [d.lower() for d in c.get("destaques", [])]
        fim_clipe = inicio_tl + c["duracao"]
        grupos = _grupos(c.get("palavras") or [], estilo)
        for gi, g in enumerate(grupos):
            a = inicio_tl + (g[0]["i"] - c["inicio"])
            prox = grupos[gi + 1][0]["i"] - c["inicio"] + inicio_tl if gi + 1 < len(grupos) else fim_clipe
            b = min(inicio_tl + (g[-1]["f"] - c["inicio"]) + 0.25, prox, fim_clipe)
            if b - a < 0.08:
                continue
            ps = []
            for w in g:
                txt = w["p"].strip()
                limpo = txt.strip(".,!?;:\"'“”").lower()
                dest = estilo != "Simples" and any(limpo and (limpo in d.split() or d == limpo) for d in destaques)
                if (dest or estilo == "Palavra por palavra") and leg.get("caixa_alta_destaque"):
                    txt = txt.upper()
                ps.append((txt, dest))
            eventos.append({"a": max(0.0, a), "b": b, "palavras": ps})
    return eventos


class Legendas:
    def __init__(self, eventos: list[dict], leg: dict, W: int, H: int, fam: str):
        base = min(W, H) / 1080
        estilo = leg.get("estilo", "Simples")
        self.tam = int(leg.get("tamanho", 54) * base * (1.25 if estilo == "Palavra por palavra" else 1.0))
        pos = leg.get("posicao", "Inferior")
        vertical = H > W
        self.alinh, self.margem = "baixo", int(H * 0.08)
        if pos == "Abaixo do queixo":
            self.margem = int(H * (0.30 if vertical else 0.12))
        elif pos == "Centro":
            self.alinh, self.margem = "centro", 0
        elif pos == "Superior":
            self.alinh, self.margem = "topo", int(H * 0.08)
        self.eventos, self.leg, self.W, self.H, self.fam = sorted(eventos, key=lambda e: e["a"]), leg, W, H, fam
        self.contorno = max(2, int(3 * base))
        self._cache: dict[int, tuple[int, int, np.ndarray]] = {}

    def aplicar(self, img: np.ndarray, t: float) -> np.ndarray:
        for i, e in enumerate(self.eventos):
            if e["a"] > t:
                break
            if e["a"] <= t < e["b"]:
                if i not in self._cache:
                    self._cache = {k: v for k, v in self._cache.items() if k >= i - 2}
                    self._cache[i] = textdraw.legenda(e["palavras"], self.W, self.H, self.fam, self.tam,
                                                      self.leg.get("cor", "#FFFFFF"),
                                                      self.leg.get("cor_destaque", "#FFFFFF"),
                                                      self.contorno, self.alinh, self.margem)
                x, y, bgra = self._cache[i]
                h, w = bgra.shape[:2]
                h, w = min(h, img.shape[0] - y), min(w, img.shape[1] - x)
                if h <= 0 or w <= 0:
                    return img
                img = img.copy()
                reg = img[y:y + h, x:x + w].astype(np.float32)
                a = bgra[:h, :w, 3:4].astype(np.float32) / 255.0
                img[y:y + h, x:x + w] = (reg * (1 - a) + bgra[:h, :w, :3].astype(np.float32) * a).astype(np.uint8)
                return img
        return img


# ------------------------------------------------------------------ áudio
def _envelope(mono: np.ndarray, janela: int) -> np.ndarray:
    n = len(mono) // janela
    if n == 0:
        return np.zeros(len(mono), np.float32)
    rms = np.sqrt((mono[: n * janela].reshape(n, janela) ** 2).mean(axis=1))
    env = np.repeat(rms, janela).astype(np.float32)
    return np.concatenate([env, np.full(len(mono) - len(env), env[-1], np.float32)])


def _suavizar(alvo: np.ndarray, ataque: float, soltura: float, passo: int) -> np.ndarray:
    """Suavização tipo compressor, calculada por blocos (rápido)."""
    blocos = alvo[::passo]
    out = np.empty_like(blocos)
    a_at = 1 - np.exp(-passo / (ataque * SR))
    a_so = 1 - np.exp(-passo / (soltura * SR))
    g = 1.0
    for i, v in enumerate(blocos):
        g += (v - g) * (a_at if v < g else a_so)
        out[i] = g
    return np.repeat(out, passo)[: len(alvo)]


def _limitar(x: np.ndarray, t: float = 0.85) -> np.ndarray:
    a = np.abs(x)
    m = a > t
    x[m] = np.sign(x[m]) * (t + (1 - t) * np.tanh((a[m] - t) / (1 - t)))
    return x


def _normalizar(x: np.ndarray, alvo_db: float = -16.0) -> np.ndarray:
    bloco = int(0.4 * SR)
    mono = x.mean(axis=1)
    n = len(mono) // bloco
    if n == 0:
        return _limitar(x)
    rms = np.sqrt((mono[: n * bloco].reshape(n, bloco) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    ativos = db[db > -50]
    if len(ativos) == 0:
        return x
    atual = 10 * np.log10(np.mean(10 ** (ativos / 10)))
    ganho = 10 ** (np.clip(alvo_db - atual, -20, 20) / 20)
    return _limitar(x * ganho)


def _montar_audio(segs: list[Segmento], inicios_q: list[int], total_q: int, T: int, plano: dict,
                  avisos: list[str]) -> np.ndarray:
    total = int(round(total_q / FPS * SR))
    voz = np.zeros((total, 2), np.float32)
    Ta = int(round(T / FPS * SR))
    for k, s in enumerate(segs):
        a = s.audio.copy()
        n_ok = int(round(s.n / FPS * SR))
        a = a[:n_ok] if len(a) >= n_ok else np.concatenate([a, np.zeros((n_ok - len(a), 2), np.float32)])
        if Ta > 0 and len(a) > 2 * Ta:
            r = np.linspace(0, 1, Ta, dtype=np.float32)[:, None]
            if k > 0:
                a[:Ta] *= r
            if k < len(segs) - 1:
                a[-Ta:] *= r[::-1]
        ini = int(round(inicios_q[k] / FPS * SR))
        fim = min(total, ini + len(a))
        voz[ini:fim] += a[: fim - ini]

    mix = voz.copy()
    trilha = plano.get("trilha") or {}
    if trilha.get("usar") and trilha.get("arquivo") and Path(trilha["arquivo"]).exists():
        try:
            mus = avtools.ler_audio(trilha["arquivo"], 0.0, None)
        except avtools.AVErro as e:
            mus = None
            avisos.append(f"Não foi possível ler a trilha: {e}")
        if mus is not None and len(mus):
            reps = int(np.ceil(total / len(mus)))
            mus = np.tile(mus, (reps, 1))[:total] * (max(0, min(100, trilha.get("volume", 20))) / 100)
            fi, fo = min(total, SR), min(total, 2 * SR)
            mus[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
            mus[total - fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None]
            if trilha.get("abaixar_na_fala", True):
                env = _envelope(voz.mean(axis=1), int(0.02 * SR))
                alvo = np.where(env > 0.02, 0.25, 1.0).astype(np.float32)
                mus *= _suavizar(alvo, 0.015, 0.45, 256)[:, None]
            mix += mus
    elif trilha.get("usar"):
        avisos.append("Sem trilha: nenhum arquivo de música disponível na biblioteca do padrão.")

    sfx_arqs = [s for s in plano.get("arquivos_sfx", []) if Path(s).exists()]
    if plano.get("efeitos_sonoros") and sfx_arqs and len(segs) > 1:
        escolhido = next((s for s in sfx_arqs if "whoosh" in Path(s).stem.lower()), sfx_arqs[0])
        try:
            sfx = avtools.ler_audio(escolhido, 0.0, None) * 0.45
            for k in range(1, min(len(segs), 61)):
                ini = max(0, int(round((inicios_q[k] / FPS - 0.15) * SR)))
                fim = min(total, ini + len(sfx))
                mix[ini:fim] += sfx[: fim - ini]
        except avtools.AVErro as e:
            avisos.append(f"Não foi possível ler o efeito sonoro: {e}")
    elif plano.get("efeitos_sonoros") and not sfx_arqs:
        avisos.append("Efeitos sonoros ativados, mas não há arquivos na biblioteca do padrão.")
    return _normalizar(mix)


# ------------------------------------------------------------------ renderização completa
def renderizar(plano: dict, saida: str, opcoes: dict | None = None, progresso: Progresso = lambda m, f: None,
               cancelado: Callable[[], bool] = lambda: False) -> dict:
    textdraw.garantir_qt()
    opcoes = opcoes or {}
    fmt = plano.get("formato") or {}
    W = _par(opcoes.get("largura") or fmt.get("largura") or 1920)
    H = _par(opcoes.get("altura") or fmt.get("altura") or 1080)
    qualidade = opcoes.get("qualidade", "Padrão")
    ident = plano.get("identidade") or {"cores": {}, "fonte_titulo": "", "fonte_texto": "", "tamanho_titulo": 72}
    fundo = _bgr((ident.get("cores") or {}).get("fundo", "#000000"))
    enquadramento = plano.get("enquadramento", "rosto")
    avisos: list[str] = []
    clipes = [c for c in plano.get("clipes", []) if c.get("duracao", 0) > 0.2]
    if not clipes:
        raise RenderErro("O plano não tem trechos.")
    faltando = sorted({c["video"] for c in clipes if not Path(c["video"]).exists()})
    if faltando:
        raise RenderErro("Vídeos originais não encontrados (HD externo desconectado?):\n" + "\n".join(faltando))
    limpar_cache()

    try:
        # ----- preparar segmentos -----
        progresso("Preparando os trechos (áudio e enquadramento)…", 0.0)
        segs: list[Segmento] = []
        ab = plano.get("abertura") or {}
        if ab.get("tipo") == "Vinheta" and ab.get("arquivo") and Path(ab["arquivo"]).exists():
            segs.append(_seg_vinheta(ab["arquivo"], W, H, fundo))
        elif ab.get("tipo") == "Título":
            segs.append(_seg_cartao(ab.get("texto") or plano.get("titulo", ""), None, ident, W, H))
        for i, c in enumerate(clipes, start=1):
            if cancelado():
                raise RenderCancelado()
            progresso(f"Preparando o trecho {i} de {len(clipes)}…", 0.08 * i / len(clipes))
            segs.append(_seg_trecho(c, W, H, enquadramento))
        en = plano.get("encerramento") or {}
        if en.get("tipo") == "Vinheta" and en.get("arquivo") and Path(en["arquivo"]).exists():
            segs.append(_seg_vinheta(en["arquivo"], W, H, fundo))
        elif en.get("tipo") == "Logo" and en.get("arquivo") and Path(en["arquivo"]).exists():
            segs.append(_seg_cartao("", en["arquivo"], ident, W, H))
        elif en.get("tipo") == "Chamada para ação" and (en.get("texto") or "").strip():
            segs.append(_seg_cartao(en["texto"], None, ident, W, H))

        # ----- linha do tempo e transições -----
        xf = TRANSICAO_XFADE.get(plano.get("transicao", "Corte seco"))
        T = int(round(xf[1] * FPS)) if xf else 0
        if xf and len(segs) > 1 and min(s.n for s in segs) < 3 * T:
            xf, T = None, 0
            avisos.append("Transições trocadas por cortes secos (há trechos curtos demais para transição).")
        if len(segs) == 1:
            xf, T = None, 0
        inicios_q, t = [], 0
        for s in segs:
            inicios_q.append(t)
            t += s.n - T
        total_q = t + T

        # ----- legendas -----
        leg = plano.get("legendas") or {}
        legendas = None
        if leg.get("ativar") and any(s.clipe and s.clipe.get("palavras") for s in segs):
            nome_fonte = leg.get("fonte") or ident.get("fonte_texto", "")
            fam, achou = textdraw.familia(nome_fonte)
            if nome_fonte and not achou:
                avisos.append(f"A fonte “{nome_fonte}” não está instalada neste computador; "
                              "a legenda usou uma fonte comum.")
            itens = [(inicios_q[k] / FPS, s.clipe) for k, s in enumerate(segs) if s.clipe]
            legendas = Legendas(eventos_legenda(itens, leg), leg, W, H, fam)

        # ----- áudio -----
        progresso("Mixando voz, trilha e efeitos…", 0.1)
        audio = _montar_audio(segs, inicios_q, total_q, T, plano, avisos)

        # ----- vídeo -----
        Path(saida).parent.mkdir(parents=True, exist_ok=True)
        tmp_saida = str(Path(saida).with_name(Path(saida).stem + ".parcial.mp4"))
        grav = avtools.Gravador(tmp_saida, W, H, FPS, audio, qualidade)
        idx = 0
        inicio_relogio = time.time()

        def emitir(img):
            nonlocal idx
            if cancelado():
                raise RenderCancelado()
            if legendas:
                img = legendas.aplicar(img, idx / FPS)
            grav.quadro(img)
            idx += 1
            if idx % 15 == 0:
                gasto = time.time() - inicio_relogio
                resta = gasto / idx * (total_q - idx)
                progresso(f"Gerando o vídeo… {idx / FPS:.0f} de {total_q / FPS:.0f} s "
                          f"(faltam ~{max(resta, 0):.0f} s)", min(0.99, 0.12 + 0.87 * idx / total_q))

        try:
            pendentes: deque = deque()
            for k, s in enumerate(segs):
                gen = s.quadros()
                ultimo = k == len(segs) - 1
                if k > 0 and T > 0:
                    for j in range(T):
                        b = next(gen)
                        a = pendentes.popleft()
                        p = (j + 1) / (T + 1)
                        if xf[0] == "fade":
                            emitir((a.astype(np.float32) * (1 - p) + b.astype(np.float32) * p).astype(np.uint8))
                        else:   # empurrar para a esquerda, com suavização
                            p = p * p * (3 - 2 * p)
                            off = max(1, int(W * p))
                            img = np.empty_like(a)
                            img[:, : W - off] = a[:, off:]
                            img[:, W - off:] = b[:, :off]
                            emitir(img)
                buf: deque = deque()
                for img in gen:
                    buf.append(img)
                    if len(buf) > (0 if ultimo else T):
                        emitir(buf.popleft())
                pendentes = buf
            for img in pendentes:
                emitir(img)
            grav.fechar()
        except BaseException:
            grav.abortar()
            Path(tmp_saida).unlink(missing_ok=True)
            raise
        Path(tmp_saida).replace(saida)
    except avtools.AVErro as e:
        raise RenderErro(str(e)) from e
    info = media.probe(saida)
    progresso("Vídeo pronto.", 1.0)
    return {"arquivo": saida, "duracao": info["duracao"], "largura": info["largura"], "altura": info["altura"],
            "avisos": avisos, "codificador": "NVENC (placa de vídeo)" if grav.nvenc else "x264 (processador)"}
