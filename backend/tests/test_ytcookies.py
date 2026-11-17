import pytest

from app.services.ytcookies import is_bot_check, validate

GOOD = (".youtube.com\tTRUE\t/\tTRUE\t1893456000\tPREF\tf6=40000000\n"
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\t__Secure-3PSID\tabc\n")


def test_validate_accepts_netscape_export_and_adds_header():
    out = validate(GOOD)
    assert out.startswith("# Netscape HTTP Cookie File\n") and "__Secure-3PSID" in out and out.endswith("\n")
    assert validate("# Netscape HTTP Cookie File\n" + GOOD).count("# Netscape") == 1
    assert "\r" not in validate(GOOD.replace("\n", "\r\n"))


@pytest.mark.parametrize("bad", [
    "hello world, these are not cookies",
    ".example.com\tTRUE\t/\tTRUE\t0\tname\tvalue\n",       # no YouTube cookies
    '{"cookies": [{"domain": ".youtube.com"}]}',            # JSON export, not Netscape
])
def test_validate_rejects_non_cookie_files(bad):
    with pytest.raises(ValueError):
        validate(bad)


def test_bot_check_detection():
    assert is_bot_check("[youtube] ZxeK4wG-gws: Sign in to confirm you’re not a bot. Use --cookies")
    assert is_bot_check("Sign in to confirm you're not a bot")
    assert not is_bot_check("Video unavailable")
