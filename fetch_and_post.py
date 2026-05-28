import requests
import os
import json
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime

PAGE_ID = os.environ["FB_PAGE_ID"]
ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]

RSS_FEEDS = [
    "https://www.artificialintelligence-news.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.marktechpost.com/feed/",
]

POSTED_FILE = "posted_urls.json"


def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return json.load(f)
    return []


def save_posted(posted):
    with open(POSTED_FILE, "w") as f:
        json.dump(posted[-200:], f, indent=2)


def fetch_articles(feed_url):
    articles = []
    try:
        resp = requests.get(feed_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        if "atom" in root.tag.lower():
            ns = "{http://www.w3.org/2005/Atom}"
            for entry in root.findall(f"{ns}entry"):
                title_el = entry.find(f"{ns}title")
                summary_el = entry.find(f"{ns}summary")
                link_el = entry.find(f"{ns}link")
                if title_el is not None and link_el is not None:
                    articles.append({
                        "title": (title_el.text or "").strip(),
                        "summary": (summary_el.text or "").strip() if summary_el is not None else "",
                        "url": link_el.get("href", "").strip(),
                    })
        else:
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item"):
                    title_el = item.find("title")
                    link_el = item.find("link")
                    desc_el = item.find("description")
                    if title_el is not None and link_el is not None:
                        articles.append({
                            "title": (title_el.text or "").strip(),
                            "summary": (desc_el.text or "").strip() if desc_el is not None else "",
                            "url": (link_el.text or "").strip(),
                        })

    except Exception as e:
        print(f"  Feed error ({feed_url}): {e}")

    return articles


def clean_html(text):
    import re
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"http\S+", "", text)
    text = text.strip()
    return text


def make_caption(article):
    today = datetime.now().strftime("%B %d, %Y")
    title = clean_html(article["title"])
    summary = clean_html(article.get("summary", ""))

    if summary and len(summary) > 200:
        summary = summary[:200].rsplit(" ", 1)[0] + "..."

    summary_block = f"\n{summary}\n" if summary else ""

    caption = f"""🤖 AI AUTOMATION UPDATE — {today}

🔥 {title}
{summary_block}
What do you think about this? Drop your thoughts below! 👇

💡 Follow this page for daily AI & automation insights!

#AIAutomation #ArtificialIntelligence #AINews #Automation #TechNews #MachineLearning #FutureOfWork #AIDaily #GenerativeAI #AITrends"""

    return caption


def generate_image(title):
    """Generate an AI image using Pollinations.ai — free, no API key needed!"""
    print("  Generating image with Pollinations.ai...")

    prompt = (
        f"futuristic AI technology illustration inspired by: {title}, "
        "digital art, vibrant neon colors, modern tech aesthetic, "
        "clean professional design, no text, widescreen"
    )
    encoded_prompt = urllib.parse.quote(prompt)
    seed = abs(hash(title)) % 99999

    image_url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=1200&height=630&nologo=true&seed={seed}"
    )

    try:
        resp = requests.get(image_url, timeout=60)
        resp.raise_for_status()
        print(f"  Image generated! ({len(resp.content) // 1024} KB)")
        return resp.content
    except Exception as e:
        print(f"  Image generation failed: {e}")
        return None


def post_to_facebook(caption, image_data=None):
    if image_data:
        # Post with image using /photos endpoint
        print("  Posting with image...")
        url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/photos"
        payload = {
            "caption": caption,
            "access_token": ACCESS_TOKEN,
        }
        files = {"source": ("image.jpg", image_data, "image/jpeg")}
        response = requests.post(url, data=payload, files=files)
    else:
        # Fallback: text-only post
        print("  Posting text only (no image)...")
        url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
        payload = {
            "message": caption,
            "access_token": ACCESS_TOKEN,
        }
        response = requests.post(url, data=payload)

    result = response.json()

    if "id" in result:
        print(f"SUCCESS! Post ID: {result['id']}")
        return True
    else:
        print(f"ERROR: {result}")
        raise Exception(f"Post failed: {result}")


if __name__ == "__main__":
    print(f"Starting AI News Poster — {datetime.now()}")
    posted = load_posted()

    all_articles = []
    for feed in RSS_FEEDS:
        print(f"Fetching: {feed}")
        articles = fetch_articles(feed)
        all_articles.extend(articles)
        print(f"  Got {len(articles)} articles")

    fresh = [a for a in all_articles if a["url"] and a["url"] not in posted]
    print(f"Fresh articles: {len(fresh)}")

    if not fresh:
        fresh = [a for a in all_articles if a["url"]]

    if not fresh:
        raise Exception("No articles found from any feed!")

    article = fresh[0]
    print(f"Posting: {article['title']}")

    caption = make_caption(article)
    image_data = generate_image(article["title"])
    post_to_facebook(caption, image_data)

    posted.append(article["url"])
    save_posted(posted)
    print("Done!")
