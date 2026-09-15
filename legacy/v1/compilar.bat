@echo off
REM ============================================================
REM  Recompila ConsolaOBS.exe DIRECTO en:
REM      D:\Escritorio\panel de sonidos\dist\ConsolaOBS.exe
REM  (siempre con el icono asignado).
REM
REM  Sin pasos de copia intermedios: PyInstaller compila
REM  directamente en esa carpeta con --distpath. En modo --onefile
REM  PyInstaller solo crea/pisa el .exe ahi, no toca ni borra el
REM  resto de lo que haya en esa carpeta (assets\, los .json de
REM  configuracion, etc.).
REM
REM  Poner este archivo en la carpeta donde esta consola_de_sonido.py
REM  y doble clic cada vez que cambies el codigo.
REM ============================================================

cd /d "%~dp0"

set CARPETA_DESTINO=D:\Escritorio\panel de sonidos\dist
set CARPETA_BUILD=%~dp0_build_tmp

REM El icono se busca directamente en esta misma carpeta (donde esta
REM este .bat y consola_de_sonido.py), no en assets\iconos\. Si no
REM esta ahi, se compila igual pero sin icono, avisando por que.
set ICONO=%~dp0app_icon.ico

echo Archivo que se va a compilar:
echo   %~dp0consola_de_sonido.py
for %%A in (consola_de_sonido.py) do echo Ultima modificacion: %%~tA
echo.
echo (Si esa fecha/hora no es de ahora hace un rato, este NO es el
echo  archivo que pensás que es: revisá que estés editando/pisando
echo  el consola_de_sonido.py que está en esta misma carpeta.)
echo.

if exist "%ICONO%" (
    echo Icono encontrado:
    echo   %ICONO%
    set OPCION_ICONO=--icon "%ICONO%"
) else (
    echo ======================================================
    echo  AVISO: no se encontro app_icon.ico en esta carpeta:
    echo    %~dp0
    echo  Se va a compilar SIN icono asignado.
    echo  Poné el archivo app_icon.ico acá al lado de este .bat
    echo  y volvé a ejecutarlo para que quede con icono.
    echo ======================================================
    set OPCION_ICONO=
)
echo.

echo Borrando restos de compilaciones anteriores...
rmdir /s /q "%CARPETA_BUILD%" 2>nul
del ConsolaOBS.spec 2>nul

if not exist "%CARPETA_DESTINO%" mkdir "%CARPETA_DESTINO%"

echo.
echo Compilando ConsolaOBS.exe...
py -m PyInstaller --onefile --windowed --name ConsolaOBS %OPCION_ICONO% --distpath "%CARPETA_DESTINO%" --workpath "%CARPETA_BUILD%" consola_de_sonido.py

if not exist "%CARPETA_DESTINO%\ConsolaOBS.exe" (
    echo.
    echo ======================================================
    echo  ALGO FALLO. Revisa los mensajes de arriba en rojo.
    echo ======================================================
    pause
    exit /b 1
)

if not exist "%CARPETA_DESTINO%\assets" (
    echo Copiando assets (primera vez en esta carpeta)...
    xcopy assets "%CARPETA_DESTINO%\assets" /E /I /Y >nul
)

echo Limpiando carpeta temporal...
rmdir /s /q "%CARPETA_BUILD%" 2>nul
del ConsolaOBS.spec 2>nul

echo.
echo ======================================================
echo  Listo. El ejecutable esta en: %CARPETA_DESTINO%\ConsolaOBS.exe
echo  Fecha/hora del .exe generado:
for %%A in ("%CARPETA_DESTINO%\ConsolaOBS.exe") do echo    %%~tA
echo ======================================================
pause
