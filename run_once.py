
import os
import re
import sys
import json
import time
import random
import logging
import subprocess

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

# ── Environment ──────────────────────────────────────────────────────────────
PEXELS_API_KEY    = os.environ["PEXELS_API_KEY"]
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
IG_ACCOUNT_1_ID   = os.environ["IG_ACCOUNT_1_ID"]
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
GITHUB_REPOSITORY = os.environ["GITHUB_REPOSITORY"]  # auto-provided by Actions as "owner/repo"

GRAPH_API = "https://graph.facebook.com/v21.0"

OUTPUT_DIR = "reels_tmp"
STATE_PATH = "state.json"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PEXELS_HEADERS = {"Authorization": PEXELS_API_KEY}

# Speed multiplier applied to every clip
SPEED = 1.5

# ── Source categories (from your Pexels links) ───────────────────────────────
VIDEO_QUERIES = [
    "tropical cliff",
    "tropical ocean landscape",
    "beach 4k drone videos tropical",
]

PHOTO_QUERIES = [
    "cinematic 4k landscape tropical",
    "tropical beach",
    "tropical cliff",
]

# Same clip timing structure as the original automation (totals ~10s)
CLIP_STRUCTURE = [
    ("photo", 0.1), ("photo", 0.1), ("photo", 0.1), ("photo", 0.1),
    ("photo", 0.1), ("photo", 0.1), ("photo", 0.1), ("photo", 0.1),
    ("photo", 0.1), ("photo", 0.1), ("photo", 0.1), ("photo", 0.1),
    ("video", 1.4),
    ("video", 0.9), ("video", 0.9),
    ("video", 0.6), ("video", 0.6),
    ("video", 0.9), ("video", 0.9),
    ("video", 0.7),
    ("video", 1.0),
    ("video", 0.9),
]

# ── State (caption dedup, committed back to the repo each run) ──────────────

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"used_captions": []}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── Pexels ────────────────────────────────────────────────────────────────────

def fetch_one_photo(tmp_path):
    query = random.choice(PHOTO_QUERIES)
    page = random.randint(1, 20)
    url = (f"https://api.pexels.com/v1/search"
           f"?query={requests.utils.quote(query)}&per_page=40&page={page}")
    resp = requests.get(url, headers=PEXELS_HEADERS, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("photos", [])
    if not items:
        return None
    photo = random.choice(items)
    src = photo["src"].get("large2x") or photo["src"].get("original")
    r = requests.get(src, stream=True, timeout=30)
    with open(tmp_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return tmp_path


def fetch_one_video(tmp_path):
    query = random.choice(VIDEO_QUERIES)
    page = random.randint(1, 15)
    url = (f"https://api.pexels.com/videos/search"
           f"?query={requests.utils.quote(query)}&per_page=40&page={page}&orientation=portrait")
    resp = requests.get(url, headers=PEXELS_HEADERS, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("videos", [])
    if not items:
        return None
    video = random.choice(items)
    files = video.get("video_files", [])
    portrait = [f for f in files if f.get("height", 0) > f.get("width", 0)] or files
    if not portrait:
        return None
    best = max(portrait, key=lambda f: f.get("height", 0))
    link = best.get("link")
    if not link:
        return None
    r = requests.get(link, stream=True, timeout=60)
    with open(tmp_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return tmp_path


def make_photo_clip(job_id, index, duration):
    raw = f"{OUTPUT_DIR}/raw_photo_{job_id}_{index}.jpg"
    out = f"{OUTPUT_DIR}/clip_{job_id}_{index:02d}.mp4"
    try:
        if not fetch_one_photo(raw):
            return None
        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", raw,
            "-t", str(duration),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            out,
        ], capture_output=True)
        return out if os.path.exists(out) else None
    finally:
        try: os.remove(raw)
        except: pass


def make_video_clip(job_id, index, duration):
    """Pull a clip and speed it up by SPEED so its final length == duration."""
    raw = f"{OUTPUT_DIR}/raw_video_{job_id}_{index}.mp4"
    out = f"{OUTPUT_DIR}/clip_{job_id}_{index:02d}.mp4"
    try:
        if not fetch_one_video(raw):
            return None

        raw_duration_needed = duration * SPEED  # more source footage since we'll speed it up

        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", raw],
            capture_output=True, text=True,
        )
        try:
            total_dur = float(result.stdout.strip())
        except Exception:
            total_dur = raw_duration_needed

        start = random.uniform(0, max(0, total_dur - raw_duration_needed))

        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start), "-i", raw,
            "-t", str(raw_duration_needed),
            "-vf", (f"scale=1080:1920:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920,setsar=1,setpts=PTS/{SPEED}"),
            "-r", "30", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            out,
        ], capture_output=True)
        return out if os.path.exists(out) else None
    finally:
        try: os.remove(raw)
        except: pass


