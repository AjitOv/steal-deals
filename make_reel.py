#!/usr/bin/env python3
"""
Generate a vertical (1080x1920) deals reel from the bundled deal data.

Slides are rendered as HTML and screenshotted with headless Chrome (crisp
text, no image libraries needed), then stitched by ffmpeg with a slow zoom.
Output: reels/reel_YYYY-MM-DD.mp4 + reels/caption_YYYY-MM-DD.txt

Silent by default — drop a royalty-free track at assets/music.mp3 to add
audio (Instagram reach is better if you add trending audio in-app instead).

Usage:
    python3 make_reel.py               # top 5 deals, ~19s reel
    python3 make_reel.py --deals 3
"""

import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date

import requests

import scraper
from share_deals import pick_deals  # same quality filter as the daily message

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REELS_DIR = os.path.join(BASE_DIR, "reels")
MUSIC = os.path.join(BASE_DIR, "assets", "music.mp3")
MUSIC_DIR = os.path.join(BASE_DIR, "assets", "music")
SITE = "https://steal-deals.onrender.com"


def pick_music(theme):
    """assets/music.mp3 wins; else rotate assets/music/*.mp3 by day+theme."""
    if os.path.exists(MUSIC):
        return MUSIC
    tracks = sorted(glob.glob(os.path.join(MUSIC_DIR, "*.mp3")))
    if not tracks:
        return None
    idx = (date.today().toordinal() + sorted(THEMES).index(theme)) % len(tracks)
    return tracks[idx]

W, H, FPS = 1080, 1920, 30
INTRO_S, DEAL_S, OUTRO_S = 2.2, 3.0, 2.5

