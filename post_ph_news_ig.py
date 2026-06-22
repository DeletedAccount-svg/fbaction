"""
post_ph_news_ig.py
==================
Philippine News Instagram Carousel Poster — Peso Weekly Style
Pulls from multiple PH RSS feeds, generates branded slide images,
and posts as an Instagram Carousel via the Graph API.

Required GitHub Secrets:
  IG_USER_ID       — Instagram Business/Creator User ID (from Graph API)
  FB_ACCESS_TOKEN  — Facebook Page Access Token with instagram_content_publish
  IMGBB_API_KEY    — Free at imgbb.com (used to host generated images publicly)
  PAGE_NAME        — Your Instagram handle WITHOUT the @, e.g. yourpage.ph

Optional:
  GROQ_API_KEY     — Free at console.groq.com — gives better Taglish slide content
"""

import os, sys, json, random, requests, re, time, base64
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from html import unescape

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
IG_USER_ID      = os.environ["IG_USER_ID"]
FB_ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
IMGBB_API_KEY   = os.environ["IMGBB_API_KEY"]
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
PAGE_NAME       = os.environ.get("PAGE_NAME", "yourpage.ph")

IMG_W, IMG_H    = 1080, 1080
IG_BASE         = "https://graph.facebook.com/v21.0"

FONT_BOLD_URL   = "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf"
FONT_REG_URL    = "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf"
FONT_BOLD_PATH  = "/tmp/Poppins-Bold.ttf"
FONT_REG_PATH   = "/tmp/Poppins-Regular.ttf"

# ─────────────────────────────────────────────────────────────────────────────
# RSS FEEDS  (add/remove freely)
# ─────────────────────────────────────────────────────────────────────────────
FEEDS = [
    # General news
    {"url": "https://www.rappler.com/feed/",                   "category": "BALITA"},
    {"url": "https://newsinfo.inquirer.net/feed",              "category": "BALITA"},
    {"url": "https://www.philstar.com/rss/headlines",          "category": "BALITA"},
    {"url": "https://www.gmanetwork.com/news/rss/latest.xml",  "category": "BALITA"},
    # Politics
    {"url": "https://www.rappler.com/nation/feed/",            "category": "PULITIKA"},
    {"url": "https://nation.inquirer.net/feed",                "category": "PULITIKA"},
    # Economy / Personal Finance
    {"url": "https://business.inquirer.net/feed",              "category": "PERA"},
    {"url": "https://www.bworldonline.com/feed/",              "category": "NEGOSYO"},
    {"url": "https://businessmirror.com.ph/feed/",             "category": "NEGOSYO"},
    # Entertainment / Chismis
    {"url": "https://www.pep.ph/rss",                         "category": "CHISMIS"},
    {"url": "https://entertainment.inquirer.net/feed",         "category": "CHISMIS"},
    # Lifestyle / How-to
    {"url": "https://www.rappler.com/life-and-style/feed/",    "category": "LIFESTYLE"},
]

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY DESIGN TOKENS
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES = {
    "BALITA":     {"rgb": (239,  68,  68), "emoji": "🔴"},   # red
    "PULITIKA":   {"rgb": (139,  92, 246), "emoji": "🗳️"},   # purple
    "PERA":       {"rgb": ( 16, 185, 129), "emoji": "💸"},   # green
    "NEGOSYO":    {"rgb": (245, 158,  11), "emoji": "💼"},   # amber
    "CHISMIS":    {"rgb": (236,  72, 153), "emoji": "👀"},   # pink
    "LIFESTYLE":  {"rgb": ( 99, 102, 241), "emoji": "✨"},   # indigo
}

# Slide labels for each position
SLIDE_LABELS = [
    "",                  # 0 — hook (no label)
    "ANO NANGYARI?",     # 1
    "MGA DETALYE",       # 2
    "TANDAAN ITO",       # 3
    "BAKIT MAHALAGA?",   # 4
    "",                  # 5 — CTA (no label)
]

# Colors
BG_DARK  = (13,  17,  28)    # #0D111C
BG_CARD  = (22,  33,  56)    # slightly lighter panel
C_WHITE  = (255, 255, 255)
C_GRAY   = (148, 163, 184)
C_BLACK  = (  0,   0,   0)


