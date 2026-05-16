@echo off
chcp 65001 >nul
echo ============================================
echo   IC-9700 CI-V 控制器 - 打包工具
echo ============================================
echo.

REM Check pyinstaller
where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [安装] pip install pyinstaller
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo 错误: PyInstaller 安装失败
        pause
        exit /b 1
    )
)

echo.
echo [清理] 删除旧构建...
if exist dist\ rd /s /q dist
if exist build\ rd /s /q build
if exist *.spec del /q *.spec

echo.
echo [打包] 正在创建 ic9700-ctrl.exe ...
pyinstaller --onefile ^
    --name "ic9700-ctrl" ^
    --add-data "static;static" ^
    --hidden-import uvicorn ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import fastapi ^
    --hidden-import serial ^
    --hidden-import serial.tools.list_ports ^
    --collect-all uvicorn ^
    --console ^
    app.py

if %errorlevel% neq 0 (
    echo.
    echo 错误: 打包失败
    pause
    exit /b 1
)

echo.
echo [完成] dist\ic9700-ctrl.exe 已生成
echo.
echo 使用方法:
echo   1. 将 dist\ic9700-ctrl.exe 复制到任意位置
echo   2. 双击运行
echo   3. 浏览器会自动打开控制页面
echo   4. 输入电台 IP 地址，点击"连接"
echo.
pause
