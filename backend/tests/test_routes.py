"""Fallback across YouTube player clients and networks (WARP / direct)."""
import pytest
import yt_dlp

from app.services import netproxy, resolver

BOT = "ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies"
AUDIO = {"format_id": "140", "url": "https://rr.googlevideo.com/a", "protocol": "https",
         "ext": "m4a", "acodec": "mp4a", "vcodec": "none", "abr": 128}


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL; `blocked` lists (client, via) routes YouTube refuses."""
    blocked: set = set()
    calls: list = []

    def __init__(self, opts):
        args = (opts.get("extractor_args") or {}).get("youtube", {})
        client = (args.get("player_client") or [None])[0]
        self.route = (client, resolver.VIA_WARP if opts.get("proxy") else resolver.VIA_DIRECT)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.calls.append(self.route)
        if self.route in FakeYDL.blocked or ("*", self.route[1]) in FakeYDL.blocked:
            raise yt_dlp.utils.DownloadError(BOT)
        return {"id": "x", "formats": [dict(AUDIO)]}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(resolver.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(resolver.ytcookies, "current_path", lambda: None)
    monkeypatch.setattr(netproxy, "proxy_url", "socks5://127.0.0.1:40000")
    FakeYDL.blocked, FakeYDL.calls = set(), []
    resolver.reset_pool()
    yield FakeYDL
    resolver.reset_pool()


def test_routes_try_every_client_on_warp_then_direct(fake):
    rs = resolver.routes()
    assert rs[0] == ("tv_samsung", "warp")
    assert rs.index(("visionos", "warp")) < rs.index(("tv_samsung", "direct"))
    assert (None, "direct") in rs


def test_falls_back_to_another_client_and_remembers_it(fake):
    fake.blocked = {("tv_samsung", "warp")}
    info = resolver._extract_sync("https://youtu.be/x")
    assert resolver.last_route == ("visionos", "warp")
    assert "X-WP-Via" not in (info["formats"][0].get("http_headers") or {})
    fake.calls.clear()
    resolver._extract_sync("https://youtu.be/y")
    assert fake.calls == [("visionos", "warp")]          # sticky: straight to the winner


def test_direct_route_marks_streams_to_skip_warp(fake):
    fake.blocked = {("*", "warp")}
    info = resolver._extract_sync("https://youtu.be/x")
    assert resolver.last_route == ("tv_samsung", "direct")
    assert info["formats"][0]["http_headers"]["X-WP-Via"] == "direct"
    src = resolver.format_to_source(resolver.pick_audio(info["formats"]), "audio")
    assert src["stream_headers"]["X-WP-Via"] == "direct"


def test_everything_blocked_is_a_bot_check(fake):
    fake.blocked = {("*", "warp"), ("*", "direct")}
    with pytest.raises(resolver.BotCheckError):
        resolver._extract_sync("https://youtu.be/x")
    assert len(resolver.last_attempts) == len(resolver.routes())
    assert not any(a["ok"] for a in resolver.last_attempts)


def test_permanent_errors_do_not_cycle_routes(fake, monkeypatch):
    def private(self, url, download=False):
        FakeYDL.calls.append(self.route)
        raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: Private video. Sign in if you've been granted access")
    monkeypatch.setattr(FakeYDL, "extract_info", private)
    with pytest.raises(resolver.ResolveError) as e:
        resolver._extract_sync("https://youtu.be/x")
    assert not isinstance(e.value, resolver.BotCheckError) and len(fake.calls) == 1


def test_cookies_never_leave_through_the_direct_route(fake, monkeypatch):
    monkeypatch.setattr(resolver.ytcookies, "current_path", lambda: "/tmp/c.txt")
    monkeypatch.setattr(resolver, "_base_opts", lambda **kw: {"cookiefile": "/tmp/c.txt",
                                                             "proxy": netproxy.proxy_url, **kw})
    assert resolver._route_opts(("tv_samsung", "warp")).get("cookiefile")
    assert "cookiefile" not in resolver._route_opts(("tv_samsung", "direct"))
    assert "cookiefile" not in resolver._route_opts(("android_vr", "warp"))   # client can't use cookies


def test_expired_cookies_are_reported_as_expired(fake, monkeypatch):
    from app.services import ytcookies
    monkeypatch.setitem(ytcookies.state, "expired", False)
    fake.blocked = {("*", "warp"), ("*", "direct")}
    real_route_opts = resolver._route_opts

    def opts_with_warning(route):
        o = real_route_opts(route)
        o["logger"].warning("[youtube] The provided YouTube account cookies are no longer valid.")
        return o
    monkeypatch.setattr(resolver, "_route_opts", opts_with_warning)
    with pytest.raises(resolver.BotCheckError, match="expired"):
        resolver._extract_sync("https://youtu.be/x")
    assert ytcookies.state["expired"] is True
