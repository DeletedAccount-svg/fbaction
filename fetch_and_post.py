import requests
import os
import json
import re
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
                        "Your writing is vivid, enthusiastic, and uses storytelling to pull readers in.\n\n"
                        "Given an AI news article, output a JSON object with exactly THREE keys:\n\n"

                        "1. 'headline' — A short, punchy, ALL-CAPS headline (8–12 words max) that will be printed "
                        "on the post image. Make it dramatic and attention-grabbing, like a newspaper front page. "
                        "No hashtags, no emojis. Example: 'AI NOW READS YOUR EMOTIONS BETTER THAN HUMANS'\n\n"

                        "2. 'caption' — A rich, engaging Facebook post. Structure it like this:\n"
                        "   - Line 1: A bold emoji + a dramatic one-liner hook that stops the scroll.\n"
                        "   - Blank line\n"
                        "   - 4–5 sentences of detailed storytelling: explain WHAT happened, WHY it matters, "
                        "HOW it changes things, and WHAT the real-world impact is. Paint a vivid picture. "
                        "Each sentence should be full, detailed, and informative — minimum 20 words per sentence.\n"
                        "   - Blank line\n"
                        "   - A thought-provoking question that personally involves the reader.\n"
                        "   - Blank line\n"
                        f"   - '📰 {source_credit}' on its own line (source credit).\n"
                        "   - '💡 Follow AI Academy @ ranksorcery.com for daily AI insights!' on its own line.\n"
                        "   - Blank line\n"
                        "   - A 3–4 sentence CTA block: Tell readers that this entire post — the news, the image, "
                        "the caption, the hashtags — was created and published AUTOMATICALLY by AI with zero manual effort. "
                        "Build curiosity and excitement about automation. End with exactly these lines: "
                        "'Imagine having a system like this running your page 24/7 — delivering consistent, quality content every single day without lifting a finger. "
                        "💬 Want to automate your Facebook page just like this? Type HOW in the comments and we\'ll show you how it\'s done!'\n"
                        "   - 6–8 relevant hashtags on the last line.\n"
                        "   Total length: 280–380 words.\n\n"

                        "3. 'image_prompt' — A vivid, cinematic Stable Diffusion prompt that visually represents "
                        "the article topic. Describe specific objects, lighting, environment, and mood. "
                        "Absolutely NO text, letters, numbers, logos, or watermarks in the scene. "
                        "Under 75 words. End with: photorealistic, cinematic lighting, 4k ultra HD, highly detailed.\n\n"

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
    caption = (
        f"🤖 THE FUTURE OF AI IS HERE — {today}\n\n"
        f"Artificial intelligence is reshaping the world as we know it, and this latest development is a "
        f"clear sign of just how fast the technology is evolving. Researchers and engineers are pushing the "
        f"boundaries of what machines can do, bringing us closer to a future where AI assists in nearly "
        f"every aspect of human life. This breakthrough has significant implications for industries ranging "
        f"from healthcare and education to finance and creative arts, touching the lives of billions of "
        f"people worldwide. As these systems grow smarter and more capable, the conversation around ethical "
        f"AI use, data privacy, and human-AI collaboration becomes more important than ever before.\n\n"
        f"🔥 {title}\n\n"
        f"How do you think this development will change your daily life or your industry?\n"
        f"{source_line}\n\n"
        f"💡 Follow AI Academy @ ranksorcery.com for daily AI insights!\n\n"
        f"🤖 Here's something wild — this entire post was created and published automatically by AI. "
        f"No human wrote this caption, picked this image, or hit the post button. "
        f"Every single element — the news, the AI-generated visual, the caption, and the hashtags — "
        f"was handled end-to-end by an automated system running quietly in the background. "
        f"Imagine having a system like this running your page 24/7 — delivering consistent, quality content every single day without lifting a finger. "
        f"💬 Want to automate your Facebook page just like this? Type HOW in the comments and we'll show you how it's done!\n\n"
        f"#AIAutomation #ArtificialIntelligence #AINews #MachineLearning #FutureOfAI #AIDaily #TechNews"
    )
    headline = title[:60].upper()
    image_prompt = (
        f"{title}, futuristic technology concept, glowing neural networks, "
        f"cinematic lighting, 4k ultra HD, photorealistic, highly detailed"
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

        # Top branding bar (semi-transparent)
        draw_ov.rectangle([(0, 0), (w, 58)], fill=(0, 0, 0, 170))

        # Bottom gradient band (bottom 42% of image)
        band_top = int(h * 0.58)
        for y in range(band_top, h):
            progress = (y - band_top) / (h - band_top)
            alpha = int(180 + 65 * progress)  # ramps from 180 → 245
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
        for fp in font_candidates:
            if os.path.exists(fp):
                font_headline = ImageFont.truetype(fp, size=58)
                font_brand   = ImageFont.truetype(fp, size=28)
                font_source  = ImageFont.truetype(fp, size=22)
                print(f"  Using font: {fp}")
                break

        if not font_headline:
            print("  ⚠️ No TTF font found, using PIL default (small)")
            font_headline = ImageFont.load_default()
            font_brand    = font_headline
            font_source   = font_headline

        # --- Brand bar top-left ---
        draw.text((22, 14), "⚡ AI Academy @ ranksorcery.com", font=font_brand, fill=(255, 255, 255, 255))

        # --- Headline text (centered, wrapped, bottom area) ---
        max_chars_per_line = max(12, int(w / 36))
        lines = textwrap.wrap(headline, width=max_chars_per_line)[:4]  # max 4 lines

        # Measure total block height
        line_h = font_headline.getbbox("Ag")[3] + 10
        total_text_h = line_h * len(lines)
        y_start = int(h * 0.60) + max(0, (int(h * 0.32) - total_text_h) // 2)

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
    """Use Pollinations.ai to generate image — free, reliable, no API key needed."""
    print("Generating image with Pollinations.ai...")
    try:
        full_prompt = f"{prompt} --no text, letters, words, logos, watermarks"
        encoded_prompt = urllib.parse.quote(full_prompt)
        seed = int(datetime.now().timestamp()) % 99999
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1200&height=630&nologo=true&seed={seed}&model=flux"
        )
        print(f"  Image URL: {image_url[:100]}...")
        resp = requests.get(image_url, timeout=90)
        resp.raise_for_status()

        if resp.headers.get("content-type", "").startswith("image"):
            path = "/tmp/post_image.jpg"
            with open(path, "wb") as f:
                f.write(resp.content)
            print(f"  Image saved! ({len(resp.content)} bytes)")
            return path
        else:
            print(f"  Not an image: {resp.headers.get('content-type')}")
            return None
    except Exception as e:
        print(f"  Pollinations error: {e}")
        return None


def post_to_facebook_with_image(message, image_path):
    """Post image + caption to Facebook page."""
    print("Posting to Facebook with image...")
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/photos"
    with open(image_path, "rb") as img:
        response = requests.post(
            url,
            data={"caption": message, "access_token": ACCESS_TOKEN},
            files={"source": img}
        )
    result = response.json()
    if "id" in result:
        print(f"SUCCESS with image! Post ID: {result['id']}")
        return True
    else:
        print(f"ERROR posting with image: {result}")
        raise Exception(f"Post with image failed: {result}")


def post_to_facebook_text_only(message):
    """Fallback: post text only."""
    print("Posting to Facebook (text only fallback)...")
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
    payload = {"message": message, "access_token": ACCESS_TOKEN}
    response = requests.post(url, data=payload)
    result = response.json()
    if "id" in result:
        print(f"SUCCESS text-only! Post ID: {result['id']}")
        return True
    else:
        print(f"ERROR: {result}")
        raise Exception(f"Post failed: {result}")


if __name__ == "__main__":
    print(f"Starting AI News Poster — {datetime.now()}")
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
    print(f"\nSelected article: {article['title']}")
    print(f"URL: {article['url']}")
    print(f"Source: {article['source']}")

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

    # Generate the base image
    image_path = generate_image(image_prompt)

    if image_path:
        # Add text overlay (headline + branding + source)
        image_path = add_text_overlay(image_path, headline, source=article["source"])
        post_to_facebook_with_image(caption, image_path)
    else:
        print("Image generation failed, posting text only.")
        post_to_facebook_text_only(caption)

    posted.append(article["url"])
    save_posted(posted)
    print("\nDone! 🎉")
