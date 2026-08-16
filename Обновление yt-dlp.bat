@echo off
chcp 866 >nul
setlocal EnableExtensions EnableDelayedExpansion
title Обновление yt-dlp
color 0B

if /i "%~1"=="--self-test" (
    echo self-test: ok
    exit /b 0
)

set "SCRIPT_DIR=%~dp0"
set "LOCAL_EXE=%SCRIPT_DIR%yt-dlp.exe"
set "DOWNLOAD_URL=https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

echo ============================================
echo Обновление yt-dlp
echo ============================================
echo.
echo Этот файл обновляет yt-dlp для этой программы.
echo.

if exist "%LOCAL_EXE%" (
    echo Найден локальный файл:
    echo "%LOCAL_EXE%"
    call :PrintVersion "%LOCAL_EXE%" "Текущая версия"
    echo.
    echo Обновляю локальный yt-dlp.exe...
    "%LOCAL_EXE%" -U
    if errorlevel 1 (
        color 0C
        echo.
        echo Не удалось обновить локальный yt-dlp.exe.
        echo Запустите BAT от имени администратора, закройте программы, которые используют yt-dlp,
        echo и проверьте интернет.
        goto :FAIL
    )
    echo.
    call :PrintVersion "%LOCAL_EXE%" "Версия после обновления"
    goto :SUCCESS
)

echo Рядом с BAT-файлом нет локального yt-dlp.exe.
echo Обновляю установку yt-dlp через Python/pip.
echo.

call :FindPython
if errorlevel 1 (
    color 0E
    echo Python не найден.
    echo.
    echo Если хотите скачать standalone yt-dlp.exe в эту папку, нажмите Y.
    set /p "DL_CHOICE=Скачать yt-dlp.exe сейчас? [Y/N]: "
    if /i "!DL_CHOICE!"=="Y" (
        call :DownloadExe
        if errorlevel 1 goto :FAIL
        goto :SUCCESS
    )
    goto :FAIL
)

echo Команда Python: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

%PYTHON_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip не найден. Пробую включить pip...
    %PYTHON_CMD% -m ensurepip --upgrade
    if errorlevel 1 (
        color 0C
        echo pip недоступен для этой установки Python.
        goto :FAIL
    )
)

set "OLD_VER="
for /f "delims=" %%V in ('yt-dlp --version 2^>nul') do set "OLD_VER=%%V"
if defined OLD_VER (
    echo Текущая версия yt-dlp: !OLD_VER!
) else (
    echo yt-dlp пока не найден в PATH. pip установит его.
)
echo.

echo Выполняю:
echo %PYTHON_CMD% -m pip install --upgrade yt-dlp
echo.
%PYTHON_CMD% -m pip install --upgrade yt-dlp
if errorlevel 1 (
    color 0C
    echo.
    echo Не удалось обновить через pip.
    echo.
    echo Можно попробовать вручную:
    echo %PYTHON_CMD% -m pip install --user --upgrade yt-dlp
    goto :FAIL
)

set "NEW_VER="
for /f "delims=" %%V in ('yt-dlp --version 2^>nul') do set "NEW_VER=%%V"
if defined NEW_VER (
    echo.
    if defined OLD_VER echo Было: !OLD_VER!
    echo Стало: !NEW_VER!
) else (
    color 0E
    echo.
    echo pip завершился, но команда yt-dlp все еще не найдена в PATH.
    echo Закройте это окно и откройте новый терминал, либо добавьте Python Scripts в PATH.
)
goto :SUCCESS

:FindPython
set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3"
        exit /b 0
    )
)
where python >nul 2>&1
if not errorlevel 1 (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
        exit /b 0
    )
)
exit /b 1

:PrintVersion
set "TOOL_PATH=%~1"
set "LABEL=%~2"
set "FOUND_VER="
for /f "delims=" %%V in ('"%TOOL_PATH%" --version 2^>nul') do set "FOUND_VER=%%V"
if defined FOUND_VER (
    echo %LABEL%: !FOUND_VER!
) else (
    echo %LABEL%: неизвестно
)
exit /b 0

:DownloadExe
echo.
echo Скачиваю standalone yt-dlp.exe...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile '%LOCAL_EXE%'"
if errorlevel 1 (
    color 0C
    echo Не удалось скачать файл.
    exit /b 1
)
call :PrintVersion "%LOCAL_EXE%" "Скачанная версия"
exit /b 0

:SUCCESS
color 0A
echo.
echo Готово.
echo.
pause
exit /b 0

:FAIL
echo.
echo Обновление не выполнено.
echo.
pause
exit /b 1
