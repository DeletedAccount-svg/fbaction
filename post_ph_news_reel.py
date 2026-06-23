"""
post_ph_news_reel.py
====================
Philippine News Instagram REEL Poster — Animated Video Edition
Same news pipeline as post_ph_news_ig.py BUT outputs a vertical
1080×1920 MP4 Reel with Ken Burns zoom/pan per slide + background music.

HOW IT DIFFERS FROM THE CAROUSEL VERSION:
  - Slides are 1080×1920 (vertical 9:16) instead of 1080×1080 (square)
  - Each slide is animated with a slow Ken Burns zoom/pan effect
  - All slides are stitched into one MP4 using moviepy
  - A royalty-free background music track is downloaded and mixed in
  - The final video is uploaded as an Instagram REEL (not carousel)

Required GitHub Secrets (same as carousel version):
  IG_USER_ID       — Instagram Business/Creator User ID (from Graph API)
  FB_ACCESS_TOKEN  — Facebook Page Access Token with instagram_content_publish
  IMGBB_API_KEY    — Free at imgbb.com (used to host the final MP4 publicly)
  PAGE_NAME        — Your Instagram handle WITHOUT the @, e.g. yourpage.ph

Optional:
  GROQ_API_KEY     — Free at console.groq.com — gives better Taglish slide content

GitHub Actions dependencies (add to your workflow pip install line):
  pip install requests Pillow moviepy numpy
"""

import os, sys, json, random, requests, re, time, base64, math, tempfile, wave, struct
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from io import BytesIO
from html import unescape

# moviepy — graceful import so we can show a clear error if missing
try:
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip,
        concatenate_videoclips, ColorClip
    )
    import moviepy.video.fx.all as vfx
    MOVIEPY_OK = True
except ImportError:
    MOVIEPY_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
IG_USER_ID      = os.environ["IG_USER_ID"]
FB_ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
IMGBB_API_KEY   = os.environ["IMGBB_API_KEY"]
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
PAGE_NAME       = os.environ.get("PAGE_NAME", "yourpage.ph")

# ── Canvas: vertical 9:16 for Reels
IMG_W, IMG_H    = 1080, 1920
IG_BASE         = "https://graph.facebook.com/v21.0"

FONT_BOLD_URL   = "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf"
FONT_REG_URL    = "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf"
FONT_BOLD_PATH  = "/tmp/Poppins-Bold.ttf"
FONT_REG_PATH   = "/tmp/Poppins-Regular.ttf"

# ── Video settings
SLIDE_DURATION  = 4.0      # seconds each slide is shown
FADE_DURATION   = 0.4      # crossfade between slides (seconds)
FPS             = 30
ZOOM_AMOUNT     = 0.08     # Ken Burns zoom: 8% scale increase over slide duration

# ── Background music — generated in pure Python, no download, no fail-state
MUSIC_PATH       = "/tmp/bg_music.wav"
MUSIC_VOLUME     = 0.18     # keep music subtle under the visuals
BEAT_SAMPLE_RATE = 44100
BEAT_BPM         = 72       # slow lofi tempo

# ─────────────────────────────────────────────────────────────────────────────
# RSS FEEDS  (same as carousel version — add/remove freely)
# ─────────────────────────────────────────────────────────────────────────────
FEEDS = [
    {"url": "https://www.rappler.com/feed/",                   "category": "BALITA"},
    {"url": "https://newsinfo.inquirer.net/feed",              "category": "BALITA"},
    {"url": "https://www.philstar.com/rss/headlines",          "category": "BALITA"},
    {"url": "https://www.gmanetwork.com/news/rss/latest.xml",  "category": "BALITA"},
    {"url": "https://www.rappler.com/nation/feed/",            "category": "PULITIKA"},
    {"url": "https://nation.inquirer.net/feed",                "category": "PULITIKA"},
    {"url": "https://business.inquirer.net/feed",              "category": "PERA"},
    {"url": "https://www.bworldonline.com/feed/",              "category": "NEGOSYO"},
    {"url": "https://businessmirror.com.ph/feed/",             "category": "NEGOSYO"},
    {"url": "https://www.pep.ph/rss",                         "category": "CHISMIS"},
    {"url": "https://entertainment.inquirer.net/feed",         "category": "CHISMIS"},
    {"url": "https://www.rappler.com/life-and-style/feed/",    "category": "LIFESTYLE"},
]

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY DESIGN TOKENS
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES = {
    "BALITA":     {"rgb": (239,  68,  68), "emoji": "🔴"},
    "PULITIKA":   {"rgb": (139,  92, 246), "emoji": "🗳️"},
    "PERA":       {"rgb": ( 16, 185, 129), "emoji": "💸"},
    "NEGOSYO":    {"rgb": (245, 158,  11), "emoji": "💼"},
    "CHISMIS":    {"rgb": (236,  72, 153), "emoji": "👀"},
    "LIFESTYLE":  {"rgb": ( 99, 102, 241), "emoji": "✨"},
}

