"""Cuadros de video: elección de URL y degradación sin ffmpeg (sin red)."""
from creative_intel.media import video


def test_video_url_skips_youtube_and_blobs():
    assert video.video_url(["blob:https://x", "https://www.youtube.com/watch?v=abc", "https://video.xx.fbcdn.net/v/t42/abc.mp4?x=1"]).endswith("x=1")
    assert video.video_url(["https://scontent.fbcdn.net/img.jpg"]) == ""


def test_without_ffmpeg_no_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "ffmpeg_available", lambda: False)
    assert video.extract_frames(1, "https://x/video.mp4", tmp_path) == []
