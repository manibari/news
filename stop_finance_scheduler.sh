#!/bin/bash
# 停止金融數據排程

cd "$(dirname "$0")"

if [ -f "finance_scheduler.pid" ]; then
    PID=$(cat finance_scheduler.pid)
    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        rm finance_scheduler.pid
        echo "金融數據排程已停止 (PID: $PID)"
    else
        rm finance_scheduler.pid
        echo "排程已不在執行中"
    fi
else
    echo "找不到 PID 檔案，排程可能未啟動"
fi