SLIDE_LABELS = [
    "",                  # 0 — hook
    "ANO NANGYARI?",     # 1
    "MGA DETALYE",       # 2
    "TANDAAN ITO",       # 3
    "BAKIT MAHALAGA?",   # 4
    "PRO TIP",           # 5
    "SA MADALING SALITA",# 6
    "",                  # 7 — CTA
]

BG_DARK  = (13,  17,  28)
BG_CARD  = (22,  33,  56)
C_WHITE  = (255, 255, 255)
C_GRAY   = (148, 163, 184)

HASHTAG_MAP = {
    "BALITA":     "#Balita #PhilippineNews #PilipinasNews #BreakingNewsPH",
    "PULITIKA":   "#Pulitika #PhilippinePolitics #BalitangPolitika",
    "PERA":       "#Pera #PinoyMoney #PersonalFinancePH #PaanoKumita",
    "NEGOSYO":    "#Negosyo #PinoyEntrepreneur #StartupPH",
    "CHISMIS":    "#Chismis #PinoyEntertainment #Showbiz",
    "LIFESTYLE":  "#LifestylePH #PinoyLiving #TipsAtTricks",
}


# ─────────────────────────────────────────────────────────────────────────────
# FONTS
# ─────────────────────────────────────────────────────────────────────────────
def setup_fonts():
    for url, path in [(FONT_BOLD_URL, FONT_BOLD_PATH), (FONT_REG_URL, FONT_REG_PATH)]:
        if not os.path.exists(path):
            print(f"  Downloading font: {url} …")
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
# MUSIC — pure-Python lofi beat generator (no download, no external service)
# ─────────────────────────────────────────────────────────────────────────────
# UGH! BEAT MAKER WORK WITH ROCK AND STICK ONLY. NO NEED FETCH FIRE FROM
# OTHER TRIBE SERVER. ALWAYS WORK. ALWAYS THERE. GRUNT.

def _note_freq(semitones_from_a4: float) -> float:
    """Return frequency in Hz for a note N semitones away from A4 (440Hz)."""
    return 440.0 * (2.0 ** (semitones_from_a4 / 12.0))


def _sine(freq: float, dur: float, sr: int, amp: float = 1.0) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return amp * np.sin(2 * np.pi * freq * t)


def _envelope(n: int, attack: float = 0.02, release: float = 0.3) -> np.ndarray:
    """Simple attack/release envelope so notes don't click."""
    env = np.ones(n)
    a = max(1, int(n * attack))
    r = max(1, int(n * release))
    env[:a] = np.linspace(0, 1, a)
    env[-r:] = np.minimum(env[-r:], np.linspace(1, 0, r))
    return env


def _lowpass(signal: np.ndarray, strength: float = 0.85) -> np.ndarray:
    """Crude one-pole lowpass filter — gives that muffled lofi warmth."""
    out = np.zeros_like(signal)
    out[0] = signal[0]
    for i in range(1, len(signal)):
        out[i] = strength * out[i - 1] + (1 - strength) * signal[i]
    return out


def _kick(sr: int, dur: float = 0.25) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    freq = np.linspace(150, 45, n)              # pitch drop = thump
    wave_ = np.sin(2 * np.pi * np.cumsum(freq) / sr)
    return wave_ * np.exp(-t * 18) * 0.9


def _snare(sr: int, dur: float = 0.18) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    noise = np.random.uniform(-1, 1, n)
    body = np.sin(2 * np.pi * 180 * t) * 0.3
    return (noise * 0.7 + body) * np.exp(-t * 14) * 0.6


def _hat(sr: int, dur: float = 0.06) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    noise = np.random.uniform(-1, 1, n)
    return noise * np.exp(-t * 40) * 0.25


