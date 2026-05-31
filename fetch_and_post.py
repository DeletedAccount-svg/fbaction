import requests
import os
import json
import re
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime
import urllib.parse
import textwrap
import subprocess
import sys

# Auto-install Pillow if not available
try:
    from PIL import Image, ImageDraw, ImageFont
    print("✅ Pillow already installed.")
except ImportError:
    print("📦 Pillow not found — installing now...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "--quiet"])
    from PIL import Image, ImageDraw, ImageFont
    print("✅ Pillow installed successfully.")

PAGE_ID = os.environ["FB_PAGE_ID"]
ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
HF_API_KEY = os.environ["HF_API_KEY"]  # Hugging Face token (hf_...)

# Hugging Face FLUX.1-dev Inference API
HF_MODEL_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-dev"

# How long to wait (seconds) after image is ready before posting to Facebook.
# HF can be slow; this also gives Facebook's API a breather. 🐢
FB_POST_DELAY = 30

# Google News RSS - trending AI news
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en"

POSTED_FILE = "posted_urls.json"


def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return json.load(f)
    return []


def save_posted(posted):
    with open(POSTED_FILE, "w") as f:
        json.dump(posted[-200:], f, indent=2)


def clean_html(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_google_news():
    """Fetch trending AI news from Google News RSS."""
    articles = []
    print("Fetching from Google News RSS...")
    try:
        resp = requests.get(
            GOOGLE_NEWS_RSS,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                source_el = item.find("source")
                if title_el is not None and link_el is not None:
                    title = clean_html(title_el.text or "")
                    title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
                    articles.append({
                        "title": title,
                        "url": (link_el.text or "").strip(),
                        "description": clean_html(desc_el.text or "") if desc_el is not None else "",
                        "source": source_el.text if source_el is not None else "Google News"
                    })
    except Exception as e:
        print(f"Google News fetch error: {e}")
    print(f"  Got {len(articles)} articles from Google News")
    return articles


def fetch_article_content(url):
    """Try to fetch and extract the actual article text."""
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html"
            },
            allow_redirects=True
        )
        resp.raise_for_status()
        html = resp.text
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL)
        text = " ".join(clean_html(p) for p in paragraphs)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 100:
            print(f"  Fetched article content: {len(text)} chars")
            return text[:2000]
    except Exception as e:
        print(f"  Could not fetch article content: {e}")
    return ""


def parse_groq_json(raw: str) -> dict | None:
    """
    Robustly extract a JSON object from Groq's response.
    Tries three strategies in order:
      1. Direct parse (model returned clean JSON).
      2. Extract the first {...} block (handles preamble/postamble text).
      3. Strip ```json ... ``` fences then parse.
    Returns the parsed dict or None on failure.
    """
    # Strategy 1 – clean JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2 – first {...} blob
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Strategy 3 – strip markdown code fences
    fenced = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced.strip())
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        pass

    return None