def build_reel(job_id):
    logger.info(f"Building reel {job_id}")
    clip_paths = []
    for i, (clip_type, duration) in enumerate(CLIP_STRUCTURE):
        path = make_photo_clip(job_id, i, duration) if clip_type == "photo" else make_video_clip(job_id, i, duration)
        if path:
            clip_paths.append(path)

    if not clip_paths:
        raise Exception("No clips generated")

    concat_file = f"{OUTPUT_DIR}/concat_{job_id}.txt"
    with open(concat_file, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{os.path.abspath(cp)}'\n")

    content_video = f"{OUTPUT_DIR}/content_{job_id}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an",
        content_video,
    ], capture_output=True)

    os.remove(concat_file)
    for cp in clip_paths:
        try: os.remove(cp)
        except: pass

    final_video = f"{OUTPUT_DIR}/final_{job_id}.mp4"
    os.rename(content_video, final_video)

    if not os.path.exists(final_video):
        raise Exception("No final video produced")

    # Reels processing frequently rejects video-only files — add a silent
    # audio track and make the file streaming-friendly (faststart) so
    # Meta's fetcher can read it reliably.
    with_audio = f"{OUTPUT_DIR}/with_audio_{job_id}.mp4"
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", final_video,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        with_audio,
    ], capture_output=True)

    if result.returncode == 0 and os.path.exists(with_audio):
        try: os.remove(final_video)
        except: pass
        final_video = with_audio
        logger.info("Added silent audio track + faststart")
    else:
        logger.info(f"Silent-audio pass failed, posting video-only: {result.stderr.decode()[:300]}")

    logger.info(f"Reel built: {final_video}")
    return final_video


# ── Captions ──────────────────────────────────────────────────────────────────

CAPTION_HASHTAGS = "#cinematic #cinematicvideo #reels #nature"
FIXED_CAPTION = "📸"


def format_instagram_caption():
    return f"{FIXED_CAPTION}\n\n{CAPTION_HASHTAGS}"


# ── GitHub release hosting ────────────────────────────────────────────────────

def upload_via_github_release(video_path):
    tag = f"reel-{int(time.time())}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases",
        headers=headers,
        json={"tag_name": tag, "name": tag, "draft": False, "prerelease": False},
        timeout=30,
    )
    resp.raise_for_status()
    release = resp.json()

    upload_url = release["upload_url"].split("{")[0]
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    upload_resp = requests.post(
        f"{upload_url}?name={filename}",
        headers={**headers, "Content-Type": "video/mp4"},
        data=video_bytes,
        timeout=120,
    )
    upload_resp.raise_for_status()
    asset = upload_resp.json()
    return release["id"], tag, asset["browser_download_url"]


def delete_github_release(release_id, tag):
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        requests.delete(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/{release_id}",
                         headers=headers, timeout=30)
        requests.delete(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/git/refs/tags/{tag}",
                         headers=headers, timeout=30)
    except Exception as e:
        logger.info(f"Release cleanup failed (non-fatal): {e}")


# ── Instagram Graph API ───────────────────────────────────────────────────────

def publish_to_instagram(video_url, caption, ig_account_id):
    resp = requests.post(
        f"{GRAPH_API}/{ig_account_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": META_ACCESS_TOKEN,
        },
        timeout=60,
    )
    resp.raise_for_status()
    creation_id = resp.json()["id"]
    logger.info(f"Created media container: {creation_id}")

    for attempt in range(30):
        status_resp = requests.get(
            f"{GRAPH_API}/{creation_id}",
            params={"fields": "status_code", "access_token": META_ACCESS_TOKEN},
            timeout=30,
        ).json()
        status = status_resp.get("status_code")
        logger.info(f"Container status ({attempt + 1}/30): {status}")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise Exception(f"Media container failed: {status_resp}")
        time.sleep(10)
    else:
        raise Exception("Timed out waiting for media container to finish processing")

    publish_resp = requests.post(
        f"{GRAPH_API}/{ig_account_id}/media_publish",
        data={"creation_id": creation_id, "access_token": META_ACCESS_TOKEN},
        timeout=60,
    )
    publish_resp.raise_for_status()
    return publish_resp.json()


MAX_ATTEMPTS = 3


def attempt_post(job_id, caption):
    """One full try: build -> upload -> publish. Returns True on success."""
    video_path = build_reel(job_id)
    release_id, tag, video_url = upload_via_github_release(video_path)
    logger.info(f"Uploaded to GitHub release: {video_url}")

    try:
        result = publish_to_instagram(video_url, caption, IG_ACCOUNT_1_ID)
        logger.info(f"Posted to Instagram: {result}")
        return True
    finally:
        delete_github_release(release_id, tag)
        try: os.remove(video_path)
        except: pass


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    state = load_state()
    caption = format_instagram_caption()
    logger.info(f"Caption: {caption[:60]}...")

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        job_id = str(random.randint(100000, 999999))
        # Give GitHub's CDN a little longer to settle on each retry
        wait_before_publish = 8 + (attempt - 1) * 12
        try:
            logger.info(f"Attempt {attempt}/{MAX_ATTEMPTS} (job {job_id})")
            video_path = build_reel(job_id)
            release_id, tag, video_url = upload_via_github_release(video_path)
            logger.info(f"Uploaded to GitHub release: {video_url}")
            time.sleep(wait_before_publish)
            try:
                result = publish_to_instagram(video_url, caption, IG_ACCOUNT_1_ID)
                logger.info(f"Posted to Instagram: {result}")
                save_state(state)
                logger.info("Done.")
                return
            finally:
                delete_github_release(release_id, tag)
                try: os.remove(video_path)
                except: pass
        except Exception as e:
            last_error = e
            logger.info(f"Attempt {attempt} failed: {e}")
            if attempt < MAX_ATTEMPTS:
                logger.info("Retrying with a freshly built reel...")
                time.sleep(15)

    # All attempts failed — still save caption state so we don't get stuck
    # re-trying the exact same caption forever, and surface the real error.
    save_state(state)
    raise Exception(f"All {MAX_ATTEMPTS} attempts failed. Last error: {last_error}")


if __name__ == "__main__":
    main()
