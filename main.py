import os
import re
from fastapi import FastAPI, HTTPException, Query
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

app = FastAPI(title="YouTube Transcript Service")

# YouTube blocks most datacenter/cloud IPs (Railway included) from fetching
# transcripts. Routing through a proxy (e.g. Webshare) works around this.
# Set these as environment variables in Railway; if unset, no proxy is used.
PROXY_USERNAME = os.environ.get("PROXY_USERNAME")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD")
PROXY_HOST = os.environ.get("PROXY_HOST")
PROXY_PORT = os.environ.get("PROXY_PORT")


def get_proxy_config():
    if PROXY_USERNAME and PROXY_PASSWORD and PROXY_HOST and PROXY_PORT:
        proxy_url = f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
        return GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
    return None


def get_api():
    return YouTubeTranscriptApi(proxy_config=get_proxy_config())


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

    ytt_api = get_api()
    try:
        try:
            fetched = ytt_api.fetch(video_id, languages=[lang, "en", "ko"])
        except NoTranscriptFound:
            # fall back to whatever transcript is available (auto-generated included)
            transcript_list = ytt_api.list(video_id)
            transcript = next(iter(transcript_list))
            fetched = transcript.fetch()

        text = " ".join(seg.text.strip() for seg in fetched if seg.text.strip())
        return {
            "video_id": video_id,
            "length_chars": len(text),
            "transcript": text,
        }
    except TranscriptsDisabled:
        raise HTTPException(
            status_code=404, detail="이 영상은 자막(자동생성 포함)이 비활성화되어 있습니다."
        )
    except VideoUnavailable:
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"자막 추출 실패: {e}")
