#!/bin/zsh
# Daily steal-deals routine, run by launchd every morning.
# Logs to logs/daily_YYYY-MM-DD.log for debugging.

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")"

mkdir -p logs
LOG="logs/daily_$(date +%Y-%m-%d).log"

{
  echo "=== steal-deals daily run: $(date) ==="
  python3 refresh_deals.py && python3 share_deals.py --post
  echo "=== finished: $(date) (exit $?) ==="
} >> "$LOG" 2>&1

# keep the last 14 logs
ls -t logs/daily_*.log 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null
exit 0
