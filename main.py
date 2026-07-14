import os
import re

import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="YouTube Transcript Service")

# Optional proxy (used for both the yt-dlp metadata request and the
# subtitle-file download). Set these as environment variables in Railway;
# if unset, requests go out directly.
PROXY_USERNAME = os.environ.get("PROXY_USERNAME")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD")
PROXY_HOST = os.environ.get("PROXY_HOST")
PROXY_PORT = os.environ.get("PROXY_PORT")


def build_proxy_url():
    if PROXY_USERNAME and PROXY_PASSWORD and PROXY_HOST and PROXY_PORT:
        return f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
    return None


class NoCaptionsError(Exception):
    pass


def extract_video_id(url_or_id: str) -> str:
    """Accepts a full YouTube URL or a bare 11-char video ID and returns the video ID."""
    patterns = [
        r"(?:v=|/videos/|youtu\.be/|/embed/|/v/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    raise ValueError(f"Could not extract a video ID from: {url_or_id}")


def vtt_to_text(vtt_content: str) -> str:
    """Strip a WebVTT subtitle file down to plain, deduplicated text."""
    lines = vtt_content.splitlines()
    text_lines = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        clean = re.sub(r"<[^>]+>", "", line)
        clean = clean.strip()
        if clean:
            text_lines.append(clean)

    deduped = []
    for line in text_lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return " ".join(deduped)


def fetch_transcript_text(video_id: str, lang: str) -> str:
    proxy_url = build_proxy_url()
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang, "en", "ko"],
        "subtitlesformat": "vtt",
        "quiet": True,
        "no_warnings": True,
    }
    if proxy_url:
        ydl_opts["proxy"] = proxy_url

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )

    subs = info.get("subtitles") or {}
    autosubs = info.get("automatic_captions") or {}

    track = None
    for lang_try in [lang, "en", "ko"]:
        if lang_try in subs:
            track = subs[lang_try]
            break
    if track is None:
        for lang_try in [lang, "en", "ko"]:
            if lang_try in autosubs:
                track = autosubs[lang_try]
                break

    if not track:
        raise NoCaptionsError("No subtitle or auto-caption track available")

    vtt_url = None
    for fmt in track:
        if fmt.get("ext") == "vtt":
            vtt_url = fmt.get("url")
            break
    if vtt_url is None:
        vtt_url = track[0].get("url")

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(vtt_url, timeout=20, proxies=proxies, headers=headers)
    resp.raise_for_status()
    return vtt_to_text(resp.text)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/transcript")
def get_transcript(
    video: str = Query(..., description="YouTube URL or video ID"),
    lang: str = Query("ko", description="Preferred language code"),
):
    try:
        video_id = extract_video_id(video)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        text = fetch_transcript_text(video_id, lang)
        return {
            "video_id": video_id,
            "length_chars": len(text),
            "transcript": text,
        }
    except NoCaptionsError:
        raise HTTPException(
            status_code=404, detail="이 영상은 자막(자동생성 포함)이 없습니다."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"자막 추출 실패: {e}")
