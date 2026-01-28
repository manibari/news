#!/bin/bash
# 新聞收集排程啟動腳本

cd "$(dirname "$0")"

# 檢查是否已經在執行
if pgrep -f "python main.py --schedule" > /dev/null; then
    echo "排程已經在執行中！"
    echo "使用 ./stop_scheduler.sh 停止"
    exit 1
fi

echo "啟動新聞收集排程..."
echo "執行時間: 每日 08:00 和 20:00"
echo ""

# 背景執行並將輸出寫入 log 檔案
nohup python main.py --schedule >> logs/scheduler.log 2>&1 &

# 記錄 PID
echo $! > .scheduler.pid

echo "排程已在背景啟動！"
echo "PID: $(cat .scheduler.pid)"
echo ""
echo "查看日誌: tail -f logs/scheduler.log"
echo "停止排程: ./stop_scheduler.sh"
