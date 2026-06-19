import os
import json
import requests
import textwrap
import subprocess
from PIL import Image, ImageDraw, ImageFont

FB_PAGE_ID = os.environ.get('FB_PAGE_ID')
FB_ACCESS_TOKEN = os.environ.get('FB_ACCESS_TOKEN')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')

def add_text_to_image(image_path, title, source_name):
    try:
        img = Image.open(image_path).convert("RGBA")

        # Resize to vertical 9:16 ratio for Reels
        target_width = 1080
        target_height = 1920
        img = img.resize((target_width, target_height), Image.LANCZOS)
        print(f"Image resized to {target_width}x{target_height} for Reels format.")

        overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = int(target_width / 18)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        try:
            font_large = ImageFont.truetype(font_path, font_size)
            font_small = ImageFont.truetype(font_path, int(font_size / 2))
        except:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Top Banner - News Title
        wrapped_title = textwrap.fill(title, width=30)
        bbox_title = draw.textbbox((0, 0), wrapped_title, font=font_large)
        title_h = bbox_title[3] - bbox_title[1]
        top_bar_height = title_h + 60
        draw.rectangle([(0, 0), (target_width, top_bar_height)], fill=(0, 0, 0, 170))
        draw.text((30, 30), wrapped_title, font=font_large, fill=(255, 255, 255, 230))

        # Bottom Banner - Source Name
        source_text = f"via {source_name} | #AI #ArtificialIntelligence"
        bbox_source = draw.textbbox((0, 0), source_text, font=font_small)
        source_h = bbox_source[3] - bbox_source[1]
        bottom_bar_height = source_h + 40
        draw.rectangle([(0, target_height - bottom_bar_height), (target_width, target_height)], fill=(0, 0, 0, 170))
        draw.text((30, target_height - bottom_bar_height + 20), source_text, font=font_small, fill=(255, 255, 255, 230))

        img = Image.alpha_composite(img, overlay)
        img.convert("RGB").save(image_path, "JPEG", quality=90)
        print("Successfully added text overlay to image.")
        return True
    except Exception as e:
        print(f"Failed to add text to image: {e}")
        return False

def convert_image_to_video(image_path, video_path, duration=10):
    """Convert image to 10-second video using ffmpeg - required for Reels"""
    try:
        cmd = [
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', image_path,
            '-c:v', 'libx264',
            '-t', str(duration),
            '-pix_fmt', 'yuv420p',
            '-vf', 'scale=1080:1920',
            '-r', '30',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Video created successfully: {video_path}")
            return True
        else:
            print(f"ffmpeg error: {result.stderr}")
            return False
    except Exception as e:
        print(f"Failed to convert image to video: {e}")
        return False

def upload_reel_to_facebook(video_path, message):
    """Upload video as a Reel using Facebook Resumable Upload API"""

    video_size = os.path.getsize(video_path)
    print(f"Video size: {video_size} bytes")

    # STEP 1: Initialize upload session
    print("Initializing Reel upload session...")
    init_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/video_reels'
    init_payload = {
        'upload_phase': 'start',
        'access_token': FB_ACCESS_TOKEN
    }
    init_response = requests.post(init_url, data=init_payload)
    
    if init_response.status_code != 200:
        print(f"Failed to initialize upload: {init_response.text}")
        return False

    video_id = init_response.json().get('video_id')
    print(f"Upload session started. Video ID: {video_id}")

    # STEP 2: Upload the video binary
    print("Uploading video binary...")
    upload_url = f'https://rupload.facebook.com/video-upload/v19.0/{video_id}'
    headers = {
        'Authorization': f'OAuth {FB_ACCESS_TOKEN}',
        'offset': '0',
        'file_size': str(video_size),
        'Content-Type': 'application/octet-stream'
    }
    with open(video_path, 'rb') as video_file:
        upload_response = requests.post(upload_url, headers=headers, data=video_file)

    if upload_response.status_code != 200:
        print(f"Failed to upload video: {upload_response.text}")
        return False

    print("Video binary uploaded successfully!")

    # STEP 3: Publish the Reel
    print("Publishing Reel to Facebook...")
    publish_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/video_reels'
    publish_payload = {
        'video_id': video_id,
        'upload_phase': 'finish',
        'video_state': 'PUBLISHED',
        'description': message,
        'access_token': FB_ACCESS_TOKEN
    }
    publish_response = requests.post(publish_url, data=publish_payload)

    if publish_response.status_code == 200:
        print(f"Reel published successfully! Response: {publish_response.json()}")
        return True
    else:
        print(f"Failed to publish Reel: {publish_response.text}")
        return False

def main():
    # 1. Fetch News
    print("Fetching news...")
    news_url = f'https://newsapi.org/v2/everything?q=artificial+intelligence&sortBy=publishedAt&pageSize=1&apiKey={NEWS_API_KEY}'
    response = requests.get(news_url)
    data = response.json()

    if data.get('status') != 'ok' or not data.get('articles'):
        print('Failed to fetch news or no articles found.')
        exit(1)

    article = data['articles'][0]
    title = article['title']
    source_url = article['url']

    raw_source_name = article.get('source', {}).get('name', 'Unknown Source')
    source_name = os.path.splitext(raw_source_name)[0]

    description = article.get('description', 'No description available.')
    api_image_url = article.get('urlToImage', '')
    print(f'Fetched Article: {title} from {source_name}')

    # 2. Download Image
    image_path = '/tmp/news_image.jpg'
    video_path = '/tmp/news_reel.mp4'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    download_success = False

    if api_image_url:
        try:
            img_response = requests.get(api_image_url, headers=headers, timeout=10)
            content_type = img_response.headers.get('Content-Type', '')
            if img_response.status_code == 200 and 'image' in content_type:
                with open(image_path, 'wb') as f:
                    f.write(img_response.content)
                download_success = True
                print('Image successfully downloaded.')
            else:
                print(f'Failed to download image. Status: {img_response.status_code}')
        except Exception as e:
            print(f'Error downloading image: {e}')

    # 3. Add Text to Image
    if download_success:
        add_text_to_image(image_path, title, source_name)

    # 4. Convert Image to Video for Reel
    message = f'🤖 {title}\n\n{description}\n\nvia {source_name}\n\n#AI #ArtificialIntelligence #TechNews'

    if download_success:
        video_created = convert_image_to_video(image_path, video_path, duration=10)
        if video_created:
            # 5. Upload as Reel
            success = upload_reel_to_facebook(video_path, message)
            if not success:
                # Fallback to regular feed post with link
                print("Reel upload failed, falling back to text post...")
                fb_api_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed'
                payload = {'message': message, 'link': source_url, 'access_token': FB_ACCESS_TOKEN}
                fb_response = requests.post(fb_api_url, data=payload)
                if fb_response.status_code == 200:
                    print('Fallback post successful!')
                else:
                    print(f'Fallback also failed: {fb_response.text}')
                    exit(1)
        else:
            print("Video conversion failed.")
            exit(1)
    else:
        # No image - post text + link to feed
        print('No image found. Posting text and link to Facebook...')
        fb_api_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed'
        payload = {'message': message, 'link': source_url, 'access_token': FB_ACCESS_TOKEN}
        fb_response = requests.post(fb_api_url, data=payload)
        if fb_response.status_code == 200:
            print('Successfully posted to Facebook!')
        else:
            print(f'Failed to post: {fb_response.text}')
            exit(1)

if __name__ == '__main__':
    main()
