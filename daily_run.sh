#!/bin/zsh
# Daily steal-deals routine, run by launchd every morning.
# Logs to logs/daily_YYYY-MM-DD.log for debugging.

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")"

mkdir -p logs
LOG="logs/daily_$(date +%Y-%m-%d).log"

# theme rotates through the day's four slots (8:00 / 12:30 / 16:30 / 19:30)
HOUR=$(date +%H)
if   [ "$HOUR" -lt 11 ]; then THEME=top
elif [ "$HOUR" -lt 15 ]; then THEME=budget
elif [ "$HOUR" -lt 18 ]; then THEME=gadgets
else                          THEME=homefashion
fi
TODAY=$(date +%Y-%m-%d)

{
  echo "=== steal-deals daily run: $(date) [theme: $THEME] ==="

  # scrape + channel post once per day, whichever slot runs first
  # (covers late wake-ups: a missed 8 AM slot refreshes at noon instead)
  if ! grep -q "\"updated_at\": \"$TODAY" deals/index.json 2>/dev/null; then
    python3 refresh_deals.py && python3 share_deals.py --post
  else
    echo "[refresh] already ran today — skipping scrape"
  fi

  # themed reel + YouTube Short (skips gracefully until yt_token.json exists)
  if python3 make_reel.py --theme "$THEME"; then
    python3 upload_youtube.py \
      --video "reels/reel_${TODAY}_${THEME}.mp4" \
      --caption "reels/caption_${TODAY}_${THEME}.txt" \
      || echo "[youtube] upload skipped/failed — see above"
  fi
  echo "=== finished: $(date) (exit $?) ==="
} >> "$LOG" 2>&1

# keep the last 14 logs
ls -t logs/daily_*.log 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null
exit 0
