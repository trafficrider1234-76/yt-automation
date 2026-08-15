import os
import json
import asyncio
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, AudioFileClip
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configuration
CHANNEL_URL = "https://www.youtube.com/channel/UC9-y-6csu5WGm29I7JiwpnA"
QUEUE_FILE = "queue.json"
STATE_FILE = "last_processed.txt"
OUTPUT_VIDEO = "final_short.mp4"
OUTPUT_AUDIO = "voiceover.mp3"

# YouTube API Setup using environment variables
CLIENT_ID = os.environ.get("YT_CLIENT_ID")
CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")

def get_youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token"
    )
    return build("youtube", "v3", credentials=creds)

def fetch_channel_videos():
    ydl_opts = {
        'extract_flat': True,
        'skip_download': True,
        'socket_timeout': 60,
        'cookies': 'cookies.txt',
        'js_runtimes': {
            'node': {'path': 'node'}
        },
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    }
    with YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.extract_info(f"{CHANNEL_URL}/videos", download=False)
            if 'entries' in result:
                videos = [entry['url'] for entry in result['entries'] if entry.get('url')]
                videos.reverse()
                return videos
        except Exception as e:
            print(f"Error fetching channel: {e}")
    return []

async def generate_voiceover(text):
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    await communicate.save(OUTPUT_AUDIO)

def process_and_upload(video_url, title):
    print(f"Downloading video: {video_url}")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4/best',
        'outtmpl': 'source_video.mp4',
        'noplaylist': True,
        'cookies': 'cookies.txt',
        'js_runtimes': {
            'node': {'path': 'node'}
        },
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    # Voiceover text
    voice_text = "Check out this incredible story! Watch till the end."
    asyncio.run(generate_voiceover(voice_text))

    print("Editing video with MoviePy...")
    video = VideoFileClip("source_video.mp4").subclip(0, min(58, VideoFileClip("source_video.mp4").duration))
    audio = AudioFileClip(OUTPUT_AUDIO)
    
    final_clip = video.set_audio(audio)
    final_clip.write_videofile(OUTPUT_VIDEO, codec="libx264", audio_codec="aac", fps=30)

    print("Uploading to YouTube...")
    youtube = get_youtube_service()
    request_body = {
        "snippet": {
            "title": f"{title[:80]} #shorts",
            "description": "Auto-generated cinematic recap short.",
            "tags": ["shorts", "movierecap", "viral"],
            "categoryId": "24"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(OUTPUT_VIDEO, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=media
    )
    response = request.execute()
    print(f"Uploaded successfully! Video ID: {response.get('id')}")

    for f in ["source_video.mp4", OUTPUT_AUDIO, OUTPUT_VIDEO]:
        if os.path.exists(f):
            os.remove(f)

def main():
    videos = fetch_channel_videos()
    if not videos:
        print("No videos found.")
        return

    last_index = 0
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            content = f.read().strip()
            if content.isdigit():
                last_index = int(content)

    if last_index >= len(videos):
        print("All videos have already been processed!")
        return

    target_url = videos[last_index]
    print(f"Processing item {last_index + 1} of {len(videos)}: {target_url}")

    try:
        process_and_upload(target_url, f"Viral Recap #{last_index + 1}")
        with open(STATE_FILE, "w") as f:
            f.write(str(last_index + 1))
    except Exception as e:
        print(f"Error processing video: {e}")

if __name__ == "__main__":
    main()
