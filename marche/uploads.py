import uuid
from pathlib import Path

from flask import current_app

# 保存するファイルの拡張子は、ブラウザから来たファイル名ではなく、中身から決めた種類で決める
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def detect_media_type(data: bytes) -> str | None:
    """ファイルの先頭のバイト（マジックナンバー）で JPEG / PNG / WebP を判定する。どれでもなければ None"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _upload_folder() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"])


def save_image(data: bytes, media_type: str) -> str:
    """UUID のファイル名で保存して、そのファイル名を返す（元のファイル名は使わない）"""
    filename = f"{uuid.uuid4().hex}{EXTENSIONS[media_type]}"
    folder = _upload_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    return filename


def delete_image(filename: str | None) -> None:
    """写真のファイルを消す。無くてもエラーにしない"""
    if not filename:
        return
    # 保存先の外を消さないように、名前の部分だけを使う
    (_upload_folder() / Path(filename).name).unlink(missing_ok=True)