def groq_generate_caption_and_prompt(title, article_text, source="Google News"):
    """Use Groq to generate a Facebook caption, image prompt, and overlay headline."""
    print("Asking Groq to write caption, image prompt, and headline...")
    today = datetime.now().strftime("%B %d, %Y")

    content_for_groq = article_text[:1000] if article_text else f"Article about: {title}"
    source_credit = f"via {source}" if source else ""

    try:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        body = {
            "model": "llama3-70b-8192",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a passionate social media manager for 'AI Academy @ ranksorcery.com', a Facebook page that educates "
                        "people about artificial intelligence in an exciting and approachable way. "
                        "Your writing is vivid, enthusiastic, conversational, and uses storytelling to pull readers in.\n\n"
                        "Given an AI news article, output a JSON object with exactly THREE keys:\n\n"

                        "1. 'headline' — A short, punchy, ALL-CAPS headline (8–12 words max) that will be printed "
                        "on the post image. Make it dramatic and attention-grabbing, like a newspaper front page. "
                        "No hashtags, no emojis. Example: 'AI NOW READS YOUR EMOTIONS BETTER THAN HUMANS'\n\n"

                        "2. 'caption' — A rich, engaging Facebook post. Structure it like this:\n"
                        "   - Line 1: A bold emoji + a UNIQUE, article-specific one-liner hook written in a human, "
                        "conversational tone. This MUST directly reference what actually happened in THIS article — "
                        "name the company, technology, or event. NEVER start with generic phrases like "
                        "'Artificial intelligence is reshaping', 'AI is changing the world', or any broad AI statement. "
                        "Write like a curious, excited human sharing breaking news with a friend.\n"
                        "   - Blank line\n"
                        "   - 4–5 sentences of detailed storytelling: explain WHAT happened, WHY it matters, "
                        "HOW it changes things, and WHAT the real-world impact is. Be specific. Use the article details. "
                        "Each sentence should be full, detailed, and informative — minimum 20 words per sentence.\n"
                        "   - Blank line\n"
                        "   - A thought-provoking question that personally involves the reader.\n"
                        "   - Blank line\n"
                        f"   - '📰 {source_credit}' on its own line (source credit).\n"
                        "   - '💡 Follow AI Academy @ ranksorcery.com for daily AI insights!' on its own line.\n"
                        "   - Blank line\n"
                        "   - End with exactly this CTA block (copy it word for word):\n"
                        "'Imagine having a system that never sleeps — an AI researching and gathering real-time insights from around the world 24/7, "
                        "delivering consistent, high-quality content to your audience every single day. No effort. No burnout. Just results. "
                        "💡 Type HOW in the comments and we\'ll show you exactly how it\'s done!'\n"
                        "   - 6–8 relevant hashtags on the last line.\n"
                        "   Total length: 280–380 words.\n\n"

                        "3. 'image_prompt' — A HYPER-REALISTIC, professional photography prompt that visually represents "
                        "the article topic, as if shot by an award-winning editorial photographer for a magazine cover. "
                        "Describe a real-world photographic scene: specific real subjects, real materials, realistic textures, "
                        "professional studio or environmental lighting, and a believable, grounded setting. "
                        "Favor authentic real-life scenes (real people, real offices, real devices, real hands) over abstract "
                        "glowing sci-fi 'neural network' or 'floating hologram' clichés. "
                        "Absolutely NO text, letters, numbers, logos, or watermarks in the scene. "
                        "Under 75 words. End with EXACTLY: hyper-realistic, photorealistic, shot on Canon EOS R5, 85mm f/1.4 lens, "
                        "professional editorial photography, natural soft lighting, ultra-detailed, tack sharp focus, 8k, lifelike, "
                        "realistic skin and material texture, shallow depth of field.\n\n"

                        "CRITICAL: Output ONLY valid JSON. No markdown fences, no preamble, no extra text. "
                        "Start your response with '{' and end with '}'."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Date: {today}\n"
                        f"Article title: {title}\n"
                        f"Source: {source}\n"
                        f"Article content:\n{content_for_groq}\n\n"
                        "Generate the headline, caption, and image prompt as JSON."
                    )
                }
            ],
            "max_tokens": 1200,
            "temperature": 0.80
        }
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=body, timeout=30
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"  Groq raw response preview: {raw[:200]}...")

        result = parse_groq_json(raw)
        if result:
            caption = result.get("caption", "")
            image_prompt = result.get("image_prompt", "")
            headline = result.get("headline", title[:60].upper())
            if caption and image_prompt:
                print(f"  ✅ JSON parsed successfully!")
                print(f"  Headline: {headline}")
                print(f"  Caption length: {len(caption)} chars")
                print(f"  Image prompt: {image_prompt[:100]}...")
                return caption, image_prompt, headline
            else:
                print(f"  ⚠️ JSON parsed but missing keys. Keys found: {list(result.keys())}")
        else:
            print(f"  ❌ All JSON parse strategies failed. Raw:\n{raw[:500]}")

    except Exception as e:
        print(f"  Groq error: {e}")

    # Fallback
    print("  Using fallback caption, prompt, and headline")
    source_line = f"\n📰 {source_credit}" if source_credit else ""

    fallback_openers = [
        f"🚨 Just dropped — and this one's hard to ignore: {title}",
        f"🔥 This just changed the game: {title}",
        f"👀 You're going to want to read this — {title}",
        f"⚡ Big news in the AI space today: {title}",
        f"🧠 This story is making waves right now — {title}",
        f"💥 Here's what everyone in tech is talking about: {title}",
        f"🌐 Something significant just happened — {title}",
        f"📢 If you follow AI news, you already know about this: {title}",
    ]
    opener = random.choice(fallback_openers)

    fallback_bodies = [
        (
            f"This development is moving faster than most people realize, and the implications stretch well beyond "
            f"just the tech industry. From how businesses operate to how individuals interact with everyday tools, "
            f"the ripple effects of this news will be felt across multiple sectors in the months ahead. "
            f"Experts are already weighing in, and the conversation around responsible adoption is growing louder."
        ),
        (
            f"What makes this story stand out is the speed at which things are evolving — and the real-world "
            f"consequences that are starting to surface. Industries from healthcare and finance to education and "
            f"creative arts are all watching closely, because what happens next could redefine how we work, "
            f"create, and solve problems at scale."
        ),
        (
            f"This is the kind of development that sounds technical on the surface but has everyday impact you'll "
            f"actually feel. Whether it's how you use your phone, how your job gets done, or how businesses reach "
            f"customers — the downstream effects of stories like this are very real, and they tend to move quickly "
            f"once momentum builds."
        ),
        (
            f"Behind every headline like this is a team of researchers, engineers, and decision-makers who've been "
            f"building toward this moment for years. The fact that it's public now means the next phase — adoption, "
            f"regulation, competition — is already in motion. And if history is any guide, things are about to "
            f"accelerate considerably from here."
        ),
    ]
    body = random.choice(fallback_bodies)

    caption = (
        f"{opener}\n\n"
        f"{body}\n\n"
        f"🔥 {title}\n\n"
        f"How do you think this development will change your daily life or your industry?\n"
        f"{source_line}\n\n"
        f"💡 Follow AI Academy @ ranksorcery.com for daily AI insights!\n\n"
        f"Imagine having a system that never sleeps — an AI researching and gathering real-time insights from around the world 24/7, "
        f"delivering consistent, high-quality content to your audience every single day. No effort. No burnout. Just results. "
        f"💡 Type HOW in the comments and we'll show you exactly how it's done!\n\n"
        f"#AIAutomation #ArtificialIntelligence #AINews #MachineLearning #FutureOfAI #AIDaily #TechNews"
    )
    headline = title[:60].upper()
    image_prompt = (
        f"A realistic professional editorial photograph representing: {title}. "
        f"Real-world scene with authentic subjects, real environment, and natural professional lighting. "
        f"hyper-realistic, photorealistic, shot on Canon EOS R5, 85mm f/1.4 lens, "
        f"professional editorial photography, natural soft lighting, ultra-detailed, tack sharp focus, "
        f"8k, lifelike, realistic skin and material texture, shallow depth of field"
    )
    return caption, image_prompt, headline


