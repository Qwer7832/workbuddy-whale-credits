#!/bin/bash
# 小鲸鱼积分挂件 —— macOS 安装引导（双击即可）
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 install.py "$@"
elif command -v python >/dev/null 2>&1; then
  python install.py "$@"
else
  echo "未找到 Python 3。请先用 python.org 安装包或 brew install python-tk 安装。"
fi
echo
read -n 1 -s -r -p "按任意键关闭窗口..."
echo
