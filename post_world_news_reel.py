"""
post_world_news_reel.py
========================
World News Facebook REEL Poster — Animated Video Edition (English)
Same pipeline as the PH/Instagram version BUT:
  - Pulls world news (BBC, Al Jazeera, NPR, etc.) in English
  - Posts straight to a Facebook Page as a video (not Instagram)
  - One-step upload (Facebook Page Video API doesn't need a separate
    container/publish step like Instagram does)

Required GitHub Secrets:
  FB_ACCESS_TOKEN  — Facebook Page Access Token with pages_manage_posts + publish_video
  FB_PAGE_ID        — Your Facebook Page ID (numeric)
  (No IMGBB_API_KEY needed — video is hosted via a throwaway
   GitHub Release in this repo, using the built-in GITHUB_TOKEN.)
  PAGE_NAME        — Optional, your Page name for captions

Optional:
  GROQ_API_KEY     — Free at console.groq.com — gives better English slide content

GitHub Actions dependencies (add to your workflow pip install line):
  pip install requests Pillow "moviepy<2" numpy
"""

import os, sys, json, random, requests, re, time, base64, math, tempfile, wave, struct
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageOps
from io import BytesIO
from html import unescape

# moviepy — graceful import so we can show a clear error if missing
try:
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip,
        concatenate_videoclips, ColorClip, VideoClip
    )
    import moviepy.video.fx.all as vfx
    MOVIEPY_OK = True
except ImportError:
    MOVIEPY_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
