import os
import json
import random
import glob
import requests
import textwrap
import subprocess
from PIL import Image, ImageDraw, ImageFont

FB_PAGE_ID = os.environ.get('FB_PAGE_ID')
FB_ACCESS_TOKEN = os.environ.get('FB_ACCESS_TOKEN')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')

# Folder where royalty-free background tracks live, organized by mood/genre.
# Example layout:
#   assets/music/suspense/curse_of_old_density_time.mp3
#   assets/music/suspense/another_track.mp3
MUSIC_ROOT = os.environ.get('MUSIC_ROOT', 'assets/music')
MUSIC_GENRE = os.environ.get('MUSIC_GENRE', 'suspense')


def pick_music_track(genre=MUSIC_GENRE, music_root=MUSIC_ROOT):
    """Pick a random track from assets/music/<genre>/. Returns None if folder is empty/missing."""
    pattern = os.path.join(music_root, genre, '*.mp3')
    tracks = glob.glob(pattern)
    if not tracks:
        print(f"No music tracks found in {pattern}. Reel will be uploaded without audio.")
        return None
    chosen = random.choice(tracks)
    print(f"Selected music track: {chosen}")
    return chosen


def get_audio_duration(audio_path):
    """Use ffprobe to get the duration (in seconds) of an audio file."""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'json',
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ffprobe error reading duration: {result.stderr}")
            return None
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception as e:
        print(f"Failed to get audio duration: {e}")
        return None


def get_random_clip_start(audio_path, clip_duration):
    """Pick a random start point in the track so the 10s clip isn't always the same section."""
    total_duration = get_audio_duration(audio_path)
    if not total_duration or total_duration <= clip_duration:
        return 0
    max_start = total_duration - clip_duration
    return round(random.uniform(0, max_start), 2)


def resize_cover(img, target_width, target_height):
    """Resize an image to completely fill target_width x target_height without
    distorting it — scales proportionally to cover the frame, then center-crops
    whatever spills over the edges (like CSS background-size: cover).
    """
    img_ratio = img.width / img.height
    target_ratio = target_width / target_height

    if img_ratio > target_ratio:
        # Source is relatively wider than target -> match height, crop the sides
        new_height = target_height
        new_width = int(round(new_height * img_ratio))
    else:
        # Source is relatively taller/narrower than target -> match width, crop top/bottom
        new_width = target_width
        new_height = int(round(new_width / img_ratio))

    img = img.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - target_width) // 2
    top = (new_height - target_height) // 2
    return img.crop((left, top, left + target_width, top + target_height))


def add_text_to_image(image_path, source_name):
    """Resize image for Reels and burn in a small bottom source credit.
    The main headline is no longer baked in here — it's drawn as an animated
    overlay on the video itself (see convert_image_to_video), since a fading
    centered title looks far better on a moving clip than a flat static banner.
    """
    try:
        img = Image.open(image_path).convert("RGBA")

        # Fill the vertical 9:16 frame without stretching/distorting the photo
        target_width = 1080
        target_height = 1920
        img = resize_cover(img, target_width, target_height)
        print(f"Image cropped/filled to {target_width}x{target_height} for Reels format (no distortion).")

        overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = int(target_width / 28)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        try:
            font_small = ImageFont.truetype(font_path, font_size)
        except:
            font_small = ImageFont.load_default()

        # Bottom Banner - Source Name (kept light/static since it's just a credit)
        source_text = f"via {source_name} | #AI #ArtificialIntelligence"
        bbox_source = draw.textbbox((0, 0), source_text, font=font_small)
        source_h = bbox_source[3] - bbox_source[1]
        bottom_bar_height = source_h + 40
        draw.rectangle(
            [(0, target_height - bottom_bar_height), (target_width, target_height)],
            fill=(0, 0, 0, 110)
        )
        draw.text(
            (30, target_height - bottom_bar_height + 20),
            source_text, font=font_small, fill=(255, 255, 255, 220)
        )

        img = Image.alpha_composite(img, overlay)
        img.convert("RGB").save(image_path, "JPEG", quality=90)
        print("Successfully added source credit to image.")
        return True
    except Exception as e:
        print(f"Failed to add text to image: {e}")
        return False


