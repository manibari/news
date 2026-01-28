#!/bin/bash
# 新聞收集排程停止腳本

cd "$(dirname "$0")"

if [ -f .scheduler.pid ]; then
    PID=$(cat .scheduler.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "停止排程 (PID: $PID)..."
        kill $PID
        rm .scheduler.pid
        echo "排程已停止"
    else
        echo "排程未在執行"
        rm .scheduler.pid
    fi
else
    # 嘗試找到並停止所有相關程序
    PIDS=$(pgrep -f "python main.py --schedule")
    if [ -n "$PIDS" ]; then
        echo "找到執行中的排程，正在停止..."
        pkill -f "python main.py --schedule"
        echo "排程已停止"
    else
        echo "排程未在執行"
    fi
fi
