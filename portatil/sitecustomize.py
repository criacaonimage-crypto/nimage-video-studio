"""Abre o NIMAGE VIDEO STUDIO quando o Python portátil é iniciado como "NIMAGE VIDEO STUDIO.exe".

O "NIMAGE VIDEO STUDIO.exe" é uma cópia do pythonw.exe oficial (com a assinatura digital da Python
Software Foundation), por isso o Controle de Aplicativo Inteligente do Windows permite abri-lo.
"""
import os
import sys
from pathlib import Path


def _desbloquear_arquivos():
    """Remove a marca "veio da internet" (Zone.Identifier) dos arquivos do programa, uma única vez.

    Sem essa marca, o Windows não trata as bibliotecas do pacote como downloads desconhecidos.
    Roda antes de qualquer biblioteca de terceiros ser carregada.
    """
    if os.name != "nt":
        return
    raiz = Path(sys.executable).resolve().parent.parent      # pasta NimageVideoStudio
    marca = raiz / "lib" / ".desbloqueado"
    if marca.exists():
        return
    for pasta in ("programa", "lib", "app"):
        for dirpath, _dirs, arquivos in os.walk(raiz / pasta):
            for nome in arquivos:
                try:
                    os.remove(os.path.join(dirpath, nome) + ":Zone.Identifier")
                except OSError:
                    pass
    try:
        marca.write_text("ok", encoding="ascii")
    except OSError:
        pass


try:
    _desbloquear_arquivos()
except Exception:  # noqa: BLE001
    pass

if Path(sys.executable).stem.lower().startswith("nimage"):
    codigo = 0
    try:
        if not getattr(sys, "argv", None):
            sys.argv = [sys.executable]
        from nimage_studio.main import main
        codigo = main() or 0
    except SystemExit as e:
        codigo = e.code if isinstance(e.code, int) else 0
    except BaseException:  # noqa: BLE001 - registra qualquer falha e mostra na tela
        import traceback
        detalhe = traceback.format_exc()
        try:
            from nimage_studio import config
            with open(config.data_dir() / "erros.log", "a", encoding="utf-8") as f:
                f.write(detalhe + "\n")
        except Exception:  # noqa: BLE001
            pass
        try:
            import ctypes
            bloqueio = "Controle de Aplicativo" in detalhe or "4551" in detalhe
            msg = ("O Windows (Controle de Aplicativo Inteligente) bloqueou uma parte do programa.\n\n"
                   if bloqueio else "O NIMAGE VIDEO STUDIO não conseguiu abrir.\n\n")
            msg += "\n".join(detalhe.strip().splitlines()[-4:]) + "\n\nTire um print desta mensagem e envie ao suporte."
            ctypes.windll.user32.MessageBoxW(None, msg, "NIMAGE VIDEO STUDIO", 0x10)
        except Exception:  # noqa: BLE001
            pass
        codigo = 1
    for s in (sys.stdout, sys.stderr):
        try:
            if s:
                s.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(codigo)
