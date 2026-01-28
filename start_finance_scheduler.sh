#!/bin/bash
# 啟動金融數據排程

cd "$(dirname "$0")"

# 檢查是否已在執行
if [ -f "finance_scheduler.pid" ]; then
    PID=$(cat finance_scheduler.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "排程已在執行中 (PID: $PID)"
        exit 1
    fi
fi

# 背景執行排程
nohup python finance_scheduler.py --schedule >> logs/finance_scheduler.log 2>&1 &
echo $! > finance_scheduler.pid

echo "金融數據排程已啟動"
echo "PID: $(cat finance_scheduler.pid)"
echo "日誌: logs/finance_scheduler.log"
echo ""
echo "排程時間:"
echo "  - 06:00 收集所有市場數據 (美股收盤後)"
echo "  - 14:30 收集台股數據 (台股收盤後)"
