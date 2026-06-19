import os
import requests
import textwrap
from PIL import Image, ImageDraw, ImageFont

FB_PAGE_ID = os.environ.get('FB_PAGE_ID')
FB_ACCESS_TOKEN = os.environ.get('FB_ACCESS_TOKEN')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')

def add_text_to_image(image_path, title, source_name):
    try:
        img = Image.open(image_path).convert("RGBA")
        
        # Resize image if it's too large to prevent FB API errors
        max_width = 1200 
        if img.width > max_width:
            new_height = int((max_width / img.width) * img.height)
            img = img.resize((max_width, new_height), Image.LANCZOS)
            print(f"Image resized to {img.width}x{img.height} to meet FB requirements.")
        
        # Create a transparent overlay layer
        overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        
        font_size = int(img.width / 25)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        
        try:
            font_large = ImageFont.truetype(font_path, font_size)
            font_small = ImageFont.truetype(font_path, int(font_size / 2.5))
        except:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # 1. Draw Top Banner (News Title)
        wrapped_title = textwrap.fill(title, width=40)
        bbox_title = draw.textbbox((0, 0), wrapped_title, font=font_large)
        title_h = bbox_title[3] - bbox_title[1]
        
        top_bar_height = title_h + 30
        draw.rectangle([(0, 0), (img.width, top_bar_height)], fill=(0, 0, 0, 120))
        draw.text((15, 15), wrapped_title, font=font_large, fill=(255, 255, 255, 230))

        # 2. Draw Bottom Banner (Source Name)
        source_text = f"via {source_name}"
        bbox_source = draw.textbbox((0, 0), source_text, font=font_small)
        source_h = bbox_source[3] - bbox_source[1]
        
        bottom_bar_height = source_h + 20
        draw.rectangle([(0, img.height - bottom_bar_height), (img.width, img.height)], fill=(0, 0, 0, 120))
        draw.text((15, img.height - bottom_bar_height + 10), source_text, font=font_small, fill=(255, 255, 255, 230))

        # Merge the transparent overlay onto the original image
        img = Image.alpha_composite(img, overlay)
        
        img.convert("RGB").save(image_path, "JPEG", quality=85)
        print("Successfully added transparent text overlay to image.")
        return True
    except Exception as e:
        print(f"Failed to add text to image: {e}")
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
    
    # Extract source name and CLEAN IT (Remove .com, .org, etc.)
    raw_source_name = article.get('source', {}).get('name', 'Unknown Source')
    source_name = os.path.splitext(raw_source_name)[0]  # Turns "Biztoc.com" into "Biztoc"
    
    description = article.get('description', 'No description available.')
    api_image_url = article.get('urlToImage', '')
    print(f'Fetched Article: {title} from {source_name}')

    # 2. Download Image
    image_path = '/tmp/news_image.jpg'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    image_url = api_image_url if api_image_url else None
    download_success = False
    
    if image_url:
        try:
            img_response = requests.get(image_url, headers=headers, timeout=10)
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

    # 4. Post to Facebook
    message = f'🤖 {title}\n\n{description}\n\nvia {source_name}\n\n#AI #ArtificialIntelligence #TechNews'

    if download_success and os.path.exists(image_path):
        # ✅ FIXED: Two-step method so image shows in Posts feed, not just Photos album

        # STEP 1: Upload image as UNPUBLISHED (silent upload, no post yet)
        print('Uploading image silently to Facebook...')
        upload_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos'
        with open(image_path, 'rb') as img_file:
            upload_payload = {
                'access_token': FB_ACCESS_TOKEN,
                'published': 'false'  # 👈 KEY FIX: Don't publish yet!
            }
            files = {'source': (os.path.basename(image_path), img_file)}
            upload_response = requests.post(upload_url, data=upload_payload, files=files)

        if upload_response.status_code != 200:
            print(f'Failed to upload image. Response: {upload_response.text}')
            exit(1)

        photo_id = upload_response.json().get('id')
        print(f'Image uploaded successfully with ID: {photo_id}')

        # STEP 2: Post to /feed with the uploaded photo attached
        # 👈 This makes it appear in the Posts feed WITH the image!
        print('Publishing post to Facebook feed with attached image...')
        fb_api_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed'
        payload = {
            'message': message,
            'attached_media': f'[{{"media_fbid":"{photo_id}"}}]',
            'access_token': FB_ACCESS_TOKEN
        }
        fb_response = requests.post(fb_api_url, data=payload)

    else:
        # No image fallback: post text + link to feed
        fb_api_url = f'https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed'
        payload = {'message': message, 'link': source_url, 'access_token': FB_ACCESS_TOKEN}
        print('No image found. Posting text and link to Facebook...')
        fb_response = requests.post(fb_api_url, data=payload)

    if fb_response.status_code == 200:
        print('Successfully posted to Facebook!')
    else:
        print(f'Failed to post to Facebook. Response: {fb_response.text}')
        exit(1)

if __name__ == '__main__':
    main()
