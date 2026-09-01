#!/bin/zsh
# Daily steal-deals routine, run by launchd every morning.
# Logs to logs/daily_YYYY-MM-DD.log for debugging.

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# system python has the deps (requests, bs4, google-api); Homebrew's 3.14 does not —
# under launchd, bare `python3` resolved to Homebrew and YouTube uploads failed silently
PYTHON=/usr/bin/python3
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
    $PYTHON refresh_deals.py && $PYTHON share_deals.py --post
  else
    echo "[refresh] already ran today — skipping scrape"
  fi

  # themed reel + YouTube Short (skips gracefully until yt_token.json exists)
  if $PYTHON make_reel.py --theme "$THEME"; then
    if ! $PYTHON upload_youtube.py \
      --video "reels/reel_${TODAY}_${THEME}.mp4" \
      --caption "reels/caption_${TODAY}_${THEME}.txt"; then
      echo "[youtube] upload FAILED — see above"
      # surface it on screen; a silent failure once cost 7 weeks of uploads
      osascript -e 'display notification "YouTube upload failed — check steal-deals/logs" with title "Loot Bazaar daily run" sound name "Basso"' 2>/dev/null
    fi
  fi
  echo "=== finished: $(date) (exit $?) ==="
} >> "$LOG" 2>&1

# keep the last 14 logs
ls -t logs/daily_*.log 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null
exit 0
