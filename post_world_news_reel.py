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


def generate_lofi_beat(duration: float, path: str, mood: str = "chill",
                        sr: int = BEAT_SAMPLE_RATE) -> str:
    """
    Build a simple lofi loop entirely with numpy math — sine-wave
    chords + drum hits synthesized from scratch, then lowpass-filtered
    and vinyl-crackled for that dusty bedroom-producer vibe.
    No internet, no audio files, no API keys. Just rocks and sticks.

    `mood` picks the tempo/chords/drum intensity — one of "chill",
    "dramatic", "upbeat" (see MOOD_PRESETS). Falls back to "chill"
    if an unknown mood is passed.
    """
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
                _mix(mix, _kick(sr) * preset["kick_amp"], beat_sample)
            if beat in (1, 3):
                _mix(mix, _snare(sr) * preset["snare_amp"], beat_sample)
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


def setup_music(duration: float = 60.0, mood: str = "chill") -> bool:
    """
    Generate the background beat in pure Python — guaranteed to work,
    no download, no flaky third-party server to beg for fire.
    `mood` should be one of "chill", "dramatic", "upbeat" (see MOOD_PRESETS).
    """
    try:
        print(f"  🎵 UGH! Smashing rocks together to make beat (mood: {mood})…")
        generate_lofi_beat(duration, MUSIC_PATH, mood=mood)
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


# Patterns that indicate a site logo / icon rather than an article photo.
# Me not want logo — me want REAL photo!
_LOGO_SKIP_PATTERNS = re.compile(
    r'(logo|favicon|icon|sprite|brand|header|badge|avatar|watermark)',
    re.IGNORECASE
)


def _is_article_image(url: str) -> bool:
    """Return True only if URL looks like a real article photo (not a logo)."""
    return _looks_like_image(url) and not _LOGO_SKIP_PATTERNS.search(url)


def scrape_og_image(article_url: str) -> str:
    """
    Scrape the article page for its og:image meta tag.
    Used as fallback when RSS gives no per-item image (e.g. Al Jazeera).
    Returns the image URL string or "" on failure.
    """
    if not article_url:
        return ""
    try:
        r = requests.get(
            article_url, headers=HEADERS, timeout=8,
            allow_redirects=True
        )
        r.raise_for_status()
        # Fast regex — no need for full HTML parser
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r.text, re.IGNORECASE
        )
        if not m:
            # Try reversed attribute order: content before property
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
    # 1. media:content / media:thumbnail (most feeds)
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

    # 2. <enclosure>
    enc = item.find("enclosure")
    if enc is not None:
        url = enc.get("url", "")
        t   = enc.get("type", "")
        if url and ("image" in t or _is_article_image(url)):
            return url

    # 3. <img> tag inside description HTML
    desc_raw = item.findtext("description", "") or ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw, re.IGNORECASE)
    if m:
        url = m.group(1)
        if _is_article_image(url):
            return url

    # 4. Last-resort: scan raw XML of THIS ITEM ONLY for image URLs.
    #    UGH! Old code scanned the WHOLE feed XML — that is how it found
    #    the channel logo and used it for every Al Jazeera article!
    #    Now me only scan the XML fragment for this specific item.
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

    return ""  # Caller will try og:image scraping as final fallback


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
                    # If RSS gave no image (common with Al Jazeera, NPR, etc.),
                    # scrape the article page for its og:image — usually a
                    # high-quality editorial photo!
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
    Download article photo at NATIVE resolution — no upscale!
    UGH! Old code force-resize small web image to 1080×1920 — THAT
    is where pixelation came from! Keep native size, sharpen once,
    let downstream code do ONE targeted resize.
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
        # Sharpen at native res — detail preserved before any later resize
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=2))
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
            # pad to 8 if Groq returned fewer
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


