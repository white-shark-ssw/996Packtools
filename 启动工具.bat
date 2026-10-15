@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 goto python
uv run --with lupa==2.8 python ws_pack_tool.py
goto finished
:python
python ws_pack_tool.py
:finished
if errorlevel 1 (
    echo.
    echo 启动失败，请查看上方错误。Python环境启用保护前需安装 requirements.txt 中的依赖。
    pause
)
