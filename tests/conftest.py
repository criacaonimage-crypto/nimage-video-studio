"""Encerramento dos testes.

Os testes abrem várias janelas (com player de vídeo) no mesmo processo. Na finalização do Python,
o Qt pode falhar ao destruí-las em ordem arbitrária e devolver código de erro mesmo com todos os
testes aprovados. O programa real não tem esse problema (uma janela, saída normal). Aqui, depois
de terminar tudo, saímos direto com o resultado do pytest.
"""
import os
import sys

_status = {"codigo": 0}


def pytest_sessionfinish(session, exitstatus):
    _status["codigo"] = int(exitstatus)
    try:
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(15000)
    except ImportError:
        pass


def pytest_unconfigure(config):
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_status["codigo"])