def add_text_overlay(image_path, headline, source=""):
    """
    Add a professional text overlay to the image using Pillow.
    - Dark gradient band across the bottom for readability
    - Centered bold white headline text
    - 'AI Academy' branding top-left
    - Source credit bottom-right
    """
    try:
        img = Image.open(image_path).convert("RGBA")
        w, h = img.size

        # --- Build dark gradient overlay ---
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)

        # Top branding bar (subtle semi-transparent)
        draw_ov.rectangle([(0, 0), (w, 58)], fill=(0, 0, 0, 120))

        # Bottom gradient band (bottom 30% of image) — lighter, softer fade
        band_top = int(h * 0.70)
        for y in range(band_top, h):
            progress = (y - band_top) / (h - band_top)
            alpha = int(80 + 100 * progress)  # ramps from 80 → 180 (much lighter!)
            draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        # Composite overlay onto image
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        # --- Load fonts ---
        font_headline = None
        font_brand = None
        font_source = None

        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        chosen_font_path = None
        for fp in font_candidates:
            if os.path.exists(fp):
                chosen_font_path = fp
                font_brand   = ImageFont.truetype(fp, size=28)
                font_source  = ImageFont.truetype(fp, size=22)
                print(f"  Using font: {fp}")
                break

        if not chosen_font_path:
            print("  ⚠️ No TTF font found, using PIL default (small)")
            font_headline = ImageFont.load_default()
            font_brand    = font_headline
            font_source   = font_headline
            lines = textwrap.wrap(headline, width=40)[:4]
        else:
            # --- Auto-fit font size so headline never overflows ---
            PADDING = 60          # px left+right safe zone
            MAX_W   = w - PADDING * 2
            MAX_LINES = 4

            # Start large, shrink until all lines fit within MAX_W
            for font_size in range(52, 22, -2):
                font_headline = ImageFont.truetype(chosen_font_path, size=font_size)
                # Wrap generously first, then check pixel width
                test_lines = textwrap.wrap(headline, width=60)[:MAX_LINES]
                too_wide = any(
                    draw.textbbox((0, 0), ln, font=font_headline)[2] > MAX_W
                    for ln in test_lines
                )
                if not too_wide:
                    lines = test_lines
                    print(f"  Font size chosen: {font_size}px ({len(lines)} lines)")
                    break
            else:
                # Absolute fallback — tiny font, just make it fit
                font_headline = ImageFont.truetype(chosen_font_path, size=24)
                lines = textwrap.wrap(headline, width=60)[:MAX_LINES]

        # --- Brand bar top-left ---
        draw.text((22, 14), "⚡ AI Academy @ ranksorcery.com", font=font_brand, fill=(255, 255, 255, 255))

        # --- Headline text (centered, wrapped, bottom area) ---
        # Measure total block height
        line_h = font_headline.getbbox("Ag")[3] + 12
        total_text_h = line_h * len(lines)
        y_start = int(h * 0.72) + max(0, (int(h * 0.20) - total_text_h) // 2)

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font_headline)
            text_w = bbox[2] - bbox[0]
            x = (w - text_w) // 2
            # Drop shadow
            draw.text((x + 3, y_start + 3), line, font=font_headline, fill=(0, 0, 0, 200))
            # Main text (white)
            draw.text((x, y_start), line, font=font_headline, fill=(255, 255, 255, 255))
            y_start += line_h

        # --- Source credit bottom-right ---
        if source:
            source_text = f"via {source}"
            bbox = draw.textbbox((0, 0), source_text, font=font_source)
            sw = bbox[2] - bbox[0]
            draw.text((w - sw - 20, h - 34), source_text, font=font_source, fill=(160, 210, 255, 230))

        # --- Save as JPEG ---
        output_path = image_path.replace(".jpg", "_overlay.jpg")
        img.convert("RGB").save(output_path, "JPEG", quality=93)
        print(f"  ✅ Overlay saved: {output_path}")
        return output_path

    except Exception as e:
        print(f"  ⚠️ Overlay error: {e}")
        return image_path


