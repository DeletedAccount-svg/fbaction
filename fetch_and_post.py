import requests
import os
import json
import xml.etree.ElementTree as ET
from datetime import datetime

PAGE_ID = os.environ["FB_PAGE_ID"]
ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
HF_API_KEY = os.environ["HF_API_KEY"]

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
    """Use Groq to generate a highly relevant image prompt based on article."""
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
                        "No text, no logos, no words in image. Focus on the scene, objects, mood."
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
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body, timeout=20)
        resp.raise_for_status()
        prompt = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"  Groq prompt: {prompt}")
        return prompt
    except Exception as e:
        print(f"  Groq error: {e}")
        # Fallback prompt
        return f"futuristic technology concept, artificial intelligence, digital innovation, cinematic lighting, photorealistic"


def generate_image(prompt):
    """Use HuggingFace Inference API to generate image from prompt."""
    print("Generating image with HuggingFace...")
    model = "stabilityai/stable-diffusion-xl-base-1.0"
    api_url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "width": 1024,
            "height": 576,
            "num_inference_steps": 30,
            "guidance_scale": 7.5
        }
    }

    for attempt in range(3):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=120)
            if resp.status_code == 503:
                import time
                wait = 30
                print(f"  Model loading, waiting {wait}s... (attempt {attempt+1}/3)")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            image_bytes = resp.content
            with open("/tmp/post_image.jpg", "wb") as f:
                f.write(image_bytes)
            print("  Image saved!")
            return "/tmp/post_image.jpg"
        except Exception as e:
            print(f"  HuggingFace attempt {attempt+1} error: {e}")
            if attempt == 2:
                return None

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

    # Step 1: Groq generates a smart relevant image prompt
    image_prompt = generate_image_prompt(article["title"], article.get("summary", ""))

    # Step 2: HuggingFace generates the actual image
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
