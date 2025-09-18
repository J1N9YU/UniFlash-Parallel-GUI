@echo off
echo 正在启动多路烧录控制系统...

:: 切换到脚本所在目录（确保路径正确）
cd /d "%~dp0"

:: 检查app.py是否存在
if not exist "app.py" (
    echo 错误：未找到app.py，请确保脚本与程序文件在同一目录
    pause
    exit /b 1
)

:: 启动Python程序（会自动打开浏览器）
python app.py

:: 程序退出后暂停，方便查看错误信息
echo 程序已停止运行
pause