def wrap_title_to_fit(title, box_width=940, box_padding=60, max_lines=4,
                       font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
    """Pick the largest font size (from a fixed set) for which the title wraps into
    at most `max_lines` lines that each fit inside box_width, using real measured
    character widths (not a guessed character count). Falls back to the smallest
    size with however many lines it takes if nothing fits within max_lines.
    """
    usable_width = box_width - box_padding
    candidate_sizes = [64, 54, 46, 38]

    last_wrapped, last_size = None, candidate_sizes[-1]
    for size in candidate_sizes:
        font = ImageFont.truetype(font_path, size)
        avg_char_width = font.getlength("Bengaluru Scaler School Technology") / 35
        chars_per_line = max(int(usable_width / avg_char_width), 8)
        wrapped = textwrap.fill(title, width=chars_per_line)
        last_wrapped, last_size = wrapped, size
        if len(wrapped.split('\n')) <= max_lines:
            return wrapped, size

    return last_wrapped, last_size


def build_title_drawtext_filter(title, duration, font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
    """Write the wrapped title to a temp textfile and return an ffmpeg drawtext filter string.
    The box has a fixed width (so it stays consistent regardless of title length) and the
    text is centered both horizontally and per-line. Font size shrinks automatically for
    longer titles so it stays readable instead of wrapping into many cramped lines.
    Positioned in the upper third, fading in over the first second and out with enough
    lead time to leave the last 2 seconds of the clip clean.
    """
    box_width = 940  # fixed width, leaves ~70px margin on each side of the 1080px frame
    wrapped_title, font_size = wrap_title_to_fit(title, box_width=box_width, font_path=font_path)

    textfile_path = '/tmp/title_text.txt'
    with open(textfile_path, 'w') as f:
        f.write(wrapped_title)

    clean_tail = 2  # seconds at the end with no title visible
    fade_out_end = max(duration - clean_tail, 0)
    fade_out_start = max(fade_out_end - 1, 0)

    alpha_expr = (
        f"if(lt(t\\,1)\\,t\\,"
        f"if(lt(t\\,{fade_out_start})\\,1\\,"
        f"if(lt(t\\,{fade_out_end})\\,({fade_out_end}-t)\\,0)))"
    )

    drawtext = (
        f"drawtext=textfile='{textfile_path}':fontfile='{font_path}':"
        f"fontsize={font_size}:fontcolor=white:line_spacing=14:"
        f"text_align=center:"
        f"box=1:boxcolor=black@0.35:boxborderw=30:boxw={box_width}:"
        f"x=(w-{box_width})/2:y=(h*0.30-text_h/2):"
        f"alpha='{alpha_expr}'"
    )
    return drawtext


def convert_image_to_video(image_path, video_path, title=None, duration=10, audio_path=None, audio_start=0):
    """Convert image to a 10-second video using ffmpeg - required for Reels.
    If title is provided, it's drawn centered on the video in a light transparent box,
    fading in over the first second and out over the last second.
    If audio_path is provided, a clip of that track (starting at audio_start) is mixed in
    with a 1s fade-in and 2s fade-out.
    """
    try:
        cmd = ['ffmpeg', '-y', '-loop', '1', '-i', image_path]

        if audio_path:
            cmd += ['-ss', str(audio_start), '-t', str(duration), '-i', audio_path]

        vf_chain = ['scale=1080:1920']
        if title:
            vf_chain.append(build_title_drawtext_filter(title, duration))

        cmd += [
            '-c:v', 'libx264',
            '-t', str(duration),
            '-pix_fmt', 'yuv420p',
            '-vf', ','.join(vf_chain),
            '-r', '30',
        ]

        if audio_path:
            fade_out_start = max(duration - 2, 0)
            cmd += [
                '-c:a', 'aac',
                '-af', f'afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start}:d=2',
                '-shortest',
            ]

        cmd += [video_path]

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

    # 3. Add source credit to image (headline now drawn on the video itself)
    if download_success:
        add_text_to_image(image_path, source_name)

    # 4. Convert Image to Video for Reel (with centered fading title + background music)
    message = f'🤖 {title}\n\n{description}\n\nvia {source_name}\n\n#AI #ArtificialIntelligence #TechNews'

    if download_success:
        clip_duration = 10
        audio_path = pick_music_track()
        audio_start = get_random_clip_start(audio_path, clip_duration) if audio_path else 0

        video_created = convert_image_to_video(
            image_path, video_path,
            title=title,
            duration=clip_duration,
            audio_path=audio_path,
            audio_start=audio_start
        )
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
