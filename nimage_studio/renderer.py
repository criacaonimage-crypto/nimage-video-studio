"""ETAPA 5 — Execução: transforma o plano aprovado em um MP4 real usando o FFmpeg.

Etapas:
1. Cada trecho vira um arquivo intermediário já no formato final (corte, enquadramento no rosto, zoom,
   30 fps, áudio 48 kHz). Intermediários ficam em cache: num ajuste, só o que mudou é refeito.
2. Abertura/encerramento: vinheta da biblioteca, cartão de título, logo ou chamada para ação.
3. Uma passada final junta tudo com as transições, queima as legendas no estilo do padrão,
   mistura a trilha (abaixando na fala) e os efeitos sonoros, normaliza o volume e grava o MP4.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import config, fonts, media

FPS = 30
Progresso = Callable[[str, float], None]
QUALIDADES = {  # nome: (crf x264, cq nvenc, preset x264)
    "Alta": (18, 19, "medium"),
    "Padrão": (21, 23, "fast"),
    "Rascunho rápido": (27, 30, "ultrafast"),
}
TRANSICAO_XFADE = {"Suaves": ("fade", 0.45), "Dinâmicas": ("smoothleft", 0.30)}
_nvenc_ok: bool | None = None


class RenderErro(Exception):
    pass


class RenderCancelado(Exception):
    pass


# ------------------------------------------------------------------ utilidades
def nvenc_funciona() -> bool:
    """O FFmpeg pode listar o NVENC mesmo sem placa NVIDIA; testa de verdade uma vez."""
    global _nvenc_ok
    if _nvenc_ok is None:
        ff = config.ffmpeg_path()
        _nvenc_ok = False
        if ff and config.ffmpeg_has_nvenc():
            r = config.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.2", "-c:v", "h264_nvenc",
                            "-f", "null", "-"], timeout=30)
            _nvenc_ok = r.returncode == 0
    return _nvenc_ok


def _codec_video(qualidade: str, intermediario: bool = False) -> list[str]:
    crf, cq, preset = QUALIDADES.get(qualidade, QUALIDADES["Padrão"])
    if intermediario:
        crf, cq, preset = 15, 16, "veryfast"
    if nvenc_funciona():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


def _par(n: float) -> int:
    n = int(round(n))
    return max(2, n - (n % 2))


def _ass_cor(hex_cor: str, alpha: str = "00") -> str:
    h = (hex_cor or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ffmpeg(args: list[str], duracao: float, progresso: Callable[[float], None], cancelado: Callable[[], bool],
            cwd: str | None = None) -> None:
    ff = config.ffmpeg_path()
    if not ff:
        raise RenderErro("FFmpeg não encontrado.")
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([ff, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", *args],
                                stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, cwd=cwd,
                                creationflags=config.CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace")
        try:
            for linha in proc.stdout:
                if cancelado():
                    proc.kill()
                    raise RenderCancelado()
                if linha.startswith("out_time_us=") or linha.startswith("out_time_ms="):
                    try:
                        t = int(linha.split("=", 1)[1]) / 1e6
                        if duracao > 0:
                            progresso(min(t / duracao, 1.0))
                    except ValueError:
                        pass
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        if proc.returncode != 0:
            err.seek(0)
            msg = err.read().decode("utf-8", "replace").strip().splitlines()
            raise RenderErro("O FFmpeg falhou: " + " | ".join(msg[-6:])[:900])


def _cache_dir() -> Path:
    d = config.data_dir() / "render_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def limpar_cache(dias: int = 14) -> None:
    limite = time.time() - dias * 86400
    for f in _cache_dir().glob("*.mp4"):
        try:
            if f.stat().st_atime < limite:
                f.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ enquadramento no rosto
def centro_do_rosto(video: str, ini: float, fim: float) -> tuple[float, float] | None:
    """Centro médio (0..1) dos rostos em alguns quadros do trecho; None se não achar."""
    try:
        import cv2
    except ImportError:
        return None
    cascata = config.resources_dir() / "haarcascade_frontalface_default.xml"   # vem com o programa
    det = cv2.CascadeClassifier(str(cascata))
    if det.empty():
        return None
    cap = cv2.VideoCapture(video)
    pontos = []
    try:
        for k in range(5):
            t = ini + (fim - ini) * (k + 0.5) / 5
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, img = cap.read()
            if not ok:
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
    finally:
        cap.release()
    if not pontos:
        return None
    xs, ys = sorted(p[0] for p in pontos), sorted(p[1] for p in pontos)
    return xs[len(xs) // 2], ys[len(ys) // 2]


def _filtro_enquadrar(w0: int, h0: int, W: int, H: int, zoom: float, centro: tuple[float, float] | None) -> str:
    """Recorta para a proporção final (centrado no rosto, se houver), aplica zoom e redimensiona."""
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
    cw, ch = _par(cw), _par(ch)
    x, y = int(min(x, w0 - cw)), int(min(y, h0 - ch))
    return f"crop={cw}:{ch}:{max(x, 0)}:{max(y, 0)},scale={W}:{H}:flags=lanczos,setsar=1"


def _filtro_encaixar(W: int, H: int, fundo: str) -> str:
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={fundo},setsar=1")


# ------------------------------------------------------------------ segmentos
def _seg_trecho(c: dict, W: int, H: int, enquadramento: str, cancelado) -> Path:
    info = media.probe(c["video"])
    dur = max(0.2, c["fim"] - c["inicio"])
    chave = hashlib.sha1(json.dumps([c["video"], os.path.getmtime(c["video"]), c["inicio"], c["fim"], W, H,
                                     c.get("zoom", 1.0), enquadramento, nvenc_funciona(), 3]).encode()).hexdigest()[:24]
    destino = _cache_dir() / f"t_{chave}.mp4"
    if destino.exists():
        return destino
    precisa_recorte = abs(info["largura"] / max(info["altura"], 1) - W / H) > 0.02 or c.get("zoom", 1.0) != 1.0
    centro = centro_do_rosto(c["video"], c["inicio"], c["fim"]) if (precisa_recorte and enquadramento == "rosto") else None
    vf = _filtro_enquadrar(info["largura"], info["altura"], W, H, c.get("zoom", 1.0), centro) + f",fps={FPS},format=yuv420p"
    args = ["-ss", f"{c['inicio']:.3f}", "-t", f"{dur:.3f}", "-i", c["video"]]
    if info["tem_audio"]:
        af = (f"aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.03,"
              f"afade=t=out:st={max(dur - 0.04, 0):.3f}:d=0.04,apad")
        mapa = ["-map", "0:v:0", "-map", "0:a:0", "-af", af]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        mapa = ["-map", "0:v:0", "-map", "1:a:0"]
    tmp = destino.with_suffix(".tmp.mp4")
    _ffmpeg(args + ["-vf", vf, *mapa, "-t", f"{dur:.3f}", *_codec_video("", True), "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", str(tmp)], dur, lambda f: None, cancelado)
    tmp.replace(destino)
    return destino


def _seg_vinheta(arquivo: str, W: int, H: int, fundo: str, cancelado) -> Path:
    info = media.probe(arquivo)
    chave = hashlib.sha1(json.dumps([arquivo, os.path.getmtime(arquivo), W, H, fundo, 2]).encode()).hexdigest()[:24]
    destino = _cache_dir() / f"v_{chave}.mp4"
    if destino.exists():
        return destino
    args = ["-i", arquivo]
    if info["tem_audio"]:
        mapa = ["-map", "0:v:0", "-map", "0:a:0", "-af", "aresample=48000,aformat=channel_layouts=stereo"]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        mapa = ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    tmp = destino.with_suffix(".tmp.mp4")
    _ffmpeg(args + ["-vf", _filtro_encaixar(W, H, fundo) + f",fps={FPS},format=yuv420p", *mapa,
                    "-t", f"{info['duracao']:.3f}", *_codec_video("", True), "-c:a", "aac", "-ar", "48000", str(tmp)],
            info["duracao"], lambda f: None, cancelado)
    tmp.replace(destino)
    return destino


def _seg_cartao(tipo: str, texto: str, logo: str | None, ident: dict, W: int, H: int, pasta: Path,
                nome: str, cancelado, dur: float = 2.6) -> Path:
    """Cartão de título, logo ou chamada para ação, nas cores e fontes do padrão."""
    cores = ident.get("cores", {})
    fundo = cores.get("fundo", "#000000")
    base = min(W, H) / 1080
    fonte, _ = fonts.arquivo_ou_padrao(ident.get("fonte_titulo", ""))
    destino = pasta / f"{nome}.mp4"
    entradas = ["-f", "lavfi", "-i", f"color=c={fundo}:s={W}x{H}:r={FPS}:d={dur}",
                "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo"]
    filtros = []
    atual = "[0:v]"
    if logo and Path(logo).exists():
        entradas += ["-loop", "1", "-t", str(dur), "-i", logo]
        lw = _par(W * 0.45)
        filtros.append(f"[2:v]scale={lw}:-2,format=rgba[lg]")
        oy = "(H-h)/2" if not texto else "(H-h)/2-H*0.08"
        filtros.append(f"{atual}[lg]overlay=(W-w)/2:{oy}[v1]")
        atual = "[v1]"
    if texto and fonte:
        shutil.copy(fonte, pasta / f"{nome}.ttf")
        (pasta / f"{nome}.txt").write_text(texto, encoding="utf-8")
        tam = int(ident.get("tamanho_titulo", 72) * base)
        y = "(h-text_h)/2" if not logo else "h*0.62"
        filtros.append(f"{atual}drawtext=fontfile={nome}.ttf:textfile={nome}.txt:fontsize={tam}:"
                       f"fontcolor={cores.get('texto', '#FFFFFF')}:x=(w-text_w)/2:y={y}:line_spacing={int(tam * 0.2)}[v2]")
        atual = "[v2]"
        barra_y = f"(ih+{tam})/2+{int(tam * 0.4)}" if not logo else f"ih*0.62+{int(tam * 1.5)}"
        filtros.append(f"{atual}drawbox=x=(iw-{_par(W * 0.18)})/2:y={barra_y}:w={_par(W * 0.18)}:h={max(4, int(8 * base))}:"
                       f"color={cores.get('principal', '#FFFFFF')}:t=fill[v3]")
        atual = "[v3]"
    filtros.append(f"{atual}fade=t=in:d=0.35,fade=t=out:st={dur - 0.35:.2f}:d=0.35,format=yuv420p[vout]")
    _ffmpeg([*entradas, "-filter_complex", ";".join(filtros), "-map", "[vout]", "-map", "1:a", "-t", str(dur),
             *_codec_video("", True), "-c:a", "aac", "-ar", "48000", destino.name], dur, lambda f: None, cancelado,
            cwd=str(pasta))
    return destino


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


def _ass_escape(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def gerar_ass(itens: list[tuple[float, dict]], leg: dict, W: int, H: int, nome_fonte: str) -> str:
    """itens: (início do trecho na linha do tempo final, clipe). Retorna o texto do arquivo .ass."""
    base = min(W, H) / 1080
    estilo = leg.get("estilo", "Simples")
    tam = int(leg.get("tamanho", 54) * base * (1.25 if estilo == "Palavra por palavra" else 1.0))
    pos = leg.get("posicao", "Inferior")
    vertical = H > W
    alinh, margem = 2, int(H * 0.08)
    if pos == "Abaixo do queixo":
        margem = int(H * (0.30 if vertical else 0.12))
    elif pos == "Centro":
        alinh, margem = 5, 0
    elif pos == "Superior":
        alinh, margem = 8, int(H * 0.08)
    cor, dest = _ass_cor(leg.get("cor", "#FFFFFF")), _ass_cor(leg.get("cor_destaque", "#FFFFFF"))
    contorno = max(2, int(3 * base))
    cab = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Leg,{nome_fonte},{tam},{cor},{cor},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{contorno},"
        f"{max(1, int(2 * base))},{alinh},{int(W * 0.08)},{int(W * 0.08)},{margem},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def ts(t: float) -> str:
        t = max(0.0, t)
        h, m = int(t // 3600), int(t % 3600 // 60)
        return f"{h}:{m:02d}:{t % 60:05.2f}"

    eventos = []
    for inicio_tl, c in itens:
        destaques = [d.lower() for d in c.get("destaques", [])]
        palavras = c.get("palavras") or []
        fim_clipe = inicio_tl + c["duracao"]
        grupos = _grupos(palavras, estilo)
        for gi, g in enumerate(grupos):
            a = inicio_tl + (g[0]["i"] - c["inicio"])
            prox = grupos[gi + 1][0]["i"] - c["inicio"] + inicio_tl if gi + 1 < len(grupos) else fim_clipe
            b = min(inicio_tl + (g[-1]["f"] - c["inicio"]) + 0.25, prox, fim_clipe)
            if b - a < 0.08:
                continue
            partes = []
            for w in g:
                txt = _ass_escape(w["p"])
                limpo = txt.strip(".,!?;:\"'“”").lower()
                eh_dest = any(limpo and (limpo in d.split() or d == limpo) for d in destaques)
                if eh_dest and estilo != "Simples":
                    if leg.get("caixa_alta_destaque"):
                        txt = txt.upper()
                    partes.append(f"{{\\c{dest}&\\fscx112\\fscy112}}{txt}{{\\r}}")
                elif estilo == "Palavra por palavra" and leg.get("caixa_alta_destaque"):
                    partes.append(txt.upper())
                else:
                    partes.append(txt)
            eventos.append(f"Dialogue: 0,{ts(a)},{ts(b)},Leg,,0,0,0,,{' '.join(partes)}")
    return cab + "\n".join(eventos) + "\n"


# ------------------------------------------------------------------ renderização completa
def renderizar(plano: dict, saida: str, opcoes: dict | None = None, progresso: Progresso = lambda m, f: None,
               cancelado: Callable[[], bool] = lambda: False) -> dict:
    opcoes = opcoes or {}
    fmt = plano.get("formato") or {}
    W = _par(opcoes.get("largura") or fmt.get("largura") or 1920)
    H = _par(opcoes.get("altura") or fmt.get("altura") or 1080)
    qualidade = opcoes.get("qualidade", "Padrão")
    ident = plano.get("identidade") or {"cores": {}, "fonte_titulo": "", "fonte_texto": "", "tamanho_titulo": 72}
    fundo = (ident.get("cores") or {}).get("fundo", "#000000")
    enquadramento = plano.get("enquadramento", "rosto")
    avisos: list[str] = []
    clipes = [c for c in plano.get("clipes", []) if c.get("duracao", 0) > 0.2]
    if not clipes:
        raise RenderErro("O plano não tem trechos.")
    faltando = sorted({c["video"] for c in clipes if not Path(c["video"]).exists()})
    if faltando:
        raise RenderErro("Vídeos originais não encontrados (HD externo desconectado?):\n" + "\n".join(faltando))
    limpar_cache()

    with tempfile.TemporaryDirectory(prefix="nimage_render_") as tmpd:
        tmp = Path(tmpd)
        segs: list[tuple[Path, dict | None]] = []   # (arquivo, clipe ou None p/ vinhetas/cartões)
        total_passos = len(clipes) + 2

        def passo(i: int, msg: str):
            progresso(msg, 0.55 * i / total_passos)

        # abertura
        ab = plano.get("abertura") or {}
        if ab.get("tipo") == "Vinheta" and ab.get("arquivo") and Path(ab["arquivo"]).exists():
            passo(0, "Preparando a vinheta de abertura…")
            segs.append((_seg_vinheta(ab["arquivo"], W, H, fundo, cancelado), None))
        elif ab.get("tipo") == "Título":
            passo(0, "Criando o cartão de título…")
            segs.append((_seg_cartao("titulo", ab.get("texto") or plano.get("titulo", ""), None, ident, W, H, tmp,
                                     "abertura", cancelado), None))
        # trechos
        for i, c in enumerate(clipes, start=1):
            if cancelado():
                raise RenderCancelado()
            passo(i, f"Cortando e enquadrando o trecho {i} de {len(clipes)}…")
            segs.append((_seg_trecho(c, W, H, enquadramento, cancelado), c))
        # encerramento
        en = plano.get("encerramento") or {}
        passo(len(clipes) + 1, "Preparando o encerramento…")
        if en.get("tipo") == "Vinheta" and en.get("arquivo") and Path(en["arquivo"]).exists():
            segs.append((_seg_vinheta(en["arquivo"], W, H, fundo, cancelado), None))
        elif en.get("tipo") == "Logo" and en.get("arquivo") and Path(en["arquivo"]).exists():
            segs.append((_seg_cartao("logo", "", en["arquivo"], ident, W, H, tmp, "encerramento", cancelado), None))
        elif en.get("tipo") == "Chamada para ação" and (en.get("texto") or "").strip():
            segs.append((_seg_cartao("cta", en["texto"], None, ident, W, H, tmp, "encerramento", cancelado), None))

        duracoes = [media.probe(s)["duracao"] for s, _ in segs]

        # transições
        tipo_tr = plano.get("transicao", "Corte seco")
        xf = TRANSICAO_XFADE.get(tipo_tr)
        if xf and min(duracoes) < xf[1] * 3:
            xf = None
            avisos.append("Transições trocadas por cortes secos (há trechos curtos demais para transição).")
        td = xf[1] if xf else 0.0
        inicios, t = [], 0.0
        for d in duracoes:
            inicios.append(t)
            t += d - td
        total = t + td

        # legendas
        leg = plano.get("legendas") or {}
        tem_legenda = leg.get("ativar") and any(c and c.get("palavras") for _, c in segs)
        if tem_legenda:
            arq_fonte, achou = fonts.arquivo_ou_padrao(leg.get("fonte") or ident.get("fonte_texto", ""))
            if not achou and (leg.get("fonte") or ident.get("fonte_texto")):
                avisos.append(f"A fonte “{leg.get('fonte') or ident.get('fonte_texto')}” não está instalada neste "
                              "computador; a legenda usou uma fonte comum.")
            (tmp / "fontes").mkdir()
            nome_fonte = "Arial"
            if arq_fonte:
                shutil.copy(arq_fonte, tmp / "fontes" / Path(arq_fonte).name)
                nome_fonte = fonts.nome_familia(arq_fonte)
            itens = [(inicios[k], c) for k, (_, c) in enumerate(segs) if c]
            (tmp / "legendas.ass").write_text(gerar_ass(itens, leg, W, H, nome_fonte), encoding="utf-8")

        # entradas
        args: list[str] = []
        for s, _ in segs:
            args += ["-i", str(s)]
        n = len(segs)
        filtros: list[str] = [f"[{k}:v]settb=AVTB,fps={FPS},format=yuv420p,setsar=1[n{k}]" for k in range(n)]
        if xf and n > 1:
            vprev, aprev = "[n0]", "[0:a]"
            for k in range(1, n):
                off = inicios[k]
                filtros.append(f"{vprev}[n{k}]xfade=transition={xf[0]}:duration={td}:offset={off:.3f}[vx{k}]")
                filtros.append(f"{aprev}[{k}:a]acrossfade=d={td}[ax{k}]")
                vprev, aprev = f"[vx{k}]", f"[ax{k}]"
            vcad, acad = vprev, aprev
        else:
            filtros.append("".join(f"[n{k}][{k}:a]" for k in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
            vcad, acad = "[vc]", "[ac]"
        if tem_legenda:
            filtros.append(f"{vcad}subtitles=legendas.ass:fontsdir=fontes[vl]")
            vcad = "[vl]"
        filtros.append(f"{vcad}format=yuv420p[vout]")

        # áudio: voz + trilha (abaixa na fala) + efeitos sonoros
        trilha = plano.get("trilha") or {}
        mix = [acad]
        extra_idx = n
        if trilha.get("usar") and trilha.get("arquivo") and Path(trilha["arquivo"]).exists():
            args += ["-stream_loop", "-1", "-i", trilha["arquivo"]]
            vol = max(0.0, min(1.0, trilha.get("volume", 20) / 100))
            filtros.append(f"{acad}asplit=2[voz][vozsc]")
            filtros.append(f"[{extra_idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                           f"asetpts=N/SR/TB,volume={vol:.3f},afade=t=in:d=1,"
                           f"afade=t=out:st={max(total - 2, 0):.3f}:d=2[mus]")
            if trilha.get("abaixar_na_fala", True):
                filtros.append("[mus][vozsc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450:makeup=1[musd]")
            else:
                filtros.append("[vozsc]anullsink;[mus]anull[musd]")
            mix = ["[voz]", "[musd]"]
            extra_idx += 1
        elif trilha.get("usar"):
            avisos.append("Sem trilha: nenhum arquivo de música disponível na biblioteca do padrão.")
        sfx = [s for s in plano.get("arquivos_sfx", []) if Path(s).exists()]
        juncoes = [inicios[k] for k in range(1, n)][:60]
        if plano.get("efeitos_sonoros") and sfx and juncoes:
            escolhido = next((s for s in sfx if "whoosh" in Path(s).stem.lower()), sfx[0])
            args += ["-i", escolhido]
            filtros.append(f"[{extra_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.45,"
                           f"asplit={len(juncoes)}" + "".join(f"[sx{j}]" for j in range(len(juncoes))))
            for j, tj in enumerate(juncoes):
                ms = int(max(tj - 0.15, 0) * 1000)
                filtros.append(f"[sx{j}]adelay={ms}|{ms}[sd{j}]")
            filtros.append("".join(f"[sd{j}]" for j in range(len(juncoes))) +
                           f"amix=inputs={len(juncoes)}:normalize=0:duration=longest[sfx]")
            mix.append("[sfx]")
            extra_idx += 1
        elif plano.get("efeitos_sonoros") and not sfx:
            avisos.append("Efeitos sonoros ativados, mas não há arquivos na biblioteca do padrão.")
        if len(mix) > 1:
            filtros.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[amix]")
            amix = "[amix]"
        else:
            amix = mix[0]
        filtros.append(f"{amix}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,atrim=0:{total:.3f}[aout]")

        saida_tmp = tmp / "final.mp4"
        progresso("Montando o vídeo final (transições, legendas, trilha)…", 0.56)
        _ffmpeg(args + ["-filter_complex", ";".join(filtros), "-map", "[vout]", "-map", "[aout]",
                        "-r", str(FPS), *_codec_video(qualidade), "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", "-t", f"{total:.3f}", str(saida_tmp)],
                total, lambda f: progresso("Montando o vídeo final (transições, legendas, trilha)…", 0.56 + 0.43 * f),
                cancelado, cwd=str(tmp))
        Path(saida).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(saida_tmp), saida)
    info = media.probe(saida)
    progresso("Vídeo pronto.", 1.0)
    return {"arquivo": saida, "duracao": info["duracao"], "largura": info["largura"], "altura": info["altura"],
            "avisos": avisos, "codificador": "NVENC (placa de vídeo)" if nvenc_funciona() else "x264 (processador)"}
