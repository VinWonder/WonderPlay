"""Read metadata, technical info, embedded art and lyrics with mutagen.

Pure / blocking: call through `asyncio.to_thread` from async code.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from .lyrics import parse_lrc, sylt_to_lines

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav"}
VIDEO_EXTS = {".mp4", ".m4v", ".webm"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

_FEAT_SPLIT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_TITLE_FEAT = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]", re.IGNORECASE)
# Only used on the *featured* part, so band names like "Simon & Garfunkel" as a
# primary artist are never split.
_MULTI_ARTIST = re.compile(r"\s*(?:,|&|/|;)\s*")
_LEADING_TRACKNO = re.compile(r"^\s*(\d{1,3})\s*[-._)\s]\s*")
_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")
_YEAR = re.compile(r"(\d{4})")
_GAIN = re.compile(r"(-?\d+(?:\.\d+)?)")


@dataclass
class TagInfo:
    title: str
    artist: str | None = None
    featured: list[str] = field(default_factory=list)
    album_artist: str | None = None
    album: str | None = None
    genre: str | None = None
    year: int | None = None
    date: str | None = None            # ISO "YYYY-MM-DD" when the tag has a full date
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int = 1
    total_discs: int | None = None
    isrc: str | None = None
    bpm: float | None = None
    explicit: bool = False
    compilation: bool = False
    replaygain_track_db: float | None = None
    replaygain_album_db: float | None = None

    duration_ms: int | None = None
    bitrate_kbps: int | None = None
    sample_rate_hz: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    audio_codec: str | None = None
    container: str | None = None

    cover: bytes | None = None
    lyrics_plain: str | None = None
    lyrics_synced: list[dict] | None = None


_ID3_EASY = {
    "title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB", "genre": "TCON",
    "date": "TDRC", "originaldate": "TDOR", "year": "TYER", "tracknumber": "TRCK",
    "discnumber": "TPOS", "isrc": "TSRC", "bpm": "TBPM", "compilation": "TCMP",
    "replaygain_track_gain": "TXXX:REPLAYGAIN_TRACK_GAIN",
    "replaygain_album_gain": "TXXX:REPLAYGAIN_ALBUM_GAIN",
}


class _ID3Easy:
    """mutagen ignores easy=True for WAV/AIFF and hands back raw ID3 frames;
    this maps the friendly keys used below onto frame ids."""

    def __init__(self, id3: ID3):
        self._id3 = id3

    def get(self, key: str):
        frame_id = _ID3_EASY.get(key.lower())
        if not frame_id:
            return None
        frames = self._id3.getall(frame_id)
        if not frames and frame_id.startswith("TXXX:"):  # descriptions are case-sensitive
            frames = [f for f in self._id3.getall("TXXX") if f.desc.upper() == frame_id[5:]]
        return [str(t) for f in frames for t in getattr(f, "text", [])] or None


def _first(tags, *keys: str) -> str | None:
    if not tags:
        return None
    for k in keys:
        try:
            v = tags.get(k)
        except (KeyError, ValueError):
            v = None
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _num_pair(value: str | None) -> tuple[int | None, int | None]:
    """'3/12' -> (3, 12); '3' -> (3, None)."""
    if not value:
        return None, None
    parts = str(value).split("/")
    def to_int(s: str) -> int | None:
        s = s.strip()
        return int(s) if s.isdigit() and int(s) > 0 else None
    return to_int(parts[0]), (to_int(parts[1]) if len(parts) > 1 else None)


def split_artists(artist: str | None, title: str) -> tuple[str | None, list[str]]:
    """'A feat. B & C' -> ('A', ['B', 'C']). Also picks '(feat. X)' out of titles."""
    featured: list[str] = []
    primary = artist
    if artist:
        parts = _FEAT_SPLIT.split(artist, maxsplit=1)
        primary = parts[0].strip()
        if len(parts) > 1:
            featured += [p for p in _MULTI_ARTIST.split(parts[1]) if p.strip()]
    m = _TITLE_FEAT.search(title or "")
    if m:
        featured += [p for p in _MULTI_ARTIST.split(m.group(1)) if p.strip()]
    seen, out = set(), []
    for f in featured:
        f = f.strip()
        if f and f.lower() not in seen and f.lower() != (primary or "").lower():
            seen.add(f.lower())
            out.append(f)
    return primary or None, out


def parse_filename(path: Path) -> tuple[str, str | None, int | None]:
    """'03 - Artist - Title.mp3' -> ('Title', 'Artist', 3)."""
    stem = path.stem
    track_no = None
    m = _LEADING_TRACKNO.match(stem)
    if m and len(stem) > m.end():
        track_no = int(m.group(1)) or None
        stem = stem[m.end():]
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip() or path.stem, artist.strip() or None, track_no
    return stem.strip() or path.stem, None, track_no


def _maybe_lrc(text: str | None) -> tuple[list[dict] | None, str | None]:
    if not text:
        return None, None
    synced, plain = parse_lrc(text)
    if synced:
        return synced, plain
    return None, text.strip() or None


def _read_art_and_lyrics(path: Path, info: TagInfo) -> None:
    try:
        f = mutagen.File(path)
    except Exception:
        return
    if f is None:
        return
    tags = f.tags

    if isinstance(tags, ID3):  # MP3, WAV, AIFF
        pics = tags.getall("APIC")
        if pics:
            front = next((p for p in pics if p.type == 3), pics[0])
            info.cover = bytes(front.data)
        for sylt in tags.getall("SYLT"):
            if sylt.format == 2 and sylt.text:  # absolute milliseconds
                info.lyrics_synced = sylt_to_lines(sylt.text)
                break
        uslt = tags.getall("USLT")
        if uslt:
            synced, plain = _maybe_lrc(uslt[0].text)
            info.lyrics_synced = info.lyrics_synced or synced
            info.lyrics_plain = plain
        adv = tags.getall("TXXX:ITUNESADVISORY")
        if adv and str(adv[0].text[0]).strip() == "1":
            info.explicit = True
        return

    if isinstance(f, MP4):
        if tags:
            covr = tags.get("covr")
            if covr:
                info.cover = bytes(covr[0])
            lyr = tags.get("\xa9lyr")
            if lyr:
                info.lyrics_synced, info.lyrics_plain = _maybe_lrc(str(lyr[0]))
            rtng = tags.get("rtng")
            if rtng and rtng[0] in (1, 4):
                info.explicit = True
        return

    if isinstance(f, FLAC) and f.pictures:
        front = next((p for p in f.pictures if p.type == 3), f.pictures[0])
        info.cover = bytes(front.data)

    if tags is not None and hasattr(tags, "get"):  # Vorbis comments (FLAC, Ogg, Opus)
        if info.cover is None:
            for b64 in tags.get("metadata_block_picture", []) or []:
                try:
                    info.cover = Picture(base64.b64decode(b64)).data
                    break
                except Exception:
                    continue
        text = _first(tags, "lyrics", "unsyncedlyrics", "LYRICS", "UNSYNCEDLYRICS")
        if text:
            info.lyrics_synced, info.lyrics_plain = _maybe_lrc(text)
        if (_first(tags, "itunesadvisory", "explicit") or "") in ("1", "true", "yes"):
            info.explicit = True


def read_tags(path: Path) -> TagInfo:
    fallback_title, fallback_artist, fallback_trackno = parse_filename(path)
    info = TagInfo(title=fallback_title)
    info.container = path.suffix.lower().lstrip(".")

    try:
        easy = mutagen.File(path, easy=True)
    except Exception:
        easy = None

    if easy is not None:
        tags = _ID3Easy(easy.tags) if isinstance(easy.tags, ID3) else easy.tags
        info.title = _first(tags, "title") or fallback_title
        raw_artist = _first(tags, "artist") or fallback_artist
        info.album_artist = _first(tags, "albumartist", "album artist", "album_artist")
        info.album = _first(tags, "album")
        info.genre = _first(tags, "genre")

        date = _first(tags, "date", "originaldate", "year")
        if date:
            m = _YEAR.search(date)
            if m and 1000 <= int(m.group(1)) <= 9999:
                info.year = int(m.group(1))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date[:10]):
                info.date = date[:10]

        tn, tt = _num_pair(_first(tags, "tracknumber"))
        info.track_number = tn or fallback_trackno
        info.total_tracks = tt or _num_pair(_first(tags, "totaltracks", "tracktotal"))[0]
        dn, dt = _num_pair(_first(tags, "discnumber"))
        info.disc_number = dn or 1
        info.total_discs = dt or _num_pair(_first(tags, "totaldiscs", "disctotal"))[0]

        isrc = (_first(tags, "isrc") or "").replace("-", "").upper()
        info.isrc = isrc if _ISRC.match(isrc) else None
        try:
            bpm = float(_first(tags, "bpm") or 0)
            info.bpm = round(bpm, 1) if 0 < bpm < 10000 else None
        except ValueError:
            pass
        info.compilation = (_first(tags, "compilation") or "") in ("1", "true", "True")

        for attr, key in (("replaygain_track_db", "replaygain_track_gain"),
                          ("replaygain_album_db", "replaygain_album_gain")):
            g = _first(tags, key)
            m = _GAIN.search(g or "")
            if m and abs(float(m.group(1))) < 1000:
                setattr(info, attr, round(float(m.group(1)), 2))

        info.artist, info.featured = split_artists(raw_artist, info.title)

        ai = easy.info
        if ai is not None:
            length = getattr(ai, "length", 0) or 0
            info.duration_ms = int(length * 1000) if length > 0 else None
            br = getattr(ai, "bitrate", 0) or 0
            info.bitrate_kbps = int(br / 1000) if br > 0 else None
            info.sample_rate_hz = getattr(ai, "sample_rate", None) or None
            info.bit_depth = getattr(ai, "bits_per_sample", None) or None
            info.channels = getattr(ai, "channels", None) or None
            info.audio_codec = getattr(ai, "codec", None) or {
                "mp3": "mp3", "flac": "flac", "ogg": "vorbis", "oga": "vorbis",
                "opus": "opus", "wav": "pcm", "aac": "aac",
            }.get(info.container)
    else:
        info.artist, info.featured = split_artists(fallback_artist, info.title)
        info.track_number = fallback_trackno

    _read_art_and_lyrics(path, info)

    # Sidecar .lrc beats embedded lyrics (it's usually the hand-tuned one).
    lrc = path.with_suffix(".lrc")
    if lrc.exists():
        try:
            text = lrc.read_text(encoding="utf-8-sig", errors="replace")
            synced, plain = parse_lrc(text)
            if synced:
                info.lyrics_synced, info.lyrics_plain = synced, plain or info.lyrics_plain
        except OSError:
            pass
    return info


_FOLDER_ART = ("cover", "folder", "front", "album", "albumart")


def find_folder_art(directory: Path) -> Path | None:
    """cover.jpg / folder.png / ... next to the audio files."""
    try:
        entries = {p.name.lower(): p for p in directory.iterdir() if p.is_file()}
    except OSError:
        return None
    for stem in _FOLDER_ART:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = entries.get(stem + ext)
            if p:
                return p
    return None
