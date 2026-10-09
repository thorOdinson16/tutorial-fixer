import httpx
import pytest

from trf import webfetch
from trf.webfetch import FetchError, fetch_markdown


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "not a url",
        "http://127.0.0.1:8000/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.1.2.3/",
        "http://[::1]/",
    ],
)
def test_unsafe_or_malformed_addresses_are_refused(url):
    with pytest.raises(FetchError):
        fetch_markdown(url)


def _client_factory(handler):
    real = httpx.Client

    def make(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)

    return make


def test_html_page_becomes_markdown_with_a_pin(monkeypatch):
    monkeypatch.setattr(webfetch, "_check_host", lambda host: None)
    page = "<title>Groupby</title><h1>Groupby</h1><pre>import pandas as pd\nx = 1</pre>"

    def handler(request):
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    monkeypatch.setattr(webfetch.httpx, "Client", _client_factory(handler))
    md, title = fetch_markdown(
        "https://pandas.pydata.org/pandas-docs/version/1.5.3/user_guide/x.html"
    )
    assert title == "Groupby"
    assert "pip install pandas==1.5.3" in md and "```python" in md


def test_redirect_to_a_private_address_is_refused(monkeypatch):
    def only_public(host):
        if host != "example.com":
            raise FetchError("That address is on a private network, so it is not fetched.")

    monkeypatch.setattr(webfetch, "_check_host", only_public)

    def handler(request):
        return httpx.Response(302, headers={"location": "http://192.168.0.1/admin"})

    monkeypatch.setattr(webfetch.httpx, "Client", _client_factory(handler))
    with pytest.raises(FetchError, match="private network"):
        fetch_markdown("https://example.com/")


def test_non_page_content_and_errors_are_refused(monkeypatch):
    monkeypatch.setattr(webfetch, "_check_host", lambda host: None)
    answers = iter(
        [
            httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}),
            httpx.Response(404, text="gone"),
        ]
    )
    monkeypatch.setattr(webfetch.httpx, "Client", _client_factory(lambda request: next(answers)))
    with pytest.raises(FetchError, match="image/png"):
        fetch_markdown("https://example.com/a.png")
    with pytest.raises(FetchError, match="404"):
        fetch_markdown("https://example.com/missing")


def test_markdown_is_passed_through(monkeypatch):
    monkeypatch.setattr(webfetch, "_check_host", lambda host: None)
    body = "# Title\n\n```python\nx = 1\n```\n"

    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/markdown"})

    monkeypatch.setattr(webfetch.httpx, "Client", _client_factory(handler))
    md, title = fetch_markdown("https://example.com/t.md")
    assert md == body and title == "Title"


def test_pin_follows_the_address_that_was_asked_for_even_after_a_redirect(monkeypatch):
    monkeypatch.setattr(webfetch, "_check_host", lambda host: None)

    def handler(request):
        if request.url.host == "docs.pydantic.dev":
            return httpx.Response(301, headers={"location": "https://pydantic.dev/docs/x/"})
        return httpx.Response(
            200, text="<h1>T</h1><pre>x = 1</pre>", headers={"content-type": "text/html"}
        )

    monkeypatch.setattr(webfetch.httpx, "Client", _client_factory(handler))
    md, _ = fetch_markdown("https://docs.pydantic.dev/1.10/usage/settings/")
    assert "pip install pydantic==1.10.13" in md
