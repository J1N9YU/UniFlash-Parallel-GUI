@echo off
chcp 65001 > nul
color 0A
cls

echo.
echo ================= 启动应用 =================
echo 正在检查Python环境...
python --version > nul 2>&1
if %errorlevel% neq 0 (
    set "PY_CMD=python"
) else (
    py --version > nul 2>&1
    if %errorlevel% neq 0 (
        set "PY_CMD=py"
    ) else (
        echo 【错误】未检测到Python环境，请先运行 install.bat
        pause
        exit /b 1
    )
)

:: 检查app.py是否存在
if not exist "app.py" (
    echo 【错误】未找到 app.py 文件！
    echo 请确保该文件与 run.bat 在同一目录
    pause
    exit /b 1
)

:: 自动打开浏览器
echo 正在打开浏览器...
start http://127.0.0.1:5000/

:: 启动应用
echo.
echo ==============================================
echo 应用已启动，访问地址：http://127.0.0.1:5000/
echo 停止服务请按 Ctrl + C，然后输入 y 确认
echo ==============================================
echo.
%PY_CMD% app.py

echo.
echo 应用已停止
pause
exit /b 0