def generate_image(prompt):
    """
    Generate a high-quality image using Hugging Face FLUX.1-dev Inference API.
    - Retries automatically if the model is still loading (cold start).
    - Downloads the raw image bytes, then center-crops to 1200x630 for Facebook.
    """
    print("🎨 Generating image with Hugging Face FLUX.1-dev...")
    TARGET_W, TARGET_H = 1200, 630

    quality_boost = (
        ", wide horizontal cinematic banner composition, balanced framing with negative space, "
        "rule of thirds, environmental shot (not an extreme close-up), correct natural proportions, "
        "award-winning photojournalism, RAW photo, real photograph, natural realistic colors, "
        "physically accurate lighting, fine detail, high dynamic range"
    )
    full_prompt = f"{prompt}{quality_boost}"

    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json",
        "x-wait-for-model": "true"   # Ask HF to wait for cold-start instead of returning 503
    }
    payload = {
        "inputs": full_prompt,
        "parameters": {
            "width": 1344,           # 16:9-ish; closest supported size to 1200x630
            "height": 768,
            "num_inference_steps": 28,
            "guidance_scale": 3.5
        }
    }

    MAX_RETRIES = 6
    RETRY_DELAY = 30   # seconds to wait between retries on model-loading / rate-limit

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  Attempt {attempt}/{MAX_RETRIES} — calling HF Inference API...")
        try:
            resp = requests.post(
                HF_MODEL_URL,
                headers=headers,
                json=payload,
                timeout=180   # FLUX.1-dev can take up to ~2 min on cold start
            )

            # ── Model still loading ──────────────────────────────────────────
            if resp.status_code == 503:
                try:
                    err_body = resp.json()
                    estimated = err_body.get("estimated_time", RETRY_DELAY)
                except Exception:
                    estimated = RETRY_DELAY
                wait = max(int(estimated) + 5, RETRY_DELAY)
                print(f"  ⏳ Model is loading on HF servers (estimated {estimated:.0f}s). "
                      f"Waiting {wait}s before retry...")
                time.sleep(wait)
                continue

            # ── Rate limited ─────────────────────────────────────────────────
            if resp.status_code == 429:
                print(f"  ⚠️  Rate limited by HF. Waiting {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
                continue

            # ── Other HTTP error ─────────────────────────────────────────────
            if not resp.ok:
                print(f"  ❌ HF API error {resp.status_code}: {resp.text[:300]}")
                if attempt < MAX_RETRIES:
                    print(f"  Retrying in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                    continue
                else:
                    return None

            # ── Success — response body is raw image bytes ───────────────────
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type and len(resp.content) < 1000:
                # Probably got a JSON error even with 200
                print(f"  ⚠️ Unexpected response (Content-Type: {content_type}): {resp.text[:200]}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                return None

            print(f"  ✅ Image received! ({len(resp.content):,} bytes)")

            # Save raw image
            raw_path = "/tmp/post_image_raw.jpg"
            with open(raw_path, "wb") as f:
                f.write(resp.content)

            # Center-crop to exact 1200x630 (cover-fit, no stretch)
            path = "/tmp/post_image.jpg"
            try:
                img = Image.open(raw_path).convert("RGB")
                src_w, src_h = img.size
                print(f"  Raw image size: {src_w}x{src_h}")
                scale = max(TARGET_W / src_w, TARGET_H / src_h)
                new_w = int(src_w * scale + 0.5)
                new_h = int(src_h * scale + 0.5)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                left = (new_w - TARGET_W) // 2
                top  = (new_h - TARGET_H) // 2
                img = img.crop((left, top, left + TARGET_W, top + TARGET_H))
                img.save(path, "JPEG", quality=92)
                print(f"  ✅ Cropped cleanly to {TARGET_W}x{TARGET_H}")
            except Exception as crop_err:
                print(f"  ⚠️ Crop failed ({crop_err}); using raw image")
                path = raw_path

            return path

        except requests.exceptions.Timeout:
            print(f"  ⏱️  Request timed out (attempt {attempt}/{MAX_RETRIES}).")
            if attempt < MAX_RETRIES:
                print(f"  Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"  ❌ Unexpected error: {e}")
            if attempt < MAX_RETRIES:
                print(f"  Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

    print("  ❌ All HF retries exhausted. Image generation failed.")
    return None


def post_to_facebook_with_image(message, image_path):
    """Post image + caption to Facebook page."""
    print(f"⏳ Waiting {FB_POST_DELAY}s before posting to Facebook (giving HF time to breathe)...")
    time.sleep(FB_POST_DELAY)

    print("📤 Posting to Facebook with image...")
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/photos"
    with open(image_path, "rb") as img:
        response = requests.post(
            url,
            data={"caption": message, "access_token": ACCESS_TOKEN},
            files={"source": img}
        )
    result = response.json()
    if "id" in result:
        print(f"✅ SUCCESS with image! Post ID: {result['id']}")
        return True
    else:
        print(f"❌ ERROR posting with image: {result}")
        raise Exception(f"Post with image failed: {result}")


def post_to_facebook_text_only(message):
    """Fallback: post text only."""
    print(f"⏳ Waiting {FB_POST_DELAY}s before posting to Facebook...")
    time.sleep(FB_POST_DELAY)

    print("📤 Posting to Facebook (text only fallback)...")
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
    payload = {"message": message, "access_token": ACCESS_TOKEN}
    response = requests.post(url, data=payload)
    result = response.json()
    if "id" in result:
        print(f"✅ SUCCESS text-only! Post ID: {result['id']}")
        return True
    else:
        print(f"❌ ERROR: {result}")
        raise Exception(f"Post failed: {result}")


if __name__ == "__main__":
    print(f"🚀 Starting AI News Poster — {datetime.now()}")
    posted = load_posted()

    articles = fetch_google_news()
    fresh = [a for a in articles if a["url"] and a["url"] not in posted]
    print(f"Fresh articles: {len(fresh)}")

    if not fresh:
        print("All articles already posted, picking latest anyway...")
        fresh = [a for a in articles if a["url"]]

    if not fresh:
        raise Exception("No articles found from Google News!")

    article = fresh[0]
    print(f"\n📰 Selected article: {article['title']}")
    print(f"🔗 URL: {article['url']}")
    print(f"📡 Source: {article['source']}")

    # Fetch actual article content for better Groq context
    article_text = fetch_article_content(article["url"])
    if not article_text:
        article_text = article.get("description", "")

    # Groq generates the caption, image prompt, AND overlay headline
    caption, image_prompt, headline = groq_generate_caption_and_prompt(
        article["title"], article_text, source=article["source"]
    )

    print(f"\n📰 Headline for overlay: {headline}")
    print(f"\n📝 Caption preview:\n{caption[:300]}...\n")
    print(f"🎨 Image prompt: {image_prompt}\n")

    # Generate the base image via Hugging Face FLUX.1-dev
    image_path = generate_image(image_prompt)

    if image_path:
        # Add text overlay (headline + branding + source)
        image_path = add_text_overlay(image_path, headline, source=article["source"])
        post_to_facebook_with_image(caption, image_path)
    else:
        print("⚠️  Image generation failed — falling back to text-only post.")
        post_to_facebook_text_only(caption)

    posted.append(article["url"])
    save_posted(posted)
    print("\n🎉 Done!")
