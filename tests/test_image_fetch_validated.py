import io
import sys

from PIL import Image

import image_fetch


class Response:
    status_code = 200
    headers = {"content-type": "image/jpeg"}

    def __init__(self, content):
        self.content = content

    def iter_content(self, _size):
        yield self.content

    def close(self):
        pass


def test_baixa_valida_converte_e_calcula_hash(tmp_path, monkeypatch):
    raw = io.BytesIO()
    Image.new("RGB", (320, 180), (30, 80, 160)).save(raw, format="JPEG")
    fake = type("Requests", (), {"get": staticmethod(lambda *a, **k: Response(raw.getvalue()))})
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setattr(image_fetch.socket, "getaddrinfo", lambda *args, **kwargs: [
        (image_fetch.socket.AF_INET, image_fetch.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
    ])
    dest = tmp_path / "image.png"
    result = image_fetch.download_image_validated("https://cdn.example/image.jpg", dest)
    assert result["ok"] and result["width"] == 320 and result["height"] == 180
    assert len(result["sha256"]) == 64 and dest.read_bytes().startswith(b"\x89PNG")


def test_recusa_endereco_privado(tmp_path):
    result = image_fetch.download_image_validated("http://127.0.0.1/image.png", tmp_path / "x.png")
    assert result["ok"] is False and "privada" in result["error"]


def test_recusa_redirect_para_rede_privada(tmp_path, monkeypatch):
    raw = io.BytesIO()
    Image.new("RGB", (100, 100), (0, 0, 0)).save(raw, format="PNG")
    response = Response(raw.getvalue())
    response.url = "http://192.168.1.10/internal.png"
    fake = type("Requests", (), {"get": staticmethod(lambda *a, **k: response)})
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setattr(image_fetch.socket, "getaddrinfo", lambda *args, **kwargs: [
        (image_fetch.socket.AF_INET, image_fetch.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
    ])
    result = image_fetch.download_image_validated("https://cdn.example/image.png", tmp_path / "x.png")
    assert result["ok"] is False and "privada" in result["error"]
