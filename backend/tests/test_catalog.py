from app.services.ytmusic import _secs, big_thumb, norm_album, norm_artists, norm_track


def test_big_thumb_upscales_googleusercontent():
    t = [{"url": "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"}]
    assert big_thumb(t) == "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj"
    assert big_thumb([{"url": "https://yt3.googleusercontent.com/x=s192"}], 300) == "https://yt3.googleusercontent.com/x=w300-h300-l90-rj"
    assert big_thumb([{"url": "https://i.ytimg.com/vi/x/hqdefault.jpg"}]).endswith("hqdefault.jpg")
    assert big_thumb([]) is None


def test_secs():
    assert _secs("3:36") == 216
    assert _secs("1:02:03") == 3723
    assert _secs("1.2M views") is None


def test_norm_artists_drops_noise():
    raw = [{"name": "Burna Boy", "id": "UC1"}, {"name": "1.2M views", "id": None}, {"name": "Song", "id": None}]
    assert norm_artists(raw) == [{"name": "Burna Boy", "id": "UC1"}]


def test_norm_track_song_and_video():
    song = norm_track({"videoId": "abcdefghijk", "title": "Common Person", "videoType": "MUSIC_VIDEO_TYPE_ATV",
                       "artists": [{"name": "Burna Boy", "id": "UC1"}], "album": {"name": "Love, Damini", "id": "MPREb_1"},
                       "duration_seconds": 211, "isExplicit": True, "thumbnails": [{"url": "https://lh3.googleusercontent.com/a=w60-h60"}]})
    assert song["video_type"] == "song" and not song["has_video"] and song["has_audio"]
    assert (song["album_title"], song["album_browse_id"], song["duration_ms"]) == ("Love, Damini", "MPREb_1", 211000)
    assert song["artist_browse_id"] == "UC1" and song["explicit"]
    video = norm_track({"videoId": "zyxwvutsrqp", "title": "X (Official Video)", "videoType": "MUSIC_VIDEO_TYPE_OMV",
                        "artists": [{"name": "A", "id": "UC2"}], "length": "4:01"})
    assert video["video_type"] == "video" and video["has_video"] and video["duration_ms"] == 241000
    assert norm_track({"title": "no id"}) is None
    assert norm_track({"videoId": "abcdefghijk", "isAvailable": False}) is None


def test_norm_track_album_page_shapes():
    # album pages give the album as a plain string and often no per-track artists
    t = norm_track({"videoId": "abcdefghijk", "title": "No Panic", "album": "No Sign Of Weakness", "artists": None},
                   album={"name": "No Sign Of Weakness", "id": "MPREb_X"}, album_thumb="https://x/y.jpg",
                   artists_fallback=[{"name": "Burna Boy", "id": "UC1"}])
    assert t["album_title"] == "No Sign Of Weakness" and t["album_browse_id"] == "MPREb_X"
    assert t["artist_name"] == "Burna Boy" and t["thumbnail"] == "https://x/y.jpg"


def test_norm_album():
    a = norm_album({"browseId": "MPREb_1", "title": "Love, Damini", "type": "Album", "year": "2022",
                    "artists": [{"name": "Burna Boy", "id": "UC1"}], "thumbnails": []})
    assert a == {"browse_id": "MPREb_1", "title": "Love, Damini", "artist_name": "Burna Boy", "artist_browse_id": "UC1",
                 "year": "2022", "type": "Album", "thumbnail": None}
    assert norm_album({"title": "no id"}) is None
