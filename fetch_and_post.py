import requests
import os
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import urllib.parse

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
                    # Google News titles include source like "Title - Source"
                    # Remove the " - Source" part at the end
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
        # Extract visible text roughly
        html = resp.text
        # Remove scripts/styles
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
        # Get paragraph text
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL)
        text = " ".join(clean_html(p) for p in paragraphs)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 100:
            print(f"  Fetched article content: {len(text)} chars")
            return text[:1500]
    except Exception as e:
        print(f"  Could not fetch article content: {e}")
    return ""


def parse_groq_json(raw: str) -> dict | None:
    """
    Robustly extract a JSON object from Groq's response.
    Tries three strategies in order:
      1. Direct parse (model returned clean JSON).
      2. Extract the first {...} block (model wrapped text around it).
      3. Strip ```json ... ``` fences then parse.
    Returns the parsed dict or None on failure.
    """
    # Strategy 1 – clean JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2 – first {...} blob (handles preamble/postamble text)
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
    """Use Groq to generate both a Facebook caption AND a specific image prompt."""
    print("Asking Groq to write caption and image prompt...")
    today = datetime.now().strftime("%B %d, %Y")

    content_for_groq = article_text[:800] if article_text else f"Article about: {title}"
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
                        "You are a social media manager for an AI education Facebook page called 'AI Academy'. "
                        "Given an AI news article, you will output a JSON object with exactly two keys:\n"
                        "1. 'caption' - an engaging Facebook post caption. Include: "
                        "a relevant emoji at the start, the key insight from the article in 2-3 sentences, "
                        "one thought-provoking question for followers, "
                        f"the source credit '{source_credit}' on its own line, "
                        "'Follow AI Academy for daily AI insights!', "
                        "and 5-8 relevant hashtags. Keep it under 300 words.\n"
                        "2. 'image_prompt' - a highly specific Stable Diffusion image prompt that visually represents "
                        "this specific article topic. Be very specific about objects, scene, style. "
                        "NO text, NO logos, NO words in the image. Under 60 words. "
                        "End with: photorealistic, cinematic lighting, 4k, highly detailed.\n"
                        "IMPORTANT: Output ONLY a valid JSON object — no markdown fences, no preamble, no postamble. "
                        "Start your response with '{' and end with '}'."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Date: {today}\n"
                        f"Article title: {title}\n"
                        f"Source: {source}\n"
                        f"Article content: {content_for_groq}\n\n"
                        "Generate the Facebook caption and image prompt as JSON."
                    )
                }
            ],
            "max_tokens": 600,
            "temperature": 0.75
        }
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=body, timeout=30
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"  Groq raw response: {raw[:200]}...")

        result = parse_groq_json(raw)
        if result:
            caption = result.get("caption", "")
            image_prompt = result.get("image_prompt", "")
            if caption and image_prompt:
                print(f"  ✅ JSON parsed successfully!")
                print(f"  Caption length: {len(caption)} chars")
                print(f"  Image prompt: {image_prompt[:100]}...")
                return caption, image_prompt
            else:
                print(f"  ⚠️ JSON parsed but missing keys. Keys found: {list(result.keys())}")
        else:
            print(f"  ❌ All JSON parse strategies failed. Raw:\n{raw[:500]}")

    except Exception as e:
        print(f"  Groq error: {e}")

    # Fallback
    print("  Using fallback caption and prompt")
    source_line = f"\n📰 {source_credit}" if source_credit else ""
    caption = (
        f"🤖 AI AUTOMATION UPDATE — {today}\n\n"
        f"🔥 {title}\n\n"
        f"What do you think about this? Drop your thoughts below! 👇\n"
        f"{source_line}\n\n"
        f"💡 Follow AI Academy for daily AI & automation insights!\n\n"
        f"#AIAutomation #ArtificialIntelligence #AINews #MachineLearning #AIDaily"
    )
    image_prompt = f"{title}, futuristic technology concept, cinematic lighting, 4k, photorealistic, highly detailed"
    return caption, image_prompt


def generate_image(prompt):
    """Use Pollinations.ai to generate image — free, reliable, no API key needed."""
    print("Generating image with Pollinations.ai...")
    try:
        # Make the prompt more specific and safe for Pollinations
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
            with open("/tmp/post_image.jpg", "wb") as f:
                f.write(resp.content)
            print(f"  Image saved! ({len(resp.content)} bytes)")
            return "/tmp/post_image.jpg"
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

    # Fetch trending AI news from Google News
    articles = fetch_google_news()

    # Filter out already-posted
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

    # Groq generates BOTH the caption and the specific image prompt
    caption, image_prompt = groq_generate_caption_and_prompt(
        article["title"], article_text, source=article["source"]
    )

    print(f"\nCaption preview:\n{caption[:200]}...\n")
    print(f"Image prompt: {image_prompt}\n")

    # Generate image using the specific prompt
    image_path = generate_image(image_prompt)

    # Post to Facebook
    if image_path:
        post_to_facebook_with_image(caption, image_path)
    else:
        print("Image generation failed, posting text only.")
        post_to_facebook_text_only(caption)

    posted.append(article["url"])
    save_posted(posted)
    print("\nDone! 🎉")
