import os
import json
import asyncio
import subprocess
from yt_dlp import YoutubeDL
# moviepy >= 2.0 removed the `moviepy.editor` namespace — import directly
# from `moviepy` instead. If you're on moviepy 1.x, use
# `from moviepy.editor import VideoFileClip, AudioFileClip` instead.
from moviepy import VideoFileClip, AudioFileClip
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configuration
CHANNEL_URL = "https://www.youtube.com/channel/UC9-y-6csu5WGm29I7JiwpnA"
STATE_FILE = "last_processed.txt"
OUTPUT_VIDEO = "final_short.mp4"
OUTPUT_AUDIO = "voiceover.mp3"

# Absolute path for cookies
COOKIES_PATH = os.path.abspath("cookies.txt")

# Optional: set this as a GitHub secret if YouTube starts blocking the
# Actions runner's IP range (datacenter IPs are commonly flagged).
# Format: http://user:pass@host:port
PROXY_URL = os.environ.get("YT_PROXY_URL")

# YouTube API Setup using environment variables
CLIENT_ID = os.environ.get("YT_CLIENT_ID")
CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")


def get_deno_path():
    """Locate the deno binary and sanity-check its version.

    yt-dlp's EJS system requires Deno >= 2.3.0. If setup-deno installs an
    older version, `deno --version` still succeeds but yt-dlp will silently
    refuse to use it and print "No supported JavaScript runtime could be
    found." This check makes that failure visible in the Actions log
    instead of surfacing later as a confusing bot-check error.
    """
    try:
        result = subprocess.run(['which', 'deno'], capture_output=True, text=True)
        path = result.stdout.strip() or '/home/runner/.deno/bin/deno'
    except Exception:
        path = '/home/runner/.deno/bin/deno'

    try:
        version_out = subprocess.run(
            [path, '--version'], capture_output=True, text=True
        ).stdout
        print(f"Deno path: {path}")
        print(f"Deno version output: {version_out.strip()}")
        # Expect a line like: "deno 2.3.1 (...)"
        first_line = version_out.splitlines()[0] if version_out else ""
        parts = first_line.split()
        if len(parts) >= 2:
            major = int(parts[1].split('.')[0])
            if major < 2:
                print(
                    "WARNING: Deno version is below 2.x. yt-dlp requires "
                    ">=2.3.0 for its JS runtime support. Update the "
                    "deno-version in your workflow's setup-deno step."
                )
    except Exception as e:
        print(f"Could not verify deno version: {e}")

    return path


def build_ydl_opts(extra_opts=None):
    """Shared yt-dlp options builder so both functions stay in sync."""
    deno_bin = get_deno_path()
    opts = {
        'cookiefile': COOKIES_PATH,
        'socket_timeout': 60,
        'js_runtimes': {
            'deno': {'path': deno_bin}
        },
        # Ensures yt-dlp can fetch the EJS challenge-solver scripts even if
        # the yt-dlp-ejs package isn't bundled with your install. Safe to
        # leave enabled even if you also install yt-dlp[default].
        'remote_components': {'ejs': 'github'},
        'user_agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        ),
    }
    if PROXY_URL:
        opts['proxy'] = PROXY_URL
    if extra_opts:
        opts.update(extra_opts)
    return opts


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
    print(f"Cookies file exists: {os.path.exists(COOKIES_PATH)}")
    ydl_opts = build_ydl_opts({
        'extract_flat': True,
        'skip_download': True,
    })
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
    ydl_opts = build_ydl_opts({
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4/best',
        'outtmpl': 'source_video.mp4',
        'noplaylist': True,
    })
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    # Voiceover text
    voice_text = "Check out this incredible story! Watch till the end."
    asyncio.run(generate_voiceover(voice_text))

    print("Editing video with MoviePy...")
    source_clip = VideoFileClip("source_video.mp4")
    # moviepy 2.x renamed .subclip() -> .subclipped() and
    # .set_audio() -> .with_audio(). If you pin moviepy<2.0, swap these back.
    video = source_clip.subclipped(0, min(58, source_clip.duration))
    audio = AudioFileClip(OUTPUT_AUDIO)

    final_clip = video.with_audio(audio)
    final_clip.write_videofile(OUTPUT_VIDEO, codec="libx264", audio_codec="aac", fps=30)

    source_clip.close()
    video.close()
    audio.close()
    final_clip.close()

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
