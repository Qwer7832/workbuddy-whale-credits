#!/bin/bash
# 小鲸鱼积分挂件 —— Linux 安装引导
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 install.py "$@"
elif command -v python >/dev/null 2>&1; then
  python install.py "$@"
else
  echo "未找到 Python 3。请先安装 python3 与 python3-tk。"
  exit 1
fi
