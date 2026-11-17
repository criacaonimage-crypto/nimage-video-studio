@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Verificando o NIMAGE VIDEO STUDIO... (leva cerca de 1 minuto)
echo.
"%~dp0python\python.exe" "%~dp0app\run.py" --autoteste
echo.
echo Se apareceu algum erro acima, tire um print desta janela e envie.
pause
