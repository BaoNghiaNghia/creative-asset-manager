"""Phase 4C.3F network isolation tests: NO actual DNS/TLS or external requests."""
from __future__ import annotations

import io
import socket

import pytest
from PIL import Image

from app.modules.agent_harness import fenced_source_capture as source


def _jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (4, 5), (30, 70, 110)).save(stream, "JPEG")
    return stream.getvalue()


def test_dns_rejects_private_and_mixed_addresses_without_connect(monkeypatch):
    def records(*ips):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             (ip, 443)) for ip in ips
        ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: records("127.0.0.1"))
    with pytest.raises(ValueError, match="dns_not_public"):
        source._resolve_public_address("i.etsystatic.com")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: records("8.8.8.8", "10.2.3.4"))
    with pytest.raises(ValueError, match="dns_not_public"):
        source._resolve_public_address("i.etsystatic.com")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: records("8.8.8.8"))
    assert source._resolve_public_address("i.etsystatic.com") == "8.8.8.8"


def test_client_supplied_url_cannot_redirect_fetch_into_internal_host(monkeypatch):
    def no_dns(host):
        raise AssertionError("DNS must not be attempted for disallowed source")
    monkeypatch.setattr(source, "_resolve_public_address", no_dns)
    for url in (
        "http://i.etsystatic.com/photo.jpg",
        "https://127.0.0.1/private",
        "https://i.etsystatic.com.evil.example/photo.jpg",
        "https://i.etsystatic.com:8080/private",
        "https://i.etsystatic.com/photo.jpg?redirect=https://127.0.0.1/",
        "https://user@i.etsystatic.com/photo.jpg",
        "https://i.etsystatic.com/a\r\nHost:127.0.0.1",
    ):
        with pytest.raises(ValueError):
            source._fetch_etsy_source(url)


def test_https_get_uses_public_pinned_ip_sni_bounded_body_and_rejects_redirects(monkeypatch):
    original = _jpeg()
    calls = []
    state = {"status": 200, "mime": "image/jpeg", "length": str(len(original)),
             "encoding": "identity", "raw": original}

    class FakeSocket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            calls.append(("socket_timeout", value))
        def sendall(self, raw):
            calls.append(("request", raw))

    class FakeTLS(FakeSocket):
        pass

    class Context:
        def wrap_socket(self, conn, server_hostname):
            calls.append(("tls_sni", server_hostname))
            return FakeTLS()

    class Response:
        def __init__(self, conn):
            self.raw = state["raw"]
            self.position = 0
            self.status = state["status"]
        def begin(self):
            pass
        def getheader(self, header):
            return {
                "content-type": state["mime"],
                "content-length": state["length"],
                "content-encoding": state["encoding"],
            }.get(header)
        def read1(self, amount):
            chunk = self.raw[self.position:self.position + amount]
            self.position += len(chunk)
            return chunk

    monkeypatch.setattr(source, "_resolve_public_address", lambda host: "8.8.8.8")
    monkeypatch.setattr(source.socket, "create_connection", lambda target, timeout: (
        calls.append(("connect", target, timeout)) or FakeSocket()
    ))
    monkeypatch.setattr(source.ssl, "create_default_context", Context)
    monkeypatch.setattr(source.http.client, "HTTPResponse", Response)
    url = "https://i.etsystatic.com/1789/r/il/photo.jpg"
    assert source._fetch_etsy_source(url) == original
    assert ("connect", ("8.8.8.8", 443), 5.0) in calls
    assert ("tls_sni", "i.etsystatic.com") in calls
    request = next(record[1] for record in calls if record[0] == "request").decode()
    assert "GET /1789/r/il/photo.jpg HTTP/1.1" in request
    assert "Host: i.etsystatic.com" in request
    assert "Connection: close" in request

    for modifications, expected in [
        ({"status": 302}, "unexpected_http_status"),
        ({"status": 308}, "unexpected_http_status"),
        ({"mime": "text/html"}, "unexpected_content_type"),
        ({"length": "22000001"}, "source_content_length_invalid"),
        ({"encoding": "gzip"}, "compressed_http_body_forbidden"),
        ({"raw": b"<html>oops</html>", "length": "17"}, "source_image_invalid"),
    ]:
        state.update(status=200, mime="image/jpeg", length=str(len(original)),
                     encoding="identity", raw=original)
        state.update(modifications)
        with pytest.raises(ValueError, match=expected):
            source._fetch_etsy_source(url)


def test_source_jpeg_decoder_rejects_fake_image_and_wrong_mime():
    jpg = _jpeg()
    assert source._verify_source_image(jpg, "image/jpeg")
    assert not source._verify_source_image(jpg, "image/png")
    assert not source._verify_source_image(b"not-an-image", "image/jpeg")
    assert not source._verify_source_image(b"", "image/jpeg")
