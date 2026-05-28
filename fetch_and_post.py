import requests
import os
import json
import xml.etree.ElementTree as ET
from datetime import datetime
import urllib.parse

PAGE_ID = os.environ["FB_PAGE_ID"]
ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

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


def generate_image_prompt(title, summary):
    """Use Groq to generate a highly relevant and specific image prompt."""
    print("Generating smart image prompt with Groq...")
    try:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        body = {
            "model": "llama3-8b-8192",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert at writing Stable Diffusion image prompts. "
                        "Given a news article title and summary, write a vivid, specific, photorealistic image prompt "
                        "that visually represents the topic. Keep it under 60 words. "
                        "No text, no logos, no words in image. Focus on the scene, objects, mood. "
                        "Always end with: cinematic lighting, 4k, photorealistic, detailed"
                    )
                },
                {
                    "role": "user",
                    "content": f"Article title: {title}\nSummary: {summary[:300]}\n\nWrite a detailed image prompt:"
                }
            ],
            "max_tokens": 120,
            "temperature": 0.7
        }
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=body, timeout=20
        )
        resp.raise_for_status()
        prompt = resp.json()["choices"][0]["message"]["content"].strip()
        # Remove any surrounding quotes if present
        prompt = prompt.strip('"').strip("'")
        print(f"  Groq prompt: {prompt}")
        return prompt
    except Exception as e:
        print(f"  Groq error: {e}")
        return f"futuristic artificial intelligence technology concept, digital brain neural network, cinematic lighting, 4k, photorealistic, detailed"


def generate_image(prompt):
    """Use Pollinations.ai to generate image — free, reliable, no API key needed."""
    print("Generating image with Pollinations.ai...")
    try:
        encoded_prompt = urllib.parse.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1200&height=630&nologo=true&seed={int(datetime.now().timestamp())}"
        print(f"  Fetching image from: {image_url[:80]}...")
        resp = requests.get(image_url, timeout=60)
        resp.raise_for_status()

        # Make sure we got an actual image
        if resp.headers.get("content-type", "").startswith("image"):
            with open("/tmp/post_image.jpg", "wb") as f:
                f.write(resp.content)
            print(f"  Image saved! ({len(resp.content)} bytes)")
            return "/tmp/post_image.jpg"
        else:
            print(f"  Not an image response: {resp.headers.get('content-type')}")
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

    # Step 1: Groq generates a smart, specific image prompt from the article
    image_prompt = generate_image_prompt(article["title"], article.get("summary", ""))

    # Step 2: Pollinations.ai generates the actual image (free, reliable!)
    image_path = generate_image(image_prompt)

    # Step 3: Post to Facebook
    if image_path:
        post_to_facebook_with_image(caption, image_path)
    else:
        print("Image generation failed, falling back to text-only post.")
        post_to_facebook_text_only(caption)

    posted.append(article["url"])
    save_posted(posted)
    print("Done!")