def _vinyl_crackle(n: int, amount: float = 0.02) -> np.ndarray:
    """Sparse little pops — the lofi 'dusty record' texture."""
    crackle = np.zeros(n)
    pops = np.random.choice(n, size=n // 800, replace=False)
    crackle[pops] = np.random.uniform(-1, 1, len(pops))
    return crackle * amount


def _mix(base: np.ndarray, addition: np.ndarray, at_sample: int) -> None:
    """Add a short sound into a longer buffer in place, clipping at edges."""
    end = min(at_sample + len(addition), len(base))
    seg = end - at_sample
    if seg > 0:
        base[at_sample:end] += addition[:seg]


def generate_lofi_beat(duration: float, path: str,
                        sr: int = BEAT_SAMPLE_RATE, bpm: int = BEAT_BPM) -> str:
    """
    Build a simple chill lofi loop entirely with numpy math — sine-wave
    chords + drum hits synthesized from scratch, then lowpass-filtered
    and vinyl-crackled for that dusty bedroom-producer vibe.
    No internet, no audio files, no API keys. Just rocks and sticks.
    """
    n_samples = int(sr * duration)
    mix = np.zeros(n_samples)

    beat_dur = 60.0 / bpm
    bar_dur  = beat_dur * 4

    # ii–V–I–vi style chill chord loop, in semitones from A4
    chord_progression = [
        [-9, -5, -2],   # Cmaj-ish
        [-14, -10, -7], # Gmaj-ish
        [-12, -8, -5],  # Amin-ish
        [-17, -13, -10],# Fmaj-ish
    ]

    n_bars = max(1, int(math.ceil(duration / bar_dur)))
    for bar in range(n_bars):
        chord = chord_progression[bar % len(chord_progression)]
        start_sample = int(bar * bar_dur * sr)

        # Warm sustained chord pad
        for semis in chord:
            freq = _note_freq(semis)
            tone = _sine(freq, bar_dur, sr, amp=0.10)
            tone *= _envelope(len(tone), attack=0.05, release=0.6)
            _mix(mix, tone, start_sample)

        # Drum pattern across the 4 beats of this bar (kick/snare/hats)
        for beat in range(4):
            beat_sample = start_sample + int(beat * beat_dur * sr)
            if beat in (0, 2):
                _mix(mix, _kick(sr), beat_sample)
            if beat in (1, 3):
                _mix(mix, _snare(sr), beat_sample)
            # lazy lofi hats on the off-beats
            _mix(mix, _hat(sr), beat_sample + int(beat_dur * sr * 0.5))

    # Trim/pad to exact duration
    if len(mix) < n_samples:
        mix = np.pad(mix, (0, n_samples - len(mix)))
    mix = mix[:n_samples]

    # Lofi warmth: lowpass filter + dusty vinyl crackle
    mix = _lowpass(mix, strength=0.6)
    mix += _vinyl_crackle(n_samples, amount=0.015)

    # Normalize and convert to 16-bit PCM
    peak = np.max(np.abs(mix)) or 1.0
    mix = (mix / peak) * 0.85
    pcm = (mix * 32767).astype(np.int16)

    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    return path


def setup_music(duration: float = 60.0) -> bool:
    """
    Generate the background beat in pure Python — guaranteed to work,
    no download, no flaky third-party server to beg for fire.
    """
    try:
        print("  🎵 UGH! Smashing rocks together to make beat… (pure Python, no download)")
        generate_lofi_beat(duration, MUSIC_PATH)
        print(f"  🎵 Beat made! Stored at fire-pit → {MUSIC_PATH}")
        return True
    except Exception as e:
        print(f"  ⚠️  Beat generator angry: {e} — video will be silent.")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RSS FETCH  (identical logic to carousel version)
# ─────────────────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PHNewsBot/1.0)"}

def strip_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(raw)).strip()


def _looks_like_image(url: str) -> bool:
    return bool(re.search(r'\.(jpg|jpeg|png|webp)(\?.*)?$', url, re.IGNORECASE))


def extract_image_from_item(item, raw_xml_text: str = "") -> str:
    for tag in [
        "{http://search.yahoo.com/mrss/}content",
        "{http://search.yahoo.com/mrss/}thumbnail",
        "media:content", "media:thumbnail",
    ]:
        el = item.find(tag)
        if el is not None:
            url = el.get("url", "")
            if url and _looks_like_image(url):
                return url
    enc = item.find("enclosure")
    if enc is not None:
        url = enc.get("url", "")
        t   = enc.get("type", "")
        if url and ("image" in t or _looks_like_image(url)):
            return url
    desc_raw = item.findtext("description", "") or ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw, re.IGNORECASE)
    if m:
        url = m.group(1)
        if _looks_like_image(url):
            return url
    if raw_xml_text:
        matches = re.findall(
            r'https?://[^\s\'"<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\'"<>]*)?',
            raw_xml_text, re.IGNORECASE
        )
        for url in matches:
            if "1x1" not in url and "pixel" not in url.lower():
                return url
    return ""


def fetch_articles() -> list[dict]:
    articles = []
    for feed in FEEDS:
        try:
            r = requests.get(feed["url"], headers=HEADERS, timeout=12)
            r.raise_for_status()
            raw_text = r.text
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:8]:
                title     = strip_html(item.findtext("title", ""))
                desc      = strip_html(item.findtext("description", ""))
                link      = (item.findtext("link") or "").strip()
                image_url = extract_image_from_item(item, raw_text)
                if title and link and len(title) > 10:
                    articles.append({
                        "title":    title,
                        "desc":     desc[:800],
                        "link":     link,
                        "category": feed["category"],
                        "image_url": image_url,
                    })
        except Exception as e:
            print(f"  ⚠️  Feed error [{feed['url']}]: {e}")
    return articles


