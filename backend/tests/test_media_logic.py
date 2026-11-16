from pathlib import Path

from app.services.lyrics import parse_lrc
from app.services.resolver import clean_metadata, pick_audio, pick_video
from app.services.tags import parse_filename, split_artists


def test_parse_lrc():
    text = """[ti:Song]
[offset:+500]
[00:12.00]First line
[00:15.5]<00:15.50>Word <00:16.00>timed
[00:20.00][01:00.00]Chorus
plain trailing line
"""
    lines, plain = parse_lrc(text)
    assert lines == [
        {"t": 11500, "text": "First line"},
        {"t": 15000, "text": "Word timed"},
        {"t": 19500, "text": "Chorus"},
        {"t": 59500, "text": "Chorus"},
    ]
    assert "plain trailing line" in plain and "Song" not in plain


def test_split_artists():
    assert split_artists("Drake feat. Rihanna & Future", "Title") == ("Drake", ["Rihanna", "Future"])
    assert split_artists("Simon & Garfunkel", "The Boxer") == ("Simon & Garfunkel", [])
    assert split_artists("A", "Song (feat. B)") == ("A", ["B"])


def test_parse_filename():
    assert parse_filename(Path("03 - Daft Punk - One More Time.mp3")) == ("One More Time", "Daft Punk", 3)
    assert parse_filename(Path("Intro.flac")) == ("Intro", None, None)


FORMATS = [
    {"format_id": "251", "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 160, "url": "u", "protocol": "https"},
    {"format_id": "140", "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 129, "url": "u", "protocol": "https"},
    {"format_id": "140-drc", "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 129, "url": "u", "protocol": "https"},
    {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "url": "u", "protocol": "https"},
    {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 1080, "url": "u", "protocol": "https"},
    {"format_id": "313", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 2160, "url": "u", "protocol": "https"},
    {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "url": "u", "protocol": "https"},
    {"format_id": "hls", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "url": "u", "protocol": "m3u8_native"},
]


def test_pick_formats():
    assert pick_audio(FORMATS)["format_id"] == "140"          # AAC, non-DRC
    assert pick_video(FORMATS, 1080)["format_id"] == "137"    # H.264 preferred at equal height
    assert pick_video(FORMATS, 2160)["format_id"] == "313"    # height beats codec
    assert pick_video(FORMATS, 480)["format_id"] == "18"      # falls back to muxed


def test_clean_metadata():
    m = clean_metadata({"title": "Daft Punk - One More Time (Official Video)", "channel": "Daft Punk",
                        "duration": 320, "upload_date": "20090601"})
    assert (m["artist"], m["title"], m["release_year"]) == ("Daft Punk", "One More Time", 2009)
    m = clean_metadata({"title": "x", "track": "Real Title", "artist": "Real Artist", "album": "LP"})
    assert (m["artist"], m["title"], m["album"]) == ("Real Artist", "Real Title", "LP")
    m = clean_metadata({"title": "Song [Lyric Video] | Label Records", "channel": "Some Band - Topic"})
    assert (m["artist"], m["title"]) == ("Some Band", "Song")