# ─────────────────────────────────────────────────────────────────────────────
# FONTS
# ─────────────────────────────────────────────────────────────────────────────
def setup_fonts():
    for url, path in [(FONT_BOLD_URL, FONT_BOLD_PATH), (FONT_REG_URL, FONT_REG_PATH)]:
        if not os.path.exists(path):
            print(f"  Downloading font from {url} …")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            print(f"  Saved → {path}")


def get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────────────────────
# RSS FETCH
# ─────────────────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PHNewsBot/1.0)"}

def strip_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(raw)).strip()


def fetch_articles() -> list[dict]:
    articles = []
    for feed in FEEDS:
        try:
            r = requests.get(feed["url"], headers=HEADERS, timeout=12)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:8]:
                title = strip_html(item.findtext("title", ""))
                desc  = strip_html(item.findtext("description", ""))
                link  = (item.findtext("link") or "").strip()
                if title and link and len(title) > 10:
                    articles.append({
                        "title":    title,
                        "desc":     desc[:800],
                        "link":     link,
                        "category": feed["category"],
                    })
        except Exception as e:
            print(f"  ⚠️  Feed error [{feed['url']}]: {e}")
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# SLIDE CONTENT  — Groq (Llama 3) or plain extraction
# ─────────────────────────────────────────────────────────────────────────────
def generate_slides_groq(article: dict) -> list[str] | None:
    prompt = f"""Ikaw ay isang Filipino social media content writer na katulad ng Peso Weekly.
Gumawa ng nilalaman para sa 6-slide na Instagram carousel tungkol sa balitang ito:

PAMAGAT: {article['title']}
DETALYE: {article['desc']}
KATEGORYA: {article['category']}

PANUTO:
- Sumulat sa Filipino / Taglish — casual, relatable, madaling intindihin
- MAIKLI lang ang bawat slide (max 25 salita)
- Slide 1: Grabbing hook headline — dramatic, curiosity-inducing
- Slide 2: Simpleng paliwanag — ano nangyari?
- Slide 3: Mahalagang detalye o numero
- Slide 4: Isa pang key point o konteksto
- Slide 5: Bakit ito mahalaga sa ordinary na Pilipino?
- Slide 6: CTA — "I-follow kami para sa ganito pang balita!"

I-format ang sagot bilang JSON array lamang (walang ibang text):
[
  {{"slide": 1, "text": "..."}},
  {{"slide": 2, "text": "..."}},
  {{"slide": 3, "text": "..."}},
  {{"slide": 4, "text": "..."}},
  {{"slide": 5, "text": "..."}},
  {{"slide": 6, "text": "..."}}
]"""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":       "llama-3.3-70b-versatile",
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.75,
                "max_tokens":  600,
            },
            timeout=30,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        m   = re.search(r"\[.*?\]", raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return [s["text"].strip() for s in data if "text" in s]
    except Exception as e:
        print(f"  ⚠️  Groq error: {e}")
    return None


def generate_slides_fallback(article: dict) -> list[str]:
    """Simple extraction — no API key required."""
    title     = article["title"]
    desc      = article["desc"]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", desc) if len(s.strip()) > 20]

    def get_sent(i, default):
        return sentences[i] if i < len(sentences) else default

    return [
        title,
        get_sent(0, "Alamin ang buong kwento sa mga susunod na slide."),
        get_sent(1, "Isa ito sa mga pinakamahalagang balita ngayon."),
        get_sent(2, "Patuloy na sinusundan ng mga Pilipino ang isyung ito."),
        "Nakakaapekto ito sa ating pang-araw-araw na buhay bilang mga Pilipino.",
        f"I-follow ang @{PAGE_NAME} para sa pinaka-updated na balita araw-araw! 🔥",
    ]


def generate_slides(article: dict) -> list[str]:
    if GROQ_API_KEY:
        print("  🤖 Generating content with Groq (Llama 3)…")
        texts = generate_slides_groq(article)
        if texts:
            return texts
    print("  ✍️  Using text extraction fallback…")
    return generate_slides_fallback(article)


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE GENERATION  — Peso Weekly vibe
# ─────────────────────────────────────────────────────────────────────────────
def draw_rounded_rect(draw, x0, y0, x1, y1, r, fill):
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.ellipse([x0, y0, x0 + 2*r, y0 + 2*r], fill=fill)
    draw.ellipse([x1 - 2*r, y0, x1, y0 + 2*r], fill=fill)
    draw.ellipse([x0, y1 - 2*r, x0 + 2*r, y1], fill=fill)
    draw.ellipse([x1 - 2*r, y1 - 2*r, x1, y1], fill=fill)


def fit_text(draw, text: str, font_size: int, max_w: int, max_lines: int, bold=True):
    """Return (font, lines) fitting within max_w px and max_lines."""
    while font_size >= 28:
        font  = get_font(font_size, bold=bold)
        words = text.split()
        lines, cur = [], []
        for word in words:
            test = " ".join(cur + [word])
            if draw.textbbox((0, 0), test, font=font)[2] > max_w and cur:
                lines.append(" ".join(cur))
                cur = [word]
            else:
                cur.append(word)
        if cur:
            lines.append(" ".join(cur))
        if len(lines) <= max_lines:
            return font, lines
        font_size -= 4
    return get_font(28, bold=bold), lines


def create_slide(text: str, idx: int, total: int, category: str) -> Image.Image:
    cat     = CATEGORIES.get(category, CATEGORIES["BALITA"])
    accent  = cat["rgb"]
    emoji   = cat["emoji"]

    img  = Image.new("RGB", (IMG_W, IMG_H), BG_DARK)
    draw = ImageDraw.Draw(img)

    is_hook = idx == 0
    is_cta  = idx == total - 1

    # ── Top accent stripe
    draw.rectangle([(0, 0), (IMG_W, 10)], fill=accent)

    # ── Category pill  (top-left)
    pill_font = get_font(26)
    pill_text = f"{emoji}  {category}"
    pill_bbox = draw.textbbox((0, 0), pill_text, font=pill_font)
    pw        = pill_bbox[2] + 36
    ph        = 46
    px, py    = 48, 34
    draw_rounded_rect(draw, px, py, px + pw, py + ph, 10, accent)
    draw.text((px + 18, py + 10), pill_text, font=pill_font, fill=C_WHITE)

    # ── Slide counter (top-right)
    ctr_font = get_font(24, bold=False)
    draw.text((IMG_W - 56, 44), f"{idx+1}/{total}", font=ctr_font, anchor="rm", fill=C_GRAY)

    # ────────────────────────────── HOOK SLIDE ──────────────────────────────
    if is_hook:
        font, lines = fit_text(draw, text.upper(), 76, IMG_W - 96, 5)
        fs   = font.size
        lh   = fs + 14
        th   = len(lines) * lh
        y    = (IMG_H - th) // 2 - 20
        for line in lines:
            bx = draw.textbbox((0, 0), line, font=font)[2]
            x  = (IMG_W - bx) // 2
            # drop shadow
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
            draw.text((x,     y    ), line, font=font, fill=C_WHITE)
            y += lh
        # accent underline
        draw.rectangle([(IMG_W//2 - 80, y + 22), (IMG_W//2 + 80, y + 28)], fill=accent)

    # ────────────────────────────── CTA SLIDE ───────────────────────────────
    elif is_cta:
        # Big fire emoji
        e_font = get_font(130)
        draw.text((IMG_W // 2, 290), "🔥", font=e_font, anchor="mm")

        draw.text((IMG_W // 2, 510), "I-FOLLOW ANG",
                  font=get_font(36, bold=False), anchor="mm", fill=C_GRAY)
        draw.text((IMG_W // 2, 590), f"@{PAGE_NAME}",
                  font=get_font(62), anchor="mm", fill=C_WHITE)
        draw.text((IMG_W // 2, 680), "Para sa pinaka-updated na balita! 📲",
                  font=get_font(32, bold=False), anchor="mm", fill=C_GRAY)
        # divider
        draw.rectangle([(160, 748), (IMG_W - 160, 755)], fill=accent)
        draw.text((IMG_W // 2, 790), "Libre naman. I-follow na! 😄",
                  font=get_font(28, bold=False), anchor="mm", fill=C_GRAY)

    # ────────────────────────────── CONTENT SLIDES ──────────────────────────
    else:
        label = SLIDE_LABELS[idx] if idx < len(SLIDE_LABELS) else ""

        # Label
        if label:
            lbl_font = get_font(32)
            lbl_bbox = draw.textbbox((0, 0), label, font=lbl_font)
            lbl_w    = lbl_bbox[2]
            lbl_x    = (IMG_W - lbl_w) // 2
            lbl_y    = 130
            draw.text((lbl_x, lbl_y), label, font=lbl_font, fill=accent)
            # underline
            draw.rectangle([(lbl_x, lbl_y + lbl_bbox[3] + 6),
                             (lbl_x + lbl_w, lbl_y + lbl_bbox[3] + 10)], fill=accent)

        # Main body text — left-aligned with wide padding
        pad   = 70
        max_w = IMG_W - pad * 2
        font, lines = fit_text(draw, text, 60, max_w, 7)
        fs    = font.size
        lh    = fs + 20
        th    = len(lines) * lh
        y     = max(220, (IMG_H - th) // 2 + 10)
        for i, line in enumerate(lines):
            # First line gets accent colour (emphasis)
            colour = accent if i == 0 else C_WHITE
            draw.text((pad, y), line, font=font, fill=colour)
            y += lh

        # Accent left border
        bar_top    = max(220, (IMG_H - th) // 2 + 10) - 8
        bar_bottom = bar_top + th + 8
        draw.rectangle([(36, bar_top), (42, bar_bottom)], fill=accent)

    # ── Bottom branding bar
    draw.rectangle([(0, IMG_H - 72), (IMG_W, IMG_H)], fill=BG_CARD)
    draw.rectangle([(0, IMG_H - 72), (IMG_W, IMG_H - 70)], fill=accent)
    brand_font = get_font(28, bold=False)
    draw.text((IMG_W // 2, IMG_H - 36), f"@{PAGE_NAME}",
              font=brand_font, anchor="mm", fill=C_GRAY)

    return img


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE HOSTING  — imgbb (free)
# ─────────────────────────────────────────────────────────────────────────────
def upload_to_imgbb(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=93)
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    r = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMGBB_API_KEY, "image": img_b64, "expiration": 600},
        timeout=30,
    )
    r.raise_for_status()
    url = r.json()["data"]["url"]
    return url


# ─────────────────────────────────────────────────────────────────────────────
# INSTAGRAM GRAPH API
# ─────────────────────────────────────────────────────────────────────────────
def ig_post(path: str, **params) -> dict:
    r = requests.post(
        f"{IG_BASE}/{path}",
        params={"access_token": FB_ACCESS_TOKEN, **params},
        timeout=30,
    )
    if not r.ok:
        print(f"  IG API error: {r.status_code} — {r.text}")
    r.raise_for_status()
    return r.json()


def ig_get(path: str, **params) -> dict:
    r = requests.get(
        f"{IG_BASE}/{path}",
        params={"access_token": FB_ACCESS_TOKEN, **params},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def upload_carousel_item(image_url: str) -> str:
    data = ig_post(
        f"{IG_USER_ID}/media",
        image_url=image_url,
        is_carousel_item="true",
    )
    return data["id"]


def wait_for_container(cid: str, retries: int = 12, interval: int = 5):
    for attempt in range(retries):
        status = ig_get(cid, fields="status_code").get("status_code", "")
        print(f"    Container {cid}: {status}  (attempt {attempt+1}/{retries})")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {cid} errored during processing.")
        time.sleep(interval)
    raise TimeoutError(f"Container {cid} did not finish in time.")


def create_carousel(children: list[str], caption: str) -> str:
    data = ig_post(
        f"{IG_USER_ID}/media",
        media_type="CAROUSEL",
        children=",".join(children),
        caption=caption,
    )
    return data["id"]


def publish_media(creation_id: str) -> str:
    data = ig_post(f"{IG_USER_ID}/media_publish", creation_id=creation_id)
    return data["id"]


# ─────────────────────────────────────────────────────────────────────────────
# CAPTION
# ─────────────────────────────────────────────────────────────────────────────
HASHTAG_MAP = {
    "BALITA":     "#Balita #PhilippineNews #PilipinasNews #BreakingNewsPH",
    "PULITIKA":   "#Pulitika #PhilippinePolitics #BalitangPolitika #PilipinoNewsToday",
    "PERA":       "#Pera #PinoyMoney #PersonalFinancePH #PaanoKumita",
    "NEGOSYO":    "#Negosyo #PinoyEntrepreneur #StartupPH #BusinessMindset",
    "CHISMIS":    "#Chismis #PinoyEntertainment #Showbiz #LatestChismis",
    "LIFESTYLE":  "#LifestylePH #PinoyLiving #TipsAtTricks #PilipinoLifestyle",
}

def build_caption(article: dict) -> str:
    cat   = article["category"]
    emoji = CATEGORIES.get(cat, CATEGORIES["BALITA"])["emoji"]
    tags  = HASHTAG_MAP.get(cat, "#PilipinasNews")
    return (
        f"{emoji} {article['title']}\n\n"
        "👉 I-swipe para sa buong kwento!\n\n"
        f"{tags} #Philippines #Pilipinas #PinoyNews\n\n"
        f"📰 Source: {article['link']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  🔥 PH News Instagram Carousel Bot — Peso Weekly Style")
    print("=" * 60)

    # ── Fonts
    print("\n📦 Setting up fonts…")
    setup_fonts()

    # ── Fetch articles
    print("\n📰 Fetching articles from RSS feeds…")
    articles = fetch_articles()
    if not articles:
        print("❌ No articles fetched. Check feeds or network. Exiting.")
        sys.exit(1)
    print(f"   Found {len(articles)} articles across all feeds.")

    # ── Pick one at random
    article = random.choice(articles)
    print(f"\n🎯 Selected article:")
    print(f"   Category : {article['category']}")
    print(f"   Title    : {article['title'][:80]}")
    print(f"   Link     : {article['link']}")

    # ── Generate slide texts
    print("\n✍️  Generating slide content…")
    slide_texts = generate_slides(article)
    for i, t in enumerate(slide_texts):
        print(f"   Slide {i+1}: {t[:60]}…")

    # ── Create slide images
    print("\n🎨 Creating slide images…")
    images = []
    for i, text in enumerate(slide_texts):
        img = create_slide(text, i, len(slide_texts), article["category"])
        images.append(img)
        print(f"   Slide {i+1}/{len(slide_texts)} ✓")

    # ── Upload images to imgbb
    print("\n☁️  Uploading images to imgbb…")
    image_urls = []
    for i, img in enumerate(images):
        url = upload_to_imgbb(img)
        image_urls.append(url)
        print(f"   Slide {i+1} → {url}")
        time.sleep(1)

    # ── Upload each as carousel item to Instagram
    print("\n📱 Creating Instagram carousel items…")
    children = []
    for i, url in enumerate(image_urls):
        cid = upload_carousel_item(url)
        children.append(cid)
        print(f"   Item {i+1} container ID: {cid}")
        time.sleep(4)  # avoid rate limits

    # ── Wait for items to process
    print("\n⏳ Waiting for carousel items to process…")
    for cid in children:
        wait_for_container(cid)

    # ── Build caption
    caption = build_caption(article)

    # ── Create carousel container
    print("\n🎠 Creating carousel container…")
    carousel_id = create_carousel(children, caption)
    print(f"   Carousel ID: {carousel_id}")

    # ── Wait for carousel
    print("\n⏳ Waiting for carousel to process…")
    wait_for_container(carousel_id)

    # ── Publish!
    print("\n🚀 Publishing to Instagram…")
    post_id = publish_media(carousel_id)
    print(f"\n✅ SUCCESS! Post ID: {post_id}")
    print("🔥 Salamat! Mabuhay ang automation! 🇵🇭")
    print("=" * 60)


if __name__ == "__main__":
    main()