def fetch_article_image(image_url: str):
    """Download article photo as 1080×1920 PIL image (vertical crop)."""
    if not image_url:
        return None
    try:
        print(f"  📷 Fetching article image: {image_url[:80]}…")
        r = requests.get(image_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        # Crop to 9:16 aspect ratio
        w, h    = img.size
        target_ratio = IMG_W / IMG_H  # 9/16 = 0.5625
        current_ratio = w / h
        if current_ratio > target_ratio:
            # image is wider than 9:16 — crop sides
            new_w = int(h * target_ratio)
            left  = (w - new_w) // 2
            img   = img.crop((left, 0, left + new_w, h))
        else:
            # image is taller — crop top/bottom
            new_h = int(w / target_ratio)
            top   = (h - new_h) // 2
            img   = img.crop((0, top, w, top + new_h))
        img = img.resize((IMG_W, IMG_H), Image.LANCZOS)
        print("  ✅ Article image loaded (1080×1920)!")
        return img
    except Exception as e:
        print(f"  ⚠️  Could not fetch article image: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SLIDE CONTENT — Groq or fallback (8 slides for Reel)
# ─────────────────────────────────────────────────────────────────────────────
def generate_slides_groq(article: dict) -> list[str] | None:
    prompt = f"""Ikaw ay isang Filipino social media content writer na katulad ng Peso Weekly.
Gumawa ng nilalaman para sa 8-slide na Instagram REEL tungkol sa balitang ito:

PAMAGAT: {article['title']}
DETALYE: {article['desc']}
KATEGORYA: {article['category']}

PANUTO:
- Sumulat sa Filipino / Taglish — casual, relatable, madaling intindihin
- MAIKLI lang ang bawat slide (max 20 salita) — kailangan mabasa agad sa video
- Slide 1: Grabbing hook headline — dramatic, curiosity-inducing, ALL CAPS feel
- Slide 2: Simpleng paliwanag — ano nangyari?
- Slide 3: Mahalagang detalye o numero
- Slide 4: Isa pang key point o konteksto
- Slide 5: Bakit ito mahalaga sa ordinary na Pilipino?
- Slide 6: Pro tip o advice para sa mambabasa
- Slide 7: Sa madaling salita — isang pangungusap na buod
- Slide 8: CTA — "I-follow kami para sa ganito pang balita araw-araw!"

I-format ang sagot bilang JSON array lamang (walang ibang text):
[
  {{"slide": 1, "text": "..."}},
  {{"slide": 2, "text": "..."}},
  {{"slide": 3, "text": "..."}},
  {{"slide": 4, "text": "..."}},
  {{"slide": 5, "text": "..."}},
  {{"slide": 6, "text": "..."}},
  {{"slide": 7, "text": "..."}},
  {{"slide": 8, "text": "..."}}
]"""
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":       "llama-3.3-70b-versatile",
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.75,
                "max_tokens":  800,
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
    title     = article["title"]
    desc      = article["desc"]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", desc) if len(s.strip()) > 20]
    def gs(i, default): return sentences[i] if i < len(sentences) else default
    return [
        title,
        gs(0, "Alamin ang buong kwento sa mga susunod na slide."),
        gs(1, "Isa ito sa mga pinakamahalagang balita ngayon."),
        gs(2, "Patuloy na sinusundan ng mga Pilipino ang isyung ito."),
        "Nakakaapekto ito sa ating pang-araw-araw na buhay bilang mga Pilipino.",
        "Manatiling updated — sundan ang mga opisyal na pahayag.",
        f"Isa sa mga pangunahing balita ngayon sa {article['category']}.",
        f"I-follow ang @{PAGE_NAME} para sa pinaka-updated na balita araw-araw! 🔥",
    ]


def generate_slides(article: dict) -> list[str]:
    if GROQ_API_KEY:
        print("  🤖 Generating content with Groq (Llama 3)…")
        texts = generate_slides_groq(article)
        if texts and len(texts) >= 6:
            # pad to 8 if Groq returned fewer
            while len(texts) < 8:
                texts.append(f"I-follow ang @{PAGE_NAME} para sa pinaka-updated na balita! 🔥")
            return texts[:8]
    print("  ✍️  Using text extraction fallback…")
    return generate_slides_fallback(article)


# ─────────────────────────────────────────────────────────────────────────────
# SLIDE IMAGE GENERATION — 1080×1920 vertical
# ─────────────────────────────────────────────────────────────────────────────
def draw_rounded_rect(draw, x0, y0, x1, y1, r, fill):
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.ellipse([x0, y0, x0 + 2*r, y0 + 2*r], fill=fill)
    draw.ellipse([x1 - 2*r, y0, x1, y0 + 2*r], fill=fill)
    draw.ellipse([x0, y1 - 2*r, x0 + 2*r, y1], fill=fill)
    draw.ellipse([x1 - 2*r, y1 - 2*r, x1, y1], fill=fill)


def draw_text_shadow(draw, xy, text, font, fill, shadow_offset=3, shadow_color=(0, 0, 0, 180)):
    sx, sy = xy[0] + shadow_offset, xy[1] + shadow_offset
    draw.text((sx, sy), text, font=font, fill=shadow_color)
    draw.text(xy, text, font=font, fill=fill)


def fit_text(draw, text: str, font_size: int, max_w: int, max_lines: int, bold=True):
    """Return (font, lines) fitting within max_w and max_lines."""
    while font_size >= 32:
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
    return get_font(32, bold=bold), lines


def make_bg(photo, accent: tuple, blur: int = 10, darkness: float = 0.55):
    """Return 1080×1920 background."""
    if photo:
        bg = photo.copy()
        bg = bg.filter(ImageFilter.GaussianBlur(radius=blur))
        enhancer = ImageEnhance.Brightness(bg)
        bg = enhancer.enhance(1 - darkness)
        tint = Image.new("RGB", (IMG_W, IMG_H), accent)
        bg   = Image.blend(bg, tint, alpha=0.18)
        return bg
    else:
        bg   = Image.new("RGB", (IMG_W, IMG_H), BG_DARK)
        draw = ImageDraw.Draw(bg)
        for y in range(IMG_H):
            alpha = int(30 * (1 - y / IMG_H))
            r_c   = min(255, BG_DARK[0] + accent[0] * alpha // 255)
            g_c   = min(255, BG_DARK[1] + accent[1] * alpha // 255)
            b_c   = min(255, BG_DARK[2] + accent[2] * alpha // 255)
            draw.line([(0, y), (IMG_W, y)], fill=(r_c, g_c, b_c))
        return bg


def create_slide(text: str, idx: int, total: int, category: str,
                 article_photo=None) -> Image.Image:
    """Draw a single 1080×1920 slide image."""
    cat    = CATEGORIES.get(category, CATEGORIES["BALITA"])
    accent = cat["rgb"]
    emoji  = cat["emoji"]

    is_hook = idx == 0
    is_cta  = idx == total - 1
    use_photo = article_photo and not is_cta

    bg   = make_bg(article_photo if use_photo else None, accent,
                   blur=10 if is_hook else 16,
                   darkness=0.45 if is_hook else 0.65)
    img  = bg.copy()
    draw = ImageDraw.Draw(img)

    # ── Top accent stripe
    draw.rectangle([(0, 0), (IMG_W, 14)], fill=accent)

    # ── Category pill (top-left)
    pill_font = get_font(34)
    pill_text = f"{emoji}  {category}"
    pill_bbox = draw.textbbox((0, 0), pill_text, font=pill_font)
    pw = pill_bbox[2] + 44
    ph = 58
    px, py = 56, 46
    draw_rounded_rect(draw, px, py, px + pw, py + ph, 12, accent)
    draw.text((px + 22, py + 12), pill_text, font=pill_font, fill=C_WHITE)

    # ── Slide counter (top-right)
    ctr_font = get_font(30, bold=False)
    draw.text((IMG_W - 64, 58), f"{idx+1}/{total}",
              font=ctr_font, anchor="rm", fill=C_GRAY)

    # ── HOOK SLIDE
    if is_hook:
        overlay  = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 170))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img  = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Reapply pill on top of overlay
        draw_rounded_rect(draw, px, py, px + pw, py + ph, 12, accent)
        draw.text((px + 22, py + 12), pill_text, font=pill_font, fill=C_WHITE)
        draw.text((IMG_W - 64, 58), f"{idx+1}/{total}",
                  font=ctr_font, anchor="rm", fill=C_GRAY)

        # Big centred headline — vertically centred in the tall canvas
        font, lines = fit_text(draw, text.upper(), 88, IMG_W - 112, 6)
        fs   = font.size
        lh   = fs + 18
        th   = len(lines) * lh
        y    = (IMG_H - th) // 2
        for line in lines:
            bx = draw.textbbox((0, 0), line, font=font)[2]
            x  = (IMG_W - bx) // 2
            draw_text_shadow(draw, (x, y), line, font, C_WHITE, shadow_offset=5)
            y += lh
        draw.rectangle([(IMG_W//2 - 90, y + 28), (IMG_W//2 + 90, y + 36)], fill=accent)

        # "SWIPE UP" nudge at bottom
        nudge_font = get_font(32, bold=False)
        draw.text((IMG_W // 2, IMG_H - 130), "👆 I-swipe para sa buong kwento",
                  font=nudge_font, anchor="mm", fill=C_GRAY)

    # ── CTA SLIDE
    elif is_cta:
        # Dark overlay for CTA
        overlay  = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 120))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img  = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Centred CTA block — pushed slightly above centre on tall canvas
        centre_y = IMG_H // 2 - 60

        e_font = get_font(160)
        draw.text((IMG_W // 2, centre_y - 160), "🔥", font=e_font, anchor="mm")

        draw.text((IMG_W // 2, centre_y + 60), "I-FOLLOW ANG",
                  font=get_font(44, bold=False), anchor="mm", fill=C_GRAY)
        draw.text((IMG_W // 2, centre_y + 150), f"@{PAGE_NAME}",
                  font=get_font(76), anchor="mm", fill=C_WHITE)
        draw.text((IMG_W // 2, centre_y + 260),
                  "Para sa pinaka-updated na balita! 📲",
                  font=get_font(38, bold=False), anchor="mm", fill=C_GRAY)
        draw.rectangle([(200, centre_y + 330), (IMG_W - 200, centre_y + 338)], fill=accent)
        draw.text((IMG_W // 2, centre_y + 390), "Libre naman. I-follow na! 😄",
                  font=get_font(34, bold=False), anchor="mm", fill=C_GRAY)

        # DM-share nudge
        draw.text((IMG_W // 2, IMG_H - 130),
                  "📤 I-share sa iyong mga kaibigan!",
                  font=get_font(32, bold=False), anchor="mm", fill=C_GRAY)

    # ── CONTENT SLIDES
    else:
        label = SLIDE_LABELS[idx] if idx < len(SLIDE_LABELS) else ""

        if use_photo:
            card_top = 150
            card_bot = IMG_H - 120
            card_img = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
            card_d   = ImageDraw.Draw(card_img)
            card_d.rectangle([(48, card_top), (IMG_W - 48, card_bot)],
                              fill=(13, 17, 28, 185))
            img_rgba = img.convert("RGBA")
            img_rgba.alpha_composite(card_img)
            img  = img_rgba.convert("RGB")
            draw = ImageDraw.Draw(img)

        # Reapply pill + counter (may have been overwritten by card)
        draw_rounded_rect(draw, px, py, px + pw, py + ph, 12, accent)
        draw.text((px + 22, py + 12), pill_text, font=pill_font, fill=C_WHITE)
        draw.text((IMG_W - 64, 58), f"{idx+1}/{total}",
                  font=ctr_font, anchor="rm", fill=C_GRAY)

        # Label
        if label:
            lbl_font = get_font(40)
            lbl_bbox = draw.textbbox((0, 0), label, font=lbl_font)
            lbl_w    = lbl_bbox[2]
            lbl_x    = (IMG_W - lbl_w) // 2
            lbl_y    = 170
            draw.text((lbl_x, lbl_y), label, font=lbl_font, fill=accent)
            draw.rectangle([(lbl_x, lbl_y + lbl_bbox[3] + 8),
                             (lbl_x + lbl_w, lbl_y + lbl_bbox[3] + 14)], fill=accent)

        # Body text — centred vertically in the taller canvas
        pad   = 80
        max_w = IMG_W - pad * 2
        font, lines = fit_text(draw, text, 72, max_w, 8)
        fs    = font.size
        lh    = fs + 24
        th    = len(lines) * lh
        # Push text to centre of remaining space below label
        content_top = 280 if label else 200
        content_bot = IMG_H - 180
        y = content_top + max(0, (content_bot - content_top - th) // 2)

        for i, line in enumerate(lines):
            colour = accent if i == 0 else C_WHITE
            draw_text_shadow(draw, (pad, y), line, font, colour, shadow_offset=3)
            y += lh

        # Accent left border
        bar_top    = content_top + max(0, (content_bot - content_top - th) // 2) - 8
        bar_bottom = bar_top + th + 8
        draw.rectangle([(40, bar_top), (48, bar_bottom)], fill=accent)

    # ── Bottom branding bar
    draw.rectangle([(0, IMG_H - 90), (IMG_W, IMG_H)], fill=BG_CARD)
    draw.rectangle([(0, IMG_H - 90), (IMG_W, IMG_H - 88)], fill=accent)
    brand_font = get_font(34, bold=False)
    draw.text((IMG_W // 2, IMG_H - 44), f"@{PAGE_NAME}",
              font=brand_font, anchor="mm", fill=C_GRAY)

    return img


# ─────────────────────────────────────────────────────────────────────────────
# KEN BURNS ANIMATION
# ─────────────────────────────────────────────────────────────────────────────
def make_ken_burns_clip(pil_img: Image.Image, duration: float,
                        zoom_in: bool = True, fps: int = FPS):
    """
    Convert a PIL image into a moviepy clip with a slow Ken Burns zoom.
    Alternates between zoom-in and zoom-out for visual variety.
    """
    img_array = np.array(pil_img)
    h, w      = img_array.shape[:2]
    zoom_start = 1.0
    zoom_end   = 1.0 + ZOOM_AMOUNT

    if not zoom_in:
        zoom_start, zoom_end = zoom_end, zoom_start

    def make_frame(t):
        progress = t / duration
        scale    = zoom_start + (zoom_end - zoom_start) * progress

        # Compute cropped region size
        crop_w = int(w / scale)
        crop_h = int(h / scale)

        # Pan: drift slightly from centre for parallax feel
        offset_x = int((w - crop_w) * 0.5)
        offset_y = int((h - crop_h) * 0.5)

        # Slightly shift the anchor based on zoom direction
        if zoom_in:
            offset_x += int((w - crop_w) * 0.1 * progress)
        else:
            offset_x += int((w - crop_w) * 0.1 * (1 - progress))

        offset_x = max(0, min(offset_x, w - crop_w))
        offset_y = max(0, min(offset_y, h - crop_h))

        cropped = img_array[offset_y:offset_y + crop_h, offset_x:offset_x + crop_w]
        # Resize back to original dimensions using PIL for quality
        cropped_pil = Image.fromarray(cropped).resize((w, h), Image.LANCZOS)
        return np.array(cropped_pil)

    return ImageClip(make_frame, duration=duration, ismask=False)


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────
def build_reel(images: list, output_path: str, has_music: bool) -> str:
    """
    Stitch PIL images into a vertical MP4 Reel with:
    - Ken Burns zoom per slide
    - Crossfade transitions
    - Optional background music
    Returns path to the output MP4.
    """
    print(f"\n🎬 Assembling {len(images)} slides into video…")

    clips = []
    for i, pil_img in enumerate(images):
        zoom_in = (i % 2 == 0)   # alternate zoom direction each slide
        clip    = make_ken_burns_clip(pil_img, SLIDE_DURATION, zoom_in=zoom_in)
        clip    = clip.set_fps(FPS)

        # Crossfade: fade out at end of each clip
        if i > 0:
            clip = clip.crossfadein(FADE_DURATION)

        clips.append(clip)
        print(f"   Slide {i+1}/{len(images)} animated ✓")

    # Concatenate with crossfade padding
    video = concatenate_videoclips(clips, method="compose",
                                   padding=-FADE_DURATION)

    # ── Background music
    if has_music and os.path.exists(MUSIC_PATH):
        try:
            print("  🎵 Mixing background music…")
            audio       = AudioFileClip(MUSIC_PATH)
            total_dur   = video.duration

            # Loop or trim music to match video length
            if audio.duration < total_dur:
                loops_needed = math.ceil(total_dur / audio.duration)
                from moviepy.editor import concatenate_audioclips
                audio = concatenate_audioclips([audio] * loops_needed)

            audio = audio.subclip(0, total_dur)
            audio = audio.volumex(MUSIC_VOLUME)
            video = video.set_audio(audio)
            print("  ✅ Music mixed in!")
        except Exception as e:
            print(f"  ⚠️  Music mix failed: {e} — continuing without audio.")

    # ── Render
    print(f"\n🎞️  Rendering MP4 → {output_path}  (this takes ~30-60 seconds)…")
    video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        ffmpeg_params=["-crf", "23", "-pix_fmt", "yuv420p"],
        logger=None,   # suppress verbose moviepy output
    )
    print(f"  ✅ Video rendered! Size: {os.path.getsize(output_path) / 1024 / 1024:.1f} MB")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO UPLOAD (imgbb supports MP4 via direct URL method)
# We upload to a temporary file host that returns a public URL for the IG API.
# Strategy: base64 encode to imgbb (works for video under ~32MB)
# ─────────────────────────────────────────────────────────────────────────────
def upload_video_to_imgbb(video_path: str) -> str:
    """Upload MP4 to imgbb and return the public URL."""
    size_mb = os.path.getsize(video_path) / 1024 / 1024
    print(f"  ☁️  Uploading video ({size_mb:.1f} MB) to imgbb…")

    with open(video_path, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode()

    r = requests.post(
        "https://api.imgbb.com/1/upload",
        data={
            "key":        IMGBB_API_KEY,
            "image":      video_b64,
            "expiration": 3600,   # 1 hour — enough for IG to pull it
        },
        timeout=120,
    )
    r.raise_for_status()
    url = r.json()["data"]["url"]
    print(f"  ✅ Video hosted at: {url}")
    return url


# ─────────────────────────────────────────────────────────────────────────────
# INSTAGRAM GRAPH API — REEL POSTING
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


def wait_for_container(cid: str, retries: int = 24, interval: int = 10):
    """Poll until the media container is FINISHED (video takes longer than images)."""
    for attempt in range(retries):
        status = ig_get(cid, fields="status_code").get("status_code", "")
        print(f"    Container {cid}: {status}  (attempt {attempt+1}/{retries})")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {cid} errored during processing.")
        time.sleep(interval)
    raise TimeoutError(f"Container {cid} did not finish in time.")


def upload_reel_container(video_url: str, caption: str) -> str:
    """
    Create a Reel media container via the Instagram Graph API.
    media_type=REELS tells Instagram this is a Reel, not a feed video.
    """
    data = ig_post(
        f"{IG_USER_ID}/media",
        media_type="REELS",
        video_url=video_url,
        caption=caption,
        share_to_feed="true",      # also show in grid, not just Reels tab
    )
    return data["id"]


def publish_media(creation_id: str) -> str:
    data = ig_post(f"{IG_USER_ID}/media_publish", creation_id=creation_id)
    return data["id"]


def post_comment(media_id: str, message: str) -> str:
    r = requests.post(
        f"{IG_BASE}/{media_id}/comments",
        params={"access_token": FB_ACCESS_TOKEN, "message": message},
        timeout=30,
    )
    if not r.ok:
        print(f"  ⚠️  Comment API error: {r.status_code} — {r.text}")
    r.raise_for_status()
    return r.json().get("id", "")


# ─────────────────────────────────────────────────────────────────────────────
# CAPTION
# ─────────────────────────────────────────────────────────────────────────────
def build_caption(article: dict) -> str:
    cat   = article["category"]
    emoji = CATEGORIES.get(cat, CATEGORIES["BALITA"])["emoji"]
    tags  = HASHTAG_MAP.get(cat, "#PilipinasNews")
    return (
        f"{emoji} {article['title']}\n\n"
        "👆 Panoorin para sa buong kwento!\n"
        "📤 I-share sa iyong mga kaibigan!\n\n"
        f"{tags} #Philippines #Pilipinas #PinoyNews"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  🎬 PH News Instagram REEL Bot — Animated Video Edition")
    print("=" * 60)

    # ── Check moviepy
    if not MOVIEPY_OK:
        print("❌ moviepy is not installed!")
        print("   Run: pip install moviepy numpy")
        sys.exit(1)

    # ── Fonts
    print("\n📦 Setting up fonts…")
    setup_fonts()

    # ── Music (generated to roughly match the video length)
    print("\n🎵 Setting up background beat…")
    est_duration = len(SLIDE_LABELS) * SLIDE_DURATION + 2.0
    has_music = setup_music(duration=est_duration)

    # ── Fetch articles
    print("\n📰 Fetching articles from RSS feeds…")
    articles = fetch_articles()
    if not articles:
        print("❌ No articles fetched. Check feeds or network. Exiting.")
        sys.exit(1)
    print(f"   Found {len(articles)} articles across all feeds.")

    # ── Pick one (prefer articles with images)
    articles_with_img    = [a for a in articles if a.get("image_url")]
    articles_without_img = [a for a in articles if not a.get("image_url")]
    if articles_with_img:
        article = random.choice(articles_with_img)
        print(f"   ✅ {len(articles_with_img)} articles had images — picking one with photo.")
    else:
        article = random.choice(articles_without_img)
        print("   ⚠️  No articles had images — using dark branded background.")

    print(f"\n🎯 Selected article:")
    print(f"   Category  : {article['category']}")
    print(f"   Title     : {article['title'][:80]}")
    print(f"   Link      : {article['link']}")
    print(f"   Image URL : {article.get('image_url', 'none')[:80] or 'none'}")

    # ── Download article image (vertical crop)
    print("\n📷 Fetching article photo…")
    article_photo = fetch_article_image(article.get("image_url", ""))
    if not article_photo:
        print("   ℹ️  No article photo — slides use the dark branded background.")

    # ── Generate slide texts (8 slides)
    print("\n✍️  Generating slide content…")
    slide_texts = generate_slides(article)
    for i, t in enumerate(slide_texts):
        print(f"   Slide {i+1}: {t[:60]}…")

    # ── Create slide images (1080×1920)
    print("\n🎨 Creating slide images (1080×1920)…")
    images = []
    for i, text in enumerate(slide_texts):
        img = create_slide(text, i, len(slide_texts), article["category"],
                           article_photo=article_photo)
        images.append(img)
        print(f"   Slide {i+1}/{len(slide_texts)} ✓")

    # ── Build animated video
    output_path = "/tmp/ph_news_reel.mp4"
    build_reel(images, output_path, has_music)

    # ── Upload video to imgbb
    print("\n☁️  Uploading video…")
    video_url = upload_video_to_imgbb(output_path)

    # ── Build caption
    caption = build_caption(article)

    # ── Create Reel container on Instagram
    print("\n📱 Creating Instagram Reel container…")
    reel_id = upload_reel_container(video_url, caption)
    print(f"   Reel container ID: {reel_id}")

    # ── Wait for Instagram to process the video (takes longer than images)
    print("\n⏳ Waiting for Reel to process (video takes ~1-3 min)…")
    wait_for_container(reel_id, retries=24, interval=10)

    # ── Publish!
    print("\n🚀 Publishing Reel to Instagram…")
    post_id = publish_media(reel_id)
    print(f"\n✅ SUCCESS! Reel ID: {post_id}")

    # ── Post comments
    time.sleep(5)
    print("\n💬 Posting comments…")
    try:
        c1 = post_comment(post_id, f"📰 Source: {article['link']}")
        print(f"   ✅ Comment 1 posted (source): {c1}")
    except Exception as e:
        print(f"   ⚠️  Could not post source comment: {e}")

    time.sleep(3)

    try:
        c2 = post_comment(post_id, "🌐 Visit us at https://ranksorcery.com/ for more! 🔥")
        print(f"   ✅ Comment 2 posted (site): {c2}")
    except Exception as e:
        print(f"   ⚠️  Could not post site comment: {e}")

    print("\n🔥 Salamat! Mabuhay ang automation! 🇵🇭")
    print("=" * 60)


if __name__ == "__main__":
    main()