def make_bg(photo, accent: tuple, blur: int = 10, darkness: float = 0.55):
    """
    Return 1080×1920 background.
    Photo may be at native resolution — we resize to canvas HERE
    for the blurred background. Blur hides upscaling artifacts, so safe.
    """
    if photo:
        bg = photo.copy()
        if bg.size != (IMG_W, IMG_H):
            bg = ImageOps.fit(bg, (IMG_W, IMG_H), method=Image.LANCZOS, centering=(0.5, 0.3))
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
    cat    = CATEGORIES.get(category, CATEGORIES["WORLD"])
    accent = cat["rgb"]
    emoji  = cat["emoji"]

    is_hook = idx == 0
    is_cta  = idx == total - 1
    use_photo = article_photo and not is_cta

    bg   = make_bg(article_photo if use_photo else None, accent,
                   blur=10 if is_hook else 14,
                   darkness=0.45 if is_hook else 0.50)   # lighter blur-bg for content slides
    img  = bg.copy()
    draw = ImageDraw.Draw(img)

    # ── Top accent stripe
    draw.rectangle([(0, 0), (IMG_W, 14)], fill=accent)

    # ── Category pill (top-left)
    pill_font = get_font(34)
    pill_text = category   # no emoji — Poppins font no have emoji glyphs, would show broken box
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
        draw.text((IMG_W // 2, IMG_H - 130), "SWIPE UP for the full story",
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

        # Decorative starburst instead of broken emoji
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
        draw.text((IMG_W // 2, centre_y + 265),
                  f"facebook.com/{PAGE_NAME}",
                  font=get_font(40, bold=False), anchor="mm", fill=accent)
        draw.rectangle([(200, centre_y + 330), (IMG_W - 200, centre_y + 338)], fill=accent)
        draw.text((IMG_W // 2, centre_y + 395), "For the latest world news!",
                  font=get_font(38, bold=False), anchor="mm", fill=C_GRAY)
        draw.text((IMG_W // 2, centre_y + 460), "Follow now — it is free!",
                  font=get_font(34, bold=False), anchor="mm", fill=C_GRAY)

        # DM-share nudge
        draw.text((IMG_W // 2, IMG_H - 130),
                  "Share this with a friend!",
                  font=get_font(32, bold=False), anchor="mm", fill=C_GRAY)

    # ── CONTENT SLIDES
    else:
        label = SLIDE_LABELS[idx] if idx < len(SLIDE_LABELS) else ""

        if use_photo:
            # ── NEW DESIGN: clear photo on top, dark card + text on bottom ──
            #
            # The old design pasted an opaque dark card over the ENTIRE slide,
            # which buried the photo completely. Now we:
            #  1. Paste the clear (unblurred) photo in the upper zone
            #  2. Fade it into a dark card below
            #  3. Put all text inside the dark card zone

            photo_zone_top = 140        # starts below pill/counter row
            photo_zone_h   = 780        # show 780px of clear photo (about 40%)
            photo_zone_bot = photo_zone_top + photo_zone_h   # y=920

            # Use ImageOps.fit for crop-to-fill (like CSS object-fit:cover).
            # Fit directly from native resolution → strip size in ONE step.
            # Anchor top-center so faces/subjects stay in frame.
            photo_strip = ImageOps.fit(
                article_photo,
                (IMG_W, photo_zone_h),
                method=Image.LANCZOS,
                centering=(0.5, 0.0)   # 0.0 = anchor top edge, keeps top of photo
            )
            # Sharpen after resize to recover crisp detail
            photo_strip = photo_strip.filter(
                ImageFilter.UnsharpMask(radius=1.2, percent=150, threshold=3)
            )
            img.paste(photo_strip, (0, photo_zone_top))

            # Smooth gradient fade at photo bottom  →  dark card transition
            grad = Image.new("RGBA", (IMG_W, 220), (0, 0, 0, 0))
            grad_d = ImageDraw.Draw(grad)
            for _gy in range(220):
                _a = int((_gy / 220) ** 1.3 * 248)
                grad_d.line([(0, _gy), (IMG_W, _gy)], fill=(13, 17, 28, _a))
            img_rgba = img.convert("RGBA")
            img_rgba.alpha_composite(grad, (0, photo_zone_bot - 110))
            img = img_rgba.convert("RGB")

            # Solid dark card for the text zone
            card_overlay = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
            card_draw    = ImageDraw.Draw(card_overlay)
            card_draw.rectangle([(0, photo_zone_bot + 80), (IMG_W, IMG_H - 90)],
                                 fill=(13, 17, 28, 235))
            img_rgba = img.convert("RGBA")
            img_rgba.alpha_composite(card_overlay)
            img  = img_rgba.convert("RGB")
            draw = ImageDraw.Draw(img)

            # Text zone lives inside the dark card
            content_top = photo_zone_bot + 120
            content_bot = IMG_H - 180
        else:
            # No photo — full dark background, text fills the canvas
            content_top = 280 if label else 200
            content_bot = IMG_H - 180

        # Reapply pill + counter on top of everything
        draw_rounded_rect(draw, px, py, px + pw, py + ph, 12, accent)
        draw.text((px + 22, py + 12), pill_text, font=pill_font, fill=C_WHITE)
        draw.text((IMG_W - 64, 58), f"{idx+1}/{total}",
                  font=ctr_font, anchor="rm", fill=C_GRAY)

        # Label — placed differently depending on whether photo is shown
        if label:
            lbl_font = get_font(40)
            lbl_bbox = draw.textbbox((0, 0), label, font=lbl_font)
            lbl_w    = lbl_bbox[2]
            lbl_x    = (IMG_W - lbl_w) // 2
            lbl_y    = (content_top - 75) if use_photo else 170
            draw.text((lbl_x, lbl_y), label, font=lbl_font, fill=accent)
            draw.rectangle([(lbl_x, lbl_y + lbl_bbox[3] + 8),
                             (lbl_x + lbl_w, lbl_y + lbl_bbox[3] + 14)], fill=accent)

        # Body text — centred vertically in the available text zone
        pad   = 80
        max_w = IMG_W - pad * 2
        font, lines = fit_text(draw, text, 72, max_w, 8)
        fs    = font.size
        lh    = fs + 24
        th    = len(lines) * lh
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
    draw.text((IMG_W // 2, IMG_H - 44), "@ranksorcery.com",
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

    return VideoClip(make_frame, duration=duration)


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
# VIDEO UPLOAD (throwaway GitHub Release asset — public direct-download URL)
# We upload to a temporary file host that returns a public URL for the IG API.
# Strategy: create a release, attach the MP4 as an asset, grab its
# browser_download_url, then delete the release once IG has the video.
# ─────────────────────────────────────────────────────────────────────────────
def create_github_release(tag: str, repo: str, token: str) -> dict:
    """Create a new (non-draft) GitHub release to attach the video asset to."""
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
    """Upload the MP4 as a release asset. Returns the public browser_download_url."""
    # upload_url comes back like ".../assets{?name,label}" — strip the template part
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
    """Best-effort cleanup — delete the release after IG has fetched the video."""
    try:
        requests.delete(
            f"https://api.github.com/repos/{repo}/releases/{release_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=30,
        )
    except Exception as e:
        print(f"  ⚠️  Could not clean up release: {e} (not fatal — delete manually if you like)")


def upload_video_to_github_release(video_path: str) -> tuple:
    """
    Upload the MP4 as an asset on a throwaway GitHub Release in this repo.
    Returns (public_url, release_id) — release_id lets the caller clean up
    after Facebook has finished pulling the video.

    Requires:
      GITHUB_TOKEN       — auto-provided by Actions (needs 'contents: write' permission)
      GITHUB_REPOSITORY  — auto-provided by Actions, e.g. "owner/repo"
    NOTE: the repo must be PUBLIC — Facebook's servers fetch the asset URL
    without any auth header, and private-repo release assets require auth to download.
    """
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
# Facebook's Page Video endpoint is simpler than Instagram's: one POST with
# file_url uploads AND publishes in a single call — no separate container
# + publish step. Facebook still processes the video async behind the
# scenes, so we poll /{video_id}?fields=status until it's done.

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
    """
    Upload (and publish) a video to the Facebook Page in one call.
    Returns the video ID.
    """
    data = fb_post(
        f"{FB_PAGE_ID}/videos",
        file_url=video_url,
        description=description,
    )
    return data["id"]


def wait_for_video_ready(video_id: str, retries: int = 24, interval: int = 10):
    """Poll until Facebook finishes processing the uploaded video."""
    for attempt in range(retries):
        status = fb_get(video_id, fields="status").get("status", {})
        video_status = status.get("video_status", "unknown")
        print(f"    Video {video_id}: {video_status}  (attempt {attempt+1}/{retries})")
        if video_status == "ready":
            return
        if video_status == "error":
            raise RuntimeError(f"Video {video_id} errored during processing.")
        time.sleep(interval)
    # Not fatal — Facebook sometimes finishes processing slightly after
    # the polling window without ever reporting "ready" cleanly.
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

    # ── Check moviepy
    if not MOVIEPY_OK:
        print("❌ moviepy is not installed!")
        print("   Run: pip install moviepy numpy")
        sys.exit(1)

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

    # ── Music — generated now, matched to this article's category mood
    mood = CATEGORY_MOOD.get(article["category"], "chill")
    print(f"\n🎵 Setting up background beat (category {article['category']} → mood '{mood}')…")
    est_duration = len(SLIDE_LABELS) * SLIDE_DURATION + 2.0
    has_music = setup_music(duration=est_duration, mood=mood)

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
    output_path = "/tmp/world_news_reel.mp4"
    build_reel(images, output_path, has_music)

    # ── Upload video to a throwaway GitHub Release
    print("\n☁️  Uploading video…")
    video_url, release_id = upload_video_to_github_release(output_path)

    # ── Build caption
    caption = build_caption(article)

    # ── Upload (and publish) the video to the Facebook Page in one call
    print("\n📱 Uploading video to Facebook Page…")
    video_id = upload_video_to_page(video_url, caption)
    print(f"   Video ID: {video_id}")

    # ── Wait for Facebook to finish processing the video
    print("\n⏳ Waiting for video to process (takes ~1-3 min)…")
    wait_for_video_ready(video_id, retries=24, interval=10)

    print(f"\n✅ SUCCESS! Posted to Facebook Page. Video ID: {video_id}")
    post_id = video_id

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
        c2 = post_comment(post_id, "Wondering how this is done? It's all run through our automated systems. Feel free to reach out at https://ranksorcery.com/ if you're interested in a similar setup.")
        print(f"   ✅ Comment 2 posted (site): {c2}")
    except Exception as e:
        print(f"   ⚠️  Could not post site comment: {e}")

    print("\n🔥 Done! Automation complete. 🌍")
    print("=" * 60)

    # ── Clean up the throwaway release/asset now that IG has the video
    print("\n🧹 Cleaning up temporary GitHub release…")
    delete_github_release(release_id, os.environ["GITHUB_REPOSITORY"], GH_RELEASE_TOKEN)


if __name__ == "__main__":
    main()
