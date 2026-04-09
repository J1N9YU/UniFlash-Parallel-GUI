@echo off
:: 强制设置为中文Windows默认编码（936=GBK），解决乱码
chcp 936 > nul
:: 设置控制台颜色和清屏
color 0A
cls

echo.
echo ================= 启动应用 =================
echo 正在检查Python环境...

:: 优先检查python命令
python --version > nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    echo 检测到Python环境：python
) else (
    :: 其次检查py命令
    py --version > nul 2>&1
    if %errorlevel% equ 0 (
        set "PY_CMD=py"
        echo 检测到Python环境：py
    ) else (
        :: 未找到Python时的中文提示（ANSI编码下正常显示）
        echo 【错误】未检测到Python环境，请先安装Python并配置到环境变量！
        echo 或先运行 install.bat 安装依赖
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