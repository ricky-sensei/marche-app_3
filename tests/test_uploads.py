import re
from pathlib import Path

import pytest

from marche.uploads import delete_image, detect_media_type, save_image


def uploaded_files(app):
    return sorted(Path(app.config["UPLOAD_FOLDER"]).glob("*"))


# --- detect_media_type --------------------------------------------------


def test_JPEGを判定できる(jpeg_bytes):
    assert detect_media_type(jpeg_bytes) == "image/jpeg"


def test_PNGを判定できる(png_bytes):
    assert detect_media_type(png_bytes) == "image/png"


def test_WebPを判定できる(webp_bytes):
    assert detect_media_type(webp_bytes) == "image/webp"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xd8",  # 短すぎる
        b"hello",
        b"<html><script>alert(1)</script></html>",
        b"GIF89a\x00\x00",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",  # RIFF だが WebP ではない
        b"MZ\x90\x00",  # 実行ファイル
    ],
)
def test_画像でない中身はNone(data):
    assert detect_media_type(data) is None


# --- save_image / delete_image -------------------------------------------


@pytest.mark.parametrize(
    "media_type, extension",
    [("image/jpeg", ".jpg"), ("image/png", ".png"), ("image/webp", ".webp")],
)
def test_UUIDのファイル名で保存される(app, jpeg_bytes, media_type, extension):
    filename = save_image(jpeg_bytes, media_type)
    assert re.fullmatch(rf"[0-9a-f]{{32}}\{extension}", filename)
    assert (Path(app.config["UPLOAD_FOLDER"]) / filename).read_bytes() == jpeg_bytes


def test_保存のたびに違うファイル名になる(app, jpeg_bytes):
    assert save_image(jpeg_bytes, "image/jpeg") != save_image(jpeg_bytes, "image/jpeg")
    assert len(uploaded_files(app)) == 2


def test_写真を消せる(app, jpeg_bytes):
    filename = save_image(jpeg_bytes, "image/jpeg")
    delete_image(filename)
    assert uploaded_files(app) == []


@pytest.mark.parametrize("filename", ["no-such-file.jpg", "", None])
def test_無い写真を消してもエラーにならない(app, filename):
    delete_image(filename)


def test_保存先の外のファイルは消せない(app, tmp_path, jpeg_bytes):
    save_image(jpeg_bytes, "image/jpeg")  # 保存先のフォルダーを作る
    outside = tmp_path / "outside.txt"
    outside.write_text("残るべきファイル")
    delete_image("../outside.txt")
    assert outside.exists()
