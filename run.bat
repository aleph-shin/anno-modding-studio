@echo off
REM Launch Anno Modding Studio.
setlocal
pushd "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)

%PY% -c "import PySide6, lxml" >nul 2>nul
if not %errorlevel%==0 (
    echo Installing dependencies ^(one time^)...
    %PY% -m pip install -r requirements.txt
    if not %errorlevel%==0 (
        echo.
        echo Could not install dependencies. Install Python 3.10+ and try again.
        pause
        popd
        exit /b 1
    )
)

REM pythonw keeps the console from lingering behind the window.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m anno_modding_studio %*
) else (
    %PY% -m anno_modding_studio %*
)
popd
endlocal
