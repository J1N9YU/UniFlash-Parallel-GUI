@echo off
setlocal enabledelayedexpansion

:: 检查Python是否已安装
echo 检查Python环境...
where python >nul 2>nul
if %errorlevel% equ 0 (
    echo Python已安装，开始安装依赖库...
) else (
    echo 未找到Python，正在下载安装...
    
    :: 下载Python 3.11.4（可替换为其他版本）
    set "python_url=https://www.python.org/ftp/python/3.11.4/python-3.11.4-amd64.exe"
    set "installer=%temp%\python_installer.exe"

    :: 优先用curl下载，无则用bitsadmin
    if exist "%SystemRoot%\system32\curl.exe" (
        curl -o "!installer!" "!python_url!" --ssl-no-revoke
    ) else (
        bitsadmin /transfer pythonDownload /download /priority normal "!python_url!" "!installer!"
    )

    :: 静默安装并添加到环境变量
    echo 安装Python中...
    "!installer!" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del "!installer!" /f /q
    echo Python安装完成，开始安装依赖库...
)

:: 安装app.py所需的第三方库
pip install flask flask-cors

echo 所有依赖安装完成！
pause