FB_PAGE_ID      = os.environ["FB_PAGE_ID"]
FB_ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
GH_RELEASE_TOKEN = os.environ.get("GH_RELEASE_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
PAGE_NAME       = os.environ.get("PAGE_NAME", "aiacademylearning")

# ── Canvas: vertical 9:16 for Reels
IMG_W, IMG_H    = 1080, 1920
FB_BASE         = "https://graph.facebook.com/v21.0"

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
BEAT_BPM         = 72       # slow lofi tempo — default/fallback

# ── Mood presets: each mood picks a tempo + chord progression (semitones
# from A4) + drum intensity, so the beat matches the article's category.
MOOD_PRESETS = {
    "chill": {
        "bpm": 72,
        "minor": False,
        "kick_amp": 0.9, "snare_amp": 0.6,
        "chords": [
            [-9, -5, -2],     # Cmaj-ish
            [-14, -10, -7],   # Gmaj-ish
            [-12, -8, -5],    # Amin-ish
            [-17, -13, -10],  # Fmaj-ish
        ],
    },
    "dramatic": {
        "bpm": 60,
        "minor": True,
        "kick_amp": 1.15, "snare_amp": 0.75,
        "chords": [
            [-12, -8, -5],    # Amin
            [-17, -13, -10],  # Fmaj (relative major lift)
            [-19, -15, -12],  # Dmin
            [-14, -11, -7],   # Gmaj-ish (tension)
        ],
    },
    "upbeat": {
        "bpm": 100,
        "minor": False,
        "kick_amp": 1.0, "snare_amp": 0.7,
        "chords": [
            [-9, -5, -2],     # Cmaj
            [-2, 2, 5],       # Gmaj
            [0, 4, 7],        # Amaj-ish (bright lift)
            [-5, -1, 2],      # Fmaj
        ],
    },
}

# Which article category leans toward which mood
CATEGORY_MOOD = {
    "WORLD":         "dramatic",
    "POLITICS":      "dramatic",
    "BUSINESS":      "chill",
    "TECHNOLOGY":    "chill",
    "SCIENCE":       "upbeat",
    "ENTERTAINMENT": "upbeat",
}

# ─────────────────────────────────────────────────────────────────────────────
# RSS FEEDS  (same as carousel version — add/remove freely)
# ─────────────────────────────────────────────────────────────────────────────
FEEDS = [
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",              "category": "WORLD"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",                "category": "WORLD"},
    {"url": "https://feeds.npr.org/1004/rss.xml",                       "category": "WORLD"},
    {"url": "https://feeds.bbci.co.uk/news/politics/rss.xml",           "category": "POLITICS"},
    {"url": "https://feeds.bbci.co.uk/news/business/rss.xml",           "category": "BUSINESS"},
    {"url": "https://feeds.bbci.co.uk/news/technology/rss.xml",         "category": "TECHNOLOGY"},
    {"url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "category": "SCIENCE"},
    {"url": "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",  "category": "ENTERTAINMENT"},
]

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY DESIGN TOKENS
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES = {
    "WORLD":         {"rgb": (239,  68,  68), "emoji": "🌍"},
    "POLITICS":      {"rgb": (139,  92, 246), "emoji": "🗳️"},
    "BUSINESS":      {"rgb": ( 16, 185, 129), "emoji": "💼"},
    "TECHNOLOGY":    {"rgb": ( 59, 130, 246), "emoji": "💻"},
    "SCIENCE":       {"rgb": ( 14, 165, 233), "emoji": "🔬"},
    "ENTERTAINMENT": {"rgb": (236,  72, 153), "emoji": "🎬"},
}

SLIDE_LABELS = [
    "",                  # 0 — hook
    "WHAT HAPPENED?",    # 1
    "KEY DETAILS",       # 2
    "REMEMBER THIS",     # 3
    "WHY IT MATTERS",    # 4
    "QUICK TAKE",        # 5
    "IN SHORT",          # 6
    "",                  # 7 — CTA
]

BG_DARK  = (13,  17,  28)
BG_CARD  = (22,  33,  56)
C_WHITE  = (255, 255, 255)
C_GRAY   = (148, 163, 184)

HASHTAG_MAP = {
    "WORLD":         "#WorldNews #BreakingNews #GlobalNews",
    "POLITICS":      "#Politics #WorldPolitics #GlobalAffairs",
    "BUSINESS":      "#Business #Markets #Economy",
    "TECHNOLOGY":    "#Tech #Technology #Innovation",
    "SCIENCE":       "#Science #Research #Discovery",
    "ENTERTAINMENT": "#Entertainment #PopCulture #Showbiz",
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

def _note_freq(semitones_from_a4: float) -> float:
    return 440.0 * (2.0 ** (semitones_from_a4 / 12.0))


def _sine(freq: float, dur: float, sr: int, amp: float = 1.0) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return amp * np.sin(2 * np.pi * freq * t)


def _envelope(n: int, attack: float = 0.02, release: float = 0.3) -> np.ndarray:
    env = np.ones(n)
    a = max(1, int(n * attack))
    r = max(1, int(n * release))
    env[:a] = np.linspace(0, 1, a)
    env[-r:] = np.minimum(env[-r:], np.linspace(1, 0, r))
    return env


def _lowpass(signal: np.ndarray, strength: float = 0.85) -> np.ndarray:
    out = np.zeros_like(signal)
    out[0] = signal[0]
    for i in range(1, len(signal)):
        out[i] = strength * out[i - 1] + (1 - strength) * signal[i]
    return out


def _kick(sr: int, dur: float = 0.25) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    freq = np.linspace(150, 45, n)
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
    crackle = np.zeros(n)
    pops = np.random.choice(n, size=n // 800, replace=False)
    crackle[pops] = np.random.uniform(-1, 1, len(pops))
    return crackle * amount


def _mix(base: np.ndarray, addition: np.ndarray, at_sample: int) -> None:
    end = min(at_sample + len(addition), len(base))
    seg = end - at_sample
    if seg > 0:
        base[at_sample:end] += addition[:seg]


def generate_lofi_beat(duration: float, path: str, mood: str = "chill",
                        sr: int = BEAT_SAMPLE_RATE) -> str:
    preset = MOOD_PRESETS.get(mood, MOOD_PRESETS["chill"])
    bpm = preset["bpm"]

    n_samples = int(sr * duration)
    mix = np.zeros(n_samples)

    beat_dur = 60.0 / bpm
    bar_dur  = beat_dur * 4

    chord_progression = preset["chords"]

    n_bars = max(1, int(math.ceil(duration / bar_dur)))
    for bar in range(n_bars):
        chord = chord_progression[bar % len(chord_progression)]
        start_sample = int(bar * bar_dur * sr)

        for semis in chord:
            freq = _note_freq(semis)
            tone = _sine(freq, bar_dur, sr, amp=0.10)
            tone *= _envelope(len(tone), attack=0.05, release=0.6)
            _mix(mix, tone, start_sample)

        for beat in range(4):
            beat_sample = start_sample + int(beat * beat_dur * sr)
            if beat in (0, 2):
                _mix(mix, _kick(sr) * preset["kick_amp"], beat_sample)
            if beat in (1, 3):
                _mix(mix, _snare(sr) * preset["snare_amp"], beat_sample)
            _mix(mix, _hat(sr), beat_sample + int(beat_dur * sr * 0.5))

    if len(mix) < n_samples:
        mix = np.pad(mix, (0, n_samples - len(mix)))
    mix = mix[:n_samples]

    mix = _lowpass(mix, strength=0.6)
    mix += _vinyl_crackle(n_samples, amount=0.015)

    peak = np.max(np.abs(mix)) or 1.0
    mix = (mix / peak) * 0.85
    pcm = (mix * 32767).astype(np.int16)

    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    return path


def setup_music(duration: float = 60.0, mood: str = "chill") -> bool:
    try:
        print(f"  🎵 UGH! Smashing rocks together to make beat (mood: {mood})…")
        generate_lofi_beat(duration, MUSIC_PATH, mood=mood)
        print(f"  🎵 Beat made! Stored at fire-pit → {MUSIC_PATH}")
        return True
    except Exception as e:
        print(f"  ⚠️  Beat generator angry: {e} — video will be silent.")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RSS FETCH
# ─────────────────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PHNewsBot/1.0)"}

def strip_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(raw)).strip()


def _looks_like_image(url: str) -> bool:
    return bool(re.search(r'\.(jpg|jpeg|png|webp)(\?.*)?$', url, re.IGNORECASE))


_LOGO_SKIP_PATTERNS = re.compile(
    r'(logo|favicon|icon|sprite|brand|header|badge|avatar|watermark)',
    re.IGNORECASE
)


def _is_article_image(url: str) -> bool:
    return _looks_like_image(url) and not _LOGO_SKIP_PATTERNS.search(url)


def scrape_og_image(article_url: str) -> str:
    if not article_url:
        return ""
    try:
        r = requests.get(
            article_url, headers=HEADERS, timeout=8,
            allow_redirects=True
        )
        r.raise_for_status()
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r.text, re.IGNORECASE
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r.text, re.IGNORECASE
            )
        if m:
            url = m.group(1).strip()
            if _is_article_image(url):
                print(f"    🖼  og:image scraped: {url[:80]}…")
                return url
    except Exception as e:
        print(f"    ⚠️  og:image scrape failed for {article_url[:60]}: {e}")
    return ""


def extract_image_from_item(item, raw_xml_text: str = "") -> str:
    for tag in [
        "{http://search.yahoo.com/mrss/}content",
        "{http://search.yahoo.com/mrss/}thumbnail",
        "media:content", "media:thumbnail",
    ]:
        el = item.find(tag)
        if el is not None:
            url = el.get("url", "")
            if url and _is_article_image(url):
                return url

    enc = item.find("enclosure")
    if enc is not None:
        url = enc.get("url", "")
        t   = enc.get("type", "")
        if url and ("image" in t or _is_article_image(url)):
            return url

    desc_raw = item.findtext("description", "") or ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw, re.IGNORECASE)
    if m:
        url = m.group(1)
        if _is_article_image(url):
            return url

    try:
        item_xml = ET.tostring(item, encoding="unicode")
    except Exception:
        item_xml = ""
    if item_xml:
        matches = re.findall(
            r'https?://[^\s\'"<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\'"<>]*)?',
            item_xml, re.IGNORECASE
        )
        for url in matches:
            if "1x1" not in url and "pixel" not in url.lower() and _is_article_image(url):
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
                    if not image_url:
                        image_url = scrape_og_image(link)
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
    """
    Download article photo at NATIVE resolution — no upscaling, no sharpening!
    Sharpening is done ONCE after the final crop in create_slide.
    """
    if not image_url:
        return None
    try:
        print(f"  📷 Fetching article image: {image_url[:80]}…")
        r = requests.get(image_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        w, h = img.size
        print(f"  ✅ Article image loaded at native size: {w}×{h}")
        return img
    except Exception as e:
        print(f"  ⚠️  Could not fetch article image: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SLIDE CONTENT — Groq or fallback (8 slides for Reel)
# ─────────────────────────────────────────────────────────────────────────────
def generate_slides_groq(article: dict) -> list[str] | None:
    prompt = f"""You are a social media content writer for a global news page, similar to NowThis or BBC News on Instagram.
Write content for an 8-slide video Reel about this news story:

HEADLINE: {article['title']}
DETAILS: {article['desc']}
CATEGORY: {article['category']}

INSTRUCTIONS:
- Write in clear, punchy English — casual but credible, easy to read fast
- KEEP EACH SLIDE SHORT (max 20 words) — it needs to be readable instantly in a video
- Slide 1: Attention-grabbing hook headline — dramatic, curiosity-inducing
- Slide 2: Simple explanation — what happened?
- Slide 3: An important detail or number
- Slide 4: Another key point or context
- Slide 5: Why this matters to the average person
- Slide 6: A quick takeaway or piece of advice
- Slide 7: In short — one sentence summary
- Slide 8: CTA — "Follow us for more news like this every day!"

Format your answer as a JSON array ONLY (no other text):
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
        gs(0, "Here's what you need to know."),
        gs(1, "This is one of today's biggest stories."),
        gs(2, "People around the world are following this closely."),
        "This could affect more people than you'd expect.",
        "Stay tuned — follow official updates as the story develops.",
        f"One of today's top stories in {article['category']}.",
        f"Follow {PAGE_NAME} for more news like this every day! 🔥",
    ]


def generate_slides(article: dict) -> list[str]:
    if GROQ_API_KEY:
        print("  🤖 Generating content with Groq (Llama 3)…")
        texts = generate_slides_groq(article)
        if texts and len(texts) >= 6:
            while len(texts) < 8:
                texts.append(f"Follow {PAGE_NAME} for more news like this! 🔥")
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


def crop_needed_size(img: Image.Image, target_w: int, target_h: int, centering_y: float = 0.35) -> Image.Image:
    """
    Fills the entire target size by maintaining aspect ratio and cropping.
    Used for the blurred background.
    """
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    
    crop_x = int((new_w - target_w) * 0.5)
    crop_y = int((new_h - target_h) * centering_y)
    crop_y = max(0, min(crop_y, new_h - target_h))
    
    return img_resized.crop((crop_x, crop_y, crop_x + target_w, crop_y + target_h))


def create_blurred_bg_and_fg(img: Image.Image, target_w: int, target_h: int):
    """
    Creates a blurred background that fills the screen, and a sharp
    foreground image that fits natively without upscaling.
    Returns (bg_image, fg_image, fg_w, fg_h)
    """
    # 1. Blurred Background
    bg = crop_needed_size(img, target_w, target_h)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
    bg = ImageEnhance.Brightness(bg).enhance(0.4)
    
    # 2. Sharp Foreground
    src_w, src_h = img.size
    max_w = target_w - 80  # small margin on sides
    max_h = int(target_h * 0.50) # max 50% of screen height
    
    # Scale to fit inside max_w x max_h
    scale = min(max_w / src_w, max_h / src_h)
    
    # Cap upscaling to 1.5x to prevent tiny images from becoming blurry
    if scale > 1.5: scale = 1.5  
    
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    fg = img.resize((new_w, new_h), Image.LANCZOS)
    fg = fg.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    
    return bg, fg, new_w, new_h


def create_slide(text: str, idx: int, total: int, category: str,
                 article_photo=None) -> Image.Image:
    """Draw a single 1080×1920 slide image."""
    cat    = CATEGORIES.get(category, CATEGORIES["WORLD"])
    accent = cat["rgb"]
    emoji  = cat["emoji"]

    is_hook = idx == 0
    is_cta  = idx == total - 1
    use_photo = article_photo and not is_cta

    # Start with a dark base
    img  = Image.new("RGB", (IMG_W, IMG_H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # ── Top accent stripe
    draw.rectangle([(0, 0), (IMG_W, 14)], fill=accent)

    # ── Category pill (top-left)
    pill_font = get_font(34)
    pill_text = category
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

    # ── HOOK & CONTENT SLIDES (Unified Clean Layout)
    if not is_cta:
        if use_photo:
            # Get blurred bg and sharp fg
            bg_photo, fg_photo, fg_w, fg_h = create_blurred_bg_and_fg(article_photo, IMG_W, IMG_H)
            img.paste(bg_photo, (0, 0))
            
            # Paste sharp foreground image at top, horizontally centered
            paste_x = (IMG_W - fg_w) // 2
            paste_y = 120
            img.paste(fg_photo, (paste_x, paste_y))
            
            # Apply clean dark gradient overlay for text readability
            overlay = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            start_y = int(IMG_H * 0.35)  # Start fading from 35% down
            for y in range(start_y, IMG_H):
                progress = (y - start_y) / (IMG_H - start_y)
                alpha = int(225 * (progress ** 1.4))  # Smooth curve, up to 225 opacity
                od.line([(0, y), (IMG_W, y)], fill=(0, 0, 0, alpha))
            img_rgba = img.convert("RGBA")
            img_rgba.alpha_composite(overlay)
            img = img_rgba.convert("RGB")
            draw = ImageDraw.Draw(img)
            
        else:
            # No photo: Simple dark gradient
            overlay = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            for y in range(IMG_H):
                alpha = int(140 * (y / IMG_H))
                od.line([(0, y), (IMG_W, y)], fill=(0, 0, 0, alpha))
            img_rgba = img.convert("RGBA")
            img_rgba.alpha_composite(overlay)
            img = img_rgba.convert("RGB")
            draw = ImageDraw.Draw(img)

        # Reapply pill + counter on top of everything
        draw_rounded_rect(draw, px, py, px + pw, py + ph, 12, accent)
        draw.text((px + 22, py + 12), pill_text, font=pill_font, fill=C_WHITE)
        draw.text((IMG_W - 64, 58), f"{idx+1}/{total}",
                  font=ctr_font, anchor="rm", fill=C_GRAY)

        pad = 64
        max_w = IMG_W - pad * 2

        # Hook Slide Text Logic (Two-Tone)
        if is_hook:
            words = text.split()
            mid   = max(1, len(words) // 2)
            line1_text = " ".join(words[:mid])
            line2_text = " ".join(words[mid:])

            font_h, _ = fit_text(draw, text, 76, max_w, 6)
            fs = font_h.size
            lh = fs + 20

            all_lines = []
            for chunk, colour in [(line1_text, accent), (line2_text, C_WHITE)]:
                if not chunk.strip():
                    continue
                _, chunk_lines = fit_text(draw, chunk.upper(), fs, max_w, 3)
                all_lines.extend([(l, colour) for l in chunk_lines])

            total_text_h = len(all_lines) * lh
            # Center text in the lower-middle zone (between 55% and 88% height)
            text_zone_top = int(IMG_H * 0.55)
            text_zone_bot = int(IMG_H * 0.88)
            y = text_zone_top + (text_zone_bot - text_zone_top - total_text_h) // 2

            for line_txt, line_col in all_lines:
                bx = draw.textbbox((0, 0), line_txt, font=font_h)[2]
                x  = (IMG_W - bx) // 2
                draw_text_shadow(draw, (x, y), line_txt, font_h, line_col,
                                 shadow_offset=5, shadow_color=(0, 0, 0, 220))
                y += lh

            # Thin accent divider line below text
            draw.rectangle([(IMG_W//2 - 100, y + 18), (IMG_W//2 + 100, y + 25)], fill=accent)

        # Content Slide Text Logic
        else:
            label = SLIDE_LABELS[idx] if idx < len(SLIDE_LABELS) else ""
            font, lines = fit_text(draw, text, 64, max_w, 6) # Slightly smaller for content
            fs = font.size
            lh = fs + 24
            th = len(lines) * lh
            
            # Center text in the lower-middle zone (between 55% and 88% height)
            text_zone_top = int(IMG_H * 0.55)
            text_zone_bot = int(IMG_H * 0.88)
            ty = text_zone_top + (text_zone_bot - text_zone_top - th) // 2

            # Draw Label above text
            if label:
                lbl_font = get_font(36)
                lbl_bbox = draw.textbbox((0, 0), label, font=lbl_font)
                lbl_w = lbl_bbox[2]
                lbl_x = (IMG_W - lbl_w) // 2
                lbl_y = ty - 80
                draw.text((lbl_x, lbl_y), label, font=lbl_font, fill=accent)
                draw.rectangle([(lbl_x, lbl_y + lbl_bbox[3] + 6), 
                                (lbl_x + lbl_w, lbl_y + lbl_bbox[3] + 12)], fill=accent)

            # Draw Body Text (Two-tone)
            for i, line in enumerate(lines):
                colour = accent if i == 0 else C_WHITE
                bx = draw.textbbox((0, 0), line, font=font)[2]
                x  = (IMG_W - bx) // 2
                draw_text_shadow(draw, (x, ty), line, font, colour,
                                 shadow_offset=4, shadow_color=(0, 0, 0, 200))
                ty += lh

    # ── CTA SLIDE
    else:
        overlay  = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 120))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img  = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        centre_y = IMG_H // 2 - 60

        import math as _math
        star_cx, star_cy = IMG_W // 2, centre_y - 130
        for _angle in range(0, 360, 20):
            _r_inner = 38
            _r_outer = 82
            _x1 = star_cx + int(_r_inner * _math.cos(_math.radians(_angle)))
            _y1 = star_cy + int(_r_inner * _math.sin(_math.radians(_angle)))
            _x2 = star_cx + int(_r_outer * _math.cos(_math.radians(_angle + 10)))
            _y2 = star_cy + int(_r_outer * _math.sin(_math.radians(_angle + 10)))
            draw.line([(_x1, _y1), (_x2, _y2)], fill=accent, width=7)
        draw.ellipse([(star_cx - 30, star_cy - 30), (star_cx + 30, star_cy + 30)], fill=accent)

        draw.text((IMG_W // 2, centre_y + 50), "FOLLOW US ON FACEBOOK",
                  font=get_font(40, bold=False), anchor="mm", fill=C_GRAY)
        draw.text((IMG_W // 2, centre_y + 155), "AI Academy",
                  font=get_font(84), anchor="mm", fill=C_WHITE)
        
        # EXPLICITLY HARDCODED FACEBOOK URL
        draw.text((IMG_W // 2, centre_y + 265),
                  "facebook.com/aiacademylearning",
                  font=get_font(40, bold=False), anchor="mm", fill=accent)
        
        draw.rectangle([(200, centre_y + 330), (IMG_W - 200, centre_y + 338)], fill=accent)
        draw.text((IMG_W // 2, centre_y + 395), "For the latest world news!",
                  font=get_font(38, bold=False), anchor="mm", fill=C_GRAY)
        draw.text((IMG_W // 2, centre_y + 460), "Follow now — it is free!",
                  font=get_font(34, bold=False), anchor="mm", fill=C_GRAY)

        draw.text((IMG_W // 2, IMG_H - 130),
                  "Share this with a friend!",
                  font=get_font(32, bold=False), anchor="mm", fill=C_GRAY)

    # ── Bottom branding bar
    draw.rectangle([(0, IMG_H - 90), (IMG_W, IMG_H)], fill=BG_CARD)
    draw.rectangle([(0, IMG_H - 90), (IMG_W, IMG_H - 88)], fill=accent)
    brand_font = get_font(34, bold=False)
    draw.text((IMG_W // 2, IMG_H - 44), "@ranksorcery.com",
              font=brand_font, anchor="mm", fill=C_GRAY)

    return img


# ─────────────────────────────────────────────────────────────────────────────
# KEN BURNS ANIMATION
# ─────────────────────────────────────────────────────────────────────────────
def make_ken_burns_clip(pil_img: Image.Image, duration: float,
                        zoom_in: bool = True, fps: int = FPS):
    img_array = np.array(pil_img)
    h, w      = img_array.shape[:2]
    zoom_start = 1.0
    zoom_end   = 1.0 + ZOOM_AMOUNT

    if not zoom_in:
        zoom_start, zoom_end = zoom_end, zoom_start

    def make_frame(t):
        progress = t / duration
        scale    = zoom_start + (zoom_end - zoom_start) * progress

        crop_w = int(w / scale)
        crop_h = int(h / scale)

        offset_x = int((w - crop_w) * 0.5)
        offset_y = int((h - crop_h) * 0.5)

        if zoom_in:
            offset_x += int((w - crop_w) * 0.1 * progress)
        else:
            offset_x += int((w - crop_w) * 0.1 * (1 - progress))

        offset_x = max(0, min(offset_x, w - crop_w))
        offset_y = max(0, min(offset_y, h - crop_h))

        cropped = img_array[offset_y:offset_y + crop_h, offset_x:offset_x + crop_w]
        cropped_pil = Image.fromarray(cropped).resize((w, h), Image.LANCZOS)
        return np.array(cropped_pil)

    return VideoClip(make_frame, duration=duration)


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────
def build_reel(images: list, output_path: str, has_music: bool) -> str:
    print(f"\n🎬 Assembling {len(images)} slides into video…")

    clips = []
    for i, pil_img in enumerate(images):
        zoom_in = (i % 2 == 0)
        clip    = make_ken_burns_clip(pil_img, SLIDE_DURATION, zoom_in=zoom_in)
        clip    = clip.set_fps(FPS)

        if i > 0:
            clip = clip.crossfadein(FADE_DURATION)

        clips.append(clip)
        print(f"   Slide {i+1}/{len(images)} animated ✓")

    video = concatenate_videoclips(clips, method="compose",
                                   padding=-FADE_DURATION)

    if has_music and os.path.exists(MUSIC_PATH):
        try:
            print("  🎵 Mixing background music…")
            audio       = AudioFileClip(MUSIC_PATH)
            total_dur   = video.duration

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

    print(f"\n🎞️  Rendering MP4 → {output_path}  (this takes ~30-60 seconds)…")
    video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",  # Better quality encoding preset
        ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p"],  # Lower CRF = higher quality (less pixelation)
        logger=None,
    )
    print(f"  ✅ Video rendered! Size: {os.path.getsize(output_path) / 1024 / 1024:.1f} MB")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO UPLOAD (throwaway GitHub Release asset — public direct-download URL)
# ─────────────────────────────────────────────────────────────────────────────
def create_github_release(tag: str, repo: str, token: str) -> dict:
    r = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "tag_name": tag,
            "name": tag,
            "body": "Auto-generated Reel video asset — safe to delete.",
            "draft": False,
            "prerelease": False,
        },
        timeout=30,
    )
    if not r.ok:
        print(f"  GitHub release create error: {r.status_code} — {r.text}")
    r.raise_for_status()
    return r.json()


def upload_asset_to_release(upload_url: str, video_path: str, token: str) -> str:
    upload_url = upload_url.split("{")[0]
    filename = os.path.basename(video_path)

    with open(video_path, "rb") as f:
        data = f.read()

    r = requests.post(
        upload_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "video/mp4",
        },
        params={"name": filename},
        data=data,
        timeout=180,
    )
    if not r.ok:
        print(f"  GitHub asset upload error: {r.status_code} — {r.text}")
    r.raise_for_status()
    return r.json()["browser_download_url"]


def delete_github_release(release_id: int, repo: str, token: str) -> None:
    try:
        requests.delete(
            f"https://api.github.com/repos/{repo}/releases/{release_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=30,
        )
    except Exception as e:
        print(f"  ⚠️  Could not clean up release: {e} (not fatal — delete manually if you like)")


def upload_video_to_github_release(video_path: str) -> tuple:
    repo  = os.environ["GITHUB_REPOSITORY"]
    token = GH_RELEASE_TOKEN
    if not token:
        raise RuntimeError("No GH_RELEASE_TOKEN or GITHUB_TOKEN available — can't create a release.")

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    print(f"  ☁️  Uploading video ({size_mb:.1f} MB) to a GitHub Release…")

    tag = f"reel-{int(time.time())}"
    release = create_github_release(tag, repo, token)
    print(f"  📦 Release created: {tag} (id={release['id']})")

    url = upload_asset_to_release(release["upload_url"], video_path, token)
    print(f"  ✅ Video hosted at: {url}")
    return url, release["id"]


# ─────────────────────────────────────────────────────────────────────────────
# FACEBOOK GRAPH API — PAGE VIDEO POSTING
# ─────────────────────────────────────────────────────────────────────────────
def fb_post(path: str, **params) -> dict:
    r = requests.post(
        f"{FB_BASE}/{path}",
        params={"access_token": FB_ACCESS_TOKEN, **params},
        timeout=60,
    )
    if not r.ok:
        print(f"  FB API error: {r.status_code} — {r.text}")
    r.raise_for_status()
    return r.json()


def fb_get(path: str, **params) -> dict:
    r = requests.get(
        f"{FB_BASE}/{path}",
        params={"access_token": FB_ACCESS_TOKEN, **params},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def upload_video_to_page(video_url: str, description: str) -> str:
    data = fb_post(
        f"{FB_PAGE_ID}/videos",
        file_url=video_url,
        description=description,
    )
    return data["id"]


def wait_for_video_ready(video_id: str, retries: int = 24, interval: int = 10):
    for attempt in range(retries):
        status = fb_get(video_id, fields="status").get("status", {})
        video_status = status.get("video_status", "unknown")
        print(f"    Video {video_id}: {video_status}  (attempt {attempt+1}/{retries})")
        if video_status == "ready":
            return
        if video_status == "error":
            raise RuntimeError(f"Video {video_id} errored during processing.")
        time.sleep(interval)
    print("    ⚠️  Didn't confirm 'ready' status in time — continuing anyway.")


def post_comment(video_id: str, message: str) -> str:
    r = requests.post(
        f"{FB_BASE}/{video_id}/comments",
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
    emoji = CATEGORIES.get(cat, CATEGORIES["WORLD"])["emoji"]
    tags  = HASHTAG_MAP.get(cat, "#WorldNews")
    return (
        f"{emoji} {article['title']}\n\n"
        "👆 Watch for the full story!\n"
        "📤 Share this with a friend!\n\n"
        f"{tags} #News #DailyNews"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  🎬 World News Facebook REEL Bot — Animated Video Edition")
    print("=" * 60)

    if not MOVIEPY_OK:
        print("❌ moviepy is not installed!")
        print("   Run: pip install moviepy numpy")
        sys.exit(1)

    print("\n📦 Setting up fonts…")
    setup_fonts()

    print("\n📰 Fetching articles from RSS feeds…")
    articles = fetch_articles()
    if not articles:
        print("❌ No articles fetched. Check feeds or network. Exiting.")
        sys.exit(1)
    print(f"   Found {len(articles)} articles across all feeds.")

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

    mood = CATEGORY_MOOD.get(article["category"], "chill")
    print(f"\n🎵 Setting up background beat (category {article['category']} → mood '{mood}')…")
    est_duration = len(SLIDE_LABELS) * SLIDE_DURATION + 2.0
    has_music = setup_music(duration=est_duration, mood=mood)

    print("\n📷 Fetching article photo…")
    article_photo = fetch_article_image(article.get("image_url", ""))
    if not article_photo:
        print("   ℹ️  No article photo — slides use the dark branded background.")

    print("\n✍️  Generating slide content…")
    slide_texts = generate_slides(article)
    for i, t in enumerate(slide_texts):
        print(f"   Slide {i+1}: {t[:60]}…")

    print("\n🎨 Creating slide images (1080×1920)…")
    images = []
    for i, text in enumerate(slide_texts):
        img = create_slide(text, i, len(slide_texts), article["category"],
                           article_photo=article_photo)
        images.append(img)
        print(f"   Slide {i+1}/{len(slide_texts)} ✓")

    output_path = "/tmp/world_news_reel.mp4"
    build_reel(images, output_path, has_music)

    print("\n☁️  Uploading video…")
    video_url, release_id = upload_video_to_github_release(output_path)

    caption = build_caption(article)

    print("\n📱 Uploading video to Facebook Page…")
    video_id = upload_video_to_page(video_url, caption)
    print(f"   Video ID: {video_id}")

    print("\n⏳ Waiting for video to process (takes ~1-3 min)…")
    wait_for_video_ready(video_id, retries=24, interval=10)

    print(f"\n✅ SUCCESS! Posted to Facebook Page. Video ID: {video_id}")
    post_id = video_id

    time.sleep(5)
    print("\n💬 Posting comments…")
    try:
        c1 = post_comment(post_id, f"📰 Source: {article['link']}")
        print(f"   ✅ Comment 1 posted (source): {c1}")
    except Exception as e:
        print(f"   ⚠️  Could not post source comment: {e}")

    time.sleep(3)

    try:
        c2 = post_comment(post_id, "Wondering how this is done? It's all run through our automated systems. Feel free to reach out at https://ranksorcery.com/ if you're interested in a similar setup.")
        print(f"   ✅ Comment 2 posted (site): {c2}")
    except Exception as e:
        print(f"   ⚠️  Could not post site comment: {e}")

    print("\n🔥 Done! Automation complete. 🌍")
    print("=" * 60)

    print("\n🧹 Cleaning up temporary GitHub release…")
    delete_github_release(release_id, os.environ["GITHUB_REPOSITORY"], GH_RELEASE_TOKEN)


if __name__ == "__main__":
    main()