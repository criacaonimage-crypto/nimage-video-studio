"""Abre o NIMAGE VIDEO STUDIO quando o Python portátil é iniciado como "NIMAGE VIDEO STUDIO.exe".

O "NIMAGE VIDEO STUDIO.exe" é uma cópia do pythonw.exe oficial (com a assinatura digital da Python
Software Foundation), por isso o Controle de Aplicativo Inteligente do Windows permite abri-lo.
"""
import os
import sys
from pathlib import Path

if Path(sys.executable).stem.lower().startswith("nimage"):
    codigo = 0
    try:
        if not getattr(sys, "argv", None):
            sys.argv = [sys.executable]
        from nimage_studio.main import main
        codigo = main() or 0
    except SystemExit as e:
        codigo = e.code if isinstance(e.code, int) else 0
    except BaseException:  # noqa: BLE001 - registra qualquer falha para diagnóstico
        import traceback
        try:
            from nimage_studio import config
            with open(config.data_dir() / "erros.log", "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
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
