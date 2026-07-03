@echo off
chcp 65001 >nul
cd /d %~dp0backend

if not exist .env (
  echo [初始化] 复制 .env.example 为 .env
  copy .env.example .env >nul
)

if not exist .venv (
  echo [初始化] 创建虚拟环境并安装依赖（首次较慢）...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -U pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

python run.py
pause