GADGET_KWS = {"Electronics", "Headphones", "Smart Watches", "Mobiles", "Laptops"}
THEMES = {
    # theme: (deal filter, intro heading, caption lead)
    "top": (lambda d: True,
            "TOP {n} AMAZON<br>STEALS TODAY",
            "🔥 Top {n} Amazon steals today"),
    "budget": (lambda d: d["price"] <= 300,
               "{n} THINGS UNDER<br>₹300 ON AMAZON",
               "🤑 {n} things under ₹300 on Amazon"),
    "gadgets": (lambda d: d.get("keyword") in GADGET_KWS,
                "{n} GADGET STEALS<br>ON AMAZON",
                "⚡ {n} gadget steals on Amazon"),
    "homefashion": (lambda d: d.get("keyword") not in GADGET_KWS,
                    "{n} HOME &amp; FASHION<br>STEALS TODAY",
                    "🏠 {n} home & fashion steals today"),
}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
  * {{ margin:0; box-sizing:border-box; font-family:-apple-system,'Segoe UI',sans-serif; }}
  body {{ width:{w}px; height:{h}px; overflow:hidden; color:#fff;
         background:linear-gradient(160deg,#1a1a2e 0%,#16213e 55%,#0f3460 100%);
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; text-align:center; padding:60px; }}
  {css}
</style></head><body>{body}</body></html>"""

INTRO_CSS = """
  .fire { font-size:160px; }
  h1 { font-size:110px; font-weight:900; line-height:1.15; margin:40px 0; }
  .pct { color:#ffd166; font-size:130px; font-weight:900; }
  .sub { font-size:52px; color:#9ad5ca; }"""
INTRO_BODY = """
  <div class="fire">🔥</div>
  <h1>{heading}</h1>
  <div class="pct">up to {maxpct}% OFF</div>
  <p class="sub">all ★4.0+ rated · honest prices</p>"""

DEAL_CSS = """
  .card { background:#fff; border-radius:48px; padding:50px; width:820px;
          height:820px; display:flex; align-items:center; justify-content:center; }
  .card img { max-width:100%; max-height:100%; object-fit:contain; }
  .badge { position:absolute; top:180px; right:80px; background:#e63946;
           border-radius:50%; width:280px; height:280px; display:flex;
           flex-direction:column; align-items:center; justify-content:center;
           transform:rotate(12deg); box-shadow:0 20px 60px rgba(230,57,70,.45); }
  .badge b { font-size:96px; font-weight:900; }
  .badge span { font-size:44px; }
  h2 { font-size:56px; font-weight:700; line-height:1.25; margin:60px 0 30px;
       max-height:2.6em; overflow:hidden; }
  .price { font-size:110px; font-weight:900; color:#ffd166; }
  .mrp { font-size:56px; color:#8899aa; text-decoration:line-through; margin-left:28px; }
  .stars { font-size:48px; color:#9ad5ca; margin-top:24px; }"""
DEAL_BODY = """
  <div class="card"><img src="{img}"></div>
  <div class="badge"><b>-{discount}%</b><span>OFF</span></div>
  <h2>{title}</h2>
  <div><span class="price">₹{price:,.0f}</span><span class="mrp">₹{mrp:,.0f}</span></div>
  <div class="stars">★ {rating} · {reviews:,} ratings</div>"""

OUTRO_CSS = """
  .bell { font-size:150px; }
  h1 { font-size:96px; font-weight:900; margin:40px 0; line-height:1.2; }
  .link { font-size:54px; color:#ffd166; font-weight:700; }
  .sub { font-size:46px; color:#9ad5ca; margin-top:30px; }"""
OUTRO_BODY = """
  <div class="bell">🔔</div>
  <h1>FOLLOW FOR<br>DAILY STEALS</h1>
  <div class="link">🛒 links in description &amp; comments 👇</div>
  <p class="sub">new deals every morning</p>"""


def shoot(chrome, html_text, out_png, tmp):
    src = os.path.join(tmp, os.path.basename(out_png) + ".html")
    with open(src, "w") as f:
        f.write(html_text)
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={W},{H}", f"--screenshot={out_png}",
         "--virtual-time-budget=4000", f"file://{src}"],
        capture_output=True, timeout=60, check=True)


def clip(png, seconds, out_mp4):
    frames = int(seconds * FPS)
    # single-frame input: zoompan holds it for d output frames — feeding it
    # a looped stream multiplies the duration (d frames PER input frame)
    vf = (f"scale={W*2}:{H*2},"
          f"zoompan=z='min(zoom+0.0009,1.10)':d={frames}"
          f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", png,
         "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "fast",
         "-pix_fmt", "yuv420p", out_mp4],
        check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--deals", type=int, default=5)
    ap.add_argument("--theme", choices=sorted(THEMES), default="top")
    args = ap.parse_args()

    chrome = scraper.find_chrome()
    if not chrome:
        sys.exit("No Chrome found — slides are rendered with headless Chrome.")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found — brew install ffmpeg")

    theme_filter, heading_tpl, lead_tpl = THEMES[args.theme]
    deals = [d for d in pick_deals(40) if theme_filter(d)][:args.deals]
    if not deals:
        sys.exit(f"No deals pass the '{args.theme}' filter — refresh first.")

    os.makedirs(REELS_DIR, exist_ok=True)
    stamp = date.today().isoformat()
    out_mp4 = os.path.join(REELS_DIR, f"reel_{stamp}_{args.theme}.mp4")

    with tempfile.TemporaryDirectory() as tmp:
        slides = []

        s = os.path.join(tmp, "00_intro.png")
        shoot(chrome, PAGE.format(w=W, h=H, css=INTRO_CSS, body=INTRO_BODY.format(
            heading=heading_tpl.format(n=len(deals)),
            maxpct=max(d["discount"] for d in deals))), s, tmp)
        slides.append((s, INTRO_S))

        for i, d in enumerate(deals, 1):
            img_path = os.path.join(tmp, f"prod{i}.jpg")
            # search thumbnails are ~200px; the size token in the URL is
            # swappable, so ask the CDN for an 800px render instead
            img_url = re.sub(r"\._[^./]+_\.", "._SL800_.", d.get("image", ""))
            try:
                r = requests.get(img_url, timeout=20)
                r.raise_for_status()
                with open(img_path, "wb") as f:
                    f.write(r.content)
                img_src = f"file://{img_path}"
            except Exception:
                img_src = ""  # empty card beats a broken reel
            s = os.path.join(tmp, f"{i:02d}_deal.png")
            shoot(chrome, PAGE.format(w=W, h=H, css=DEAL_CSS, body=DEAL_BODY.format(
                img=img_src, discount=d["discount"],
                title=html.escape(d["title"][:80]),
                price=d["price"], mrp=d["mrp"],
                rating=d.get("rating") or "—", reviews=d.get("reviews") or 0)), s, tmp)
            slides.append((s, DEAL_S))
            print(f"[slide] {i}/{len(deals)} {d['title'][:40]}")

        s = os.path.join(tmp, "99_outro.png")
        shoot(chrome, PAGE.format(w=W, h=H, css=OUTRO_CSS, body=OUTRO_BODY), s, tmp)
        slides.append((s, OUTRO_S))

        clips = []
        for png, seconds in slides:
            c = png.replace(".png", ".mp4")
            clip(png, seconds, c)
            clips.append(c)

        concat_list = os.path.join(tmp, "list.txt")
        with open(concat_list, "w") as f:
            f.writelines(f"file '{c}'\n" for c in clips)
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", concat_list]
        music = pick_music(args.theme)
        if music:
            print(f"[music] {os.path.basename(music)}")
            cmd += ["-stream_loop", "-1", "-i", music, "-shortest",
                    "-c:a", "aac", "-b:a", "128k"]
        cmd += ["-c:v", "copy", "-movflags", "+faststart", out_mp4]
        subprocess.run(cmd, check=True)

    caption = os.path.join(REELS_DIR, f"caption_{stamp}_{args.theme}.txt")
    lines = [f"{lead_tpl.format(n=len(deals))} — up to "
             f"{max(d['discount'] for d in deals)}% OFF! #shorts", ""]
    lines += [f"{i}. {d['title'][:60]} — ₹{d['price']:,.0f} ({d['discount']}% off)\n"
              f"   {SITE}/go/{d['asin']}?src=yt"
              for i, d in enumerate(deals, 1)]
    lines += ["", f"All deals: {SITE}",
              "📲 Telegram: https://t.me/lootbazaardealsa",
              "💬 WhatsApp: https://whatsapp.com/channel/0029VaI5CV93AzNUiZ5Tt226",
              "",
              "As an Amazon Associate I earn from qualifying purchases. #ad #affiliate",
              "#amazondeals #dealsindia #loot #amazonfinds #steals"]
    with open(caption, "w") as f:
        f.write("\n".join(lines))

    dur = INTRO_S + DEAL_S * len(deals) + OUTRO_S
    print(f"\nReel: {out_mp4} (~{dur:.0f}s)\nCaption: {caption}")


if __name__ == "__main__":
    main()
