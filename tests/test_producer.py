import io
import re
from datetime import timedelta
from pathlib import Path

import pytest

from marche.extensions import db
from marche.models import Product, now_jst
from marche.uploads import save_image


def product_data(**overrides):
    """登録・編集フォームに送る値。公開するにチェックを付けた状態"""
    data = {
        "name": "トマト 1袋",
        "price": "500",
        "stock": "20",
        "description": "甘いです",
        "is_published": "y",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def all_products():
    db.session.expire_all()
    return db.session.scalars(db.select(Product)).all()


# --- 一覧 ---------------------------------------------------------------


def test_生産者は出品管理を開ける(client, producer, login):
    login(producer)
    response = client.get("/producer/products")
    assert response.status_code == 200
    assert "出品管理" in response.text


def test_一覧には自分の商品だけが出る(client, producer, login, product, other_product):
    login(producer)
    text = client.get("/producer/products").text
    assert product.name in text
    assert "300円" in text
    assert "公開中" in text
    assert other_product.name not in text


def test_商品が無いときは案内が出る(client, producer, login):
    login(producer)
    assert "商品がまだありません" in client.get("/producer/products").text


# --- 登録 ---------------------------------------------------------------


def test_商品を登録できる(client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data())
    assert response.status_code == 302
    assert response.headers["Location"] == "/producer/products"
    (created,) = all_products()
    assert created.producer_id == producer.id
    assert (created.name, created.price, created.stock) == ("トマト 1袋", 500, 20)
    assert created.is_published is True
    assert created.image_filename is None


def test_公開するに印がなければ非公開で登録される(client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(is_published=None))
    (created,) = all_products()
    assert created.is_published is False


def test_フォームにproducer_idを混ぜても無視される(
    client, producer, other_producer, login
):
    login(producer)
    client.post(
        "/producer/products/new", data=product_data(producer_id=other_producer.id)
    )
    (created,) = all_products()
    assert created.producer_id == producer.id


def test_登録画面が開ける(client, producer, login):
    login(producer)
    response = client.get("/producer/products/new")
    assert response.status_code == 200
    assert "商品の登録" in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "あ" * 51},
        {"price": ""},
        {"price": "49"},
        {"price": "100001"},
        {"price": "abc"},
        {"price": "300.5"},
        {"stock": ""},
        {"stock": "-1"},
        {"stock": "1000"},
        {"stock": "abc"},
        {"description": "あ" * 2001},
    ],
)
def test_不正な入力は422で保存されない(client, producer, login, overrides):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(**overrides))
    assert response.status_code == 422
    assert all_products() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "あ" * 50},
        {"price": "50"},
        {"price": "100000"},
        {"stock": "0"},
        {"stock": "999"},
        {"description": ""},
        {"description": "あ" * 2000},
    ],
)
def test_境界の値は登録できる(client, producer, login, overrides):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(**overrides))
    assert response.status_code == 302
    assert len(all_products()) == 1


def test_入力エラーは日本語で表示される(client, producer, login):
    login(producer)
    text = client.post("/producer/products/new", data=product_data(price="49")).text
    assert "価格は50円から100,000円の間で入力してください" in text
    text = client.post("/producer/products/new", data=product_data(stock="abc")).text
    assert "整数を入力してください" in text


# --- 編集 ---------------------------------------------------------------


def test_編集画面に今の値が入っている(client, producer, login, product):
    login(producer)
    response = client.get(f"/producer/products/{product.id}/edit")
    assert response.status_code == 200
    assert product.name in response.text


def test_商品を編集できる(client, producer, login, product):
    old = now_jst() - timedelta(days=1)
    product.updated_at = old
    db.session.commit()
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(
            name="ほうれん草 2束", price="550", stock="0", is_published=None
        ),
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert (product.name, product.price, product.stock) == ("ほうれん草 2束", 550, 0)
    assert product.is_published is False
    assert product.updated_at > old  # 更新のたびに変わる


def test_編集の不正な入力は422で変更されない(client, producer, login, product):
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit", data=product_data(price="1")
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.price == 300


# --- 公開の切り替え・削除 ----------------------------------------------------


def test_公開と非公開を切り替えられる(client, producer, login, product):
    login(producer)
    url = f"/producer/products/{product.id}/toggle"
    assert client.post(url).status_code == 302
    db.session.refresh(product)
    assert product.is_published is False
    client.post(url)
    db.session.refresh(product)
    assert product.is_published is True


def test_商品を削除できる(client, producer, login, product):
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 302
    assert all_products() == []


def test_切り替えと削除はGETでは動かない(client, producer, login, product):
    login(producer)
    assert client.get(f"/producer/products/{product.id}/toggle").status_code == 405
    assert client.get(f"/producer/products/{product.id}/delete").status_code == 405


# --- 権限 ---------------------------------------------------------------

OWN_URLS = [
    ("get", "/producer/products"),
    ("get", "/producer/products/new"),
    ("post", "/producer/products/new"),
]
ID_URLS = [
    ("get", "/producer/products/{id}/edit"),
    ("post", "/producer/products/{id}/edit"),
    ("post", "/producer/products/{id}/toggle"),
    ("post", "/producer/products/{id}/delete"),
]


@pytest.mark.parametrize("method, url", OWN_URLS + ID_URLS)
def test_購入者は生産者の画面を使えない(client, customer, login, product, method, url):
    login(customer)
    response = getattr(client, method)(url.format(id=product.id), data=product_data())
    assert response.status_code == 403


@pytest.mark.parametrize("method, url", OWN_URLS + ID_URLS)
def test_管理者は生産者の画面を使えない(client, admin, login, product, method, url):
    login(admin)
    response = getattr(client, method)(url.format(id=product.id), data=product_data())
    assert response.status_code == 403


@pytest.mark.parametrize("method, url", OWN_URLS + ID_URLS)
def test_未ログインはログイン画面へ移る(client, product, method, url):
    response = getattr(client, method)(url.format(id=product.id), data=product_data())
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("method, url", ID_URLS)
def test_他の生産者の商品は操作できない(
    client, producer, login, other_product, method, url
):
    login(producer)
    response = getattr(client, method)(
        url.format(id=other_product.id), data=product_data(name="乗っ取り")
    )
    assert response.status_code == 403
    db.session.refresh(other_product)
    assert other_product.name == "りんご 3個"
    assert other_product.is_published is True
    assert len(all_products()) == 1


@pytest.mark.parametrize("method, url", ID_URLS)
def test_存在しない商品は404(client, producer, login, method, url):
    login(producer)
    response = getattr(client, method)(url.format(id=9999), data=product_data())
    assert response.status_code == 404


# --- 写真 ---------------------------------------------------------------


def photo(data, filename="photo.jpg"):
    """フォームに送る写真（ファイル名つき）"""
    return (io.BytesIO(data), filename)


def uploaded_files(app):
    return sorted(Path(app.config["UPLOAD_FOLDER"]).glob("*"))


@pytest.fixture
def product_with_photo(app, producer, product, jpeg_bytes):
    product.image_filename = save_image(jpeg_bytes, "image/jpeg")
    db.session.commit()
    return product


def test_写真つきで登録できる(app, client, producer, login, jpeg_bytes):
    login(producer)
    response = client.post(
        "/producer/products/new",
        data=product_data(image=photo(jpeg_bytes, "元の名前.jpg")),
    )
    assert response.status_code == 302
    (created,) = all_products()
    # ファイル名はサーバーで付け直す（元の名前は使わない）
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", created.image_filename)
    (saved,) = uploaded_files(app)
    assert saved.name == created.image_filename
    assert saved.read_bytes() == jpeg_bytes


def test_写真を選ばなくても登録できる(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data())
    (created,) = all_products()
    assert created.image_filename is None
    assert uploaded_files(app) == []


def test_拡張子ではなく中身で種類が決まる(app, client, producer, login, png_bytes):
    login(producer)
    client.post(
        "/producer/products/new", data=product_data(image=photo(png_bytes, "a.jpg"))
    )
    (created,) = all_products()
    assert created.image_filename.endswith(".png")


@pytest.mark.parametrize(
    "content, filename",
    [
        (b"<html><script>alert(1)</script></html>", "evil.jpg"),  # 拡張子だけ画像
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "memo.txt"),  # 中身は画像だが拡張子が違う
        (b"GIF89a\x00\x00", "a.gif"),
        (b"", "empty.jpg"),
    ],
)
def test_画像でない写真は422で保存されない(
    app, client, producer, login, content, filename
):
    login(producer)
    response = client.post(
        "/producer/products/new", data=product_data(image=photo(content, filename))
    )
    assert response.status_code == 422
    assert "JPEG・PNG・WebP の画像を選んでください" in response.text
    assert all_products() == []
    assert uploaded_files(app) == []


def test_5MBを超える写真は422(app, client, producer, login, jpeg_bytes):
    login(producer)
    big = jpeg_bytes + b"\x00" * (5 * 1024 * 1024)
    response = client.post(
        "/producer/products/new", data=product_data(image=photo(big))
    )
    assert response.status_code == 422
    assert "写真は5MB以下にしてください" in response.text
    assert all_products() == []
    assert uploaded_files(app) == []


def test_6MBを超えるリクエストは413のエラーページ(
    app, client, producer, login, jpeg_bytes
):
    login(producer)
    huge = jpeg_bytes + b"\x00" * (6 * 1024 * 1024)
    response = client.post(
        "/producer/products/new", data=product_data(image=photo(huge))
    )
    assert response.status_code == 413
    assert "ファイルが大きすぎます" in response.text
    assert all_products() == []


def test_編集で写真を差し替えると古い写真が消える(
    app, client, producer, login, product_with_photo, png_bytes
):
    old = product_with_photo.image_filename
    login(producer)
    response = client.post(
        f"/producer/products/{product_with_photo.id}/edit",
        data=product_data(image=photo(png_bytes, "new.png")),
    )
    assert response.status_code == 302
    db.session.refresh(product_with_photo)
    new = product_with_photo.image_filename
    assert new != old
    assert new.endswith(".png")
    assert [f.name for f in uploaded_files(app)] == [new]


def test_編集で写真を選ばなければ今の写真のまま(
    app, client, producer, login, product_with_photo
):
    old = product_with_photo.image_filename
    login(producer)
    client.post(
        f"/producer/products/{product_with_photo.id}/edit",
        data=product_data(name="ほうれん草 2束"),
    )
    db.session.refresh(product_with_photo)
    assert product_with_photo.name == "ほうれん草 2束"
    assert product_with_photo.image_filename == old
    assert [f.name for f in uploaded_files(app)] == [old]


def test_編集で画像でない中身は422で今の写真が残る(
    app, client, producer, login, product_with_photo
):
    old = product_with_photo.image_filename
    login(producer)
    response = client.post(
        f"/producer/products/{product_with_photo.id}/edit",
        data=product_data(name="変更しない", image=photo(b"<html></html>", "x.jpg")),
    )
    assert response.status_code == 422
    db.session.refresh(product_with_photo)
    assert product_with_photo.name == "ほうれん草 1束"
    assert product_with_photo.image_filename == old
    assert [f.name for f in uploaded_files(app)] == [old]


def test_商品を削除すると写真も消える(app, client, producer, login, product_with_photo):
    login(producer)
    client.post(f"/producer/products/{product_with_photo.id}/delete")
    assert all_products() == []
    assert uploaded_files(app) == []


def test_編集画面に今の写真が表示される(client, producer, login, product_with_photo):
    login(producer)
    text = client.get(f"/producer/products/{product_with_photo.id}/edit").text
    assert f"uploads/{product_with_photo.image_filename}" in text
    assert 'enctype="multipart/form-data"' in text


def test_写真が無い商品の編集画面に写真は出ない(client, producer, login, product):
    login(producer)
    assert "今の写真" not in client.get(f"/producer/products/{product.id}/edit").text


def test_一覧に写真が表示される(client, producer, login, product_with_photo):
    login(producer)
    text = client.get("/producer/products").text
    assert f"uploads/{product_with_photo.image_filename}" in text


def test_他の生産者の商品に写真は送れない(
    app, client, producer, login, other_product, jpeg_bytes
):
    login(producer)
    response = client.post(
        f"/producer/products/{other_product.id}/edit",
        data=product_data(image=photo(jpeg_bytes)),
    )
    assert response.status_code == 403
    db.session.refresh(other_product)
    assert other_product.image_filename is None
    assert uploaded_files(app) == []


def test_他の生産者の商品を削除しても写真は消えない(
    app, client, producer, other_producer, login, jpeg_bytes
):
    other = Product(
        producer_id=other_producer.id,
        name="りんご 3個",
        price=300,
        stock=1,
        image_filename=save_image(jpeg_bytes, "image/jpeg"),
    )
    db.session.add(other)
    db.session.commit()
    login(producer)
    assert client.post(f"/producer/products/{other.id}/delete").status_code == 403
    assert len(uploaded_files(app)) == 1


# --- 注文がある商品の削除 ----------------------------------------------------


def test_注文がある商品は削除できない(
    app, client, producer, customer, login, product_with_photo, make_order
):
    make_order(customer, [(product_with_photo, 1)])
    login(producer)
    response = client.post(
        f"/producer/products/{product_with_photo.id}/delete", follow_redirects=True
    )
    assert response.status_code == 200
    assert "注文がある商品は削除できません。非公開にしてください" in response.text
    assert len(all_products()) == 1
    assert len(uploaded_files(app)) == 1  # 写真も残る


def test_キャンセルされた注文があっても削除できない(
    client, producer, customer, login, product, make_order
):
    from marche.models import OrderStatus

    make_order(customer, [(product, 1)], status=OrderStatus.CANCELLED)
    login(producer)
    client.post(f"/producer/products/{product.id}/delete")
    assert len(all_products()) == 1


def test_他の商品に注文があっても注文の無い商品は削除できる(
    client, producer, customer, login, product, make_order
):
    other = Product(producer_id=producer.id, name="にんじん 3本", price=200, stock=5)
    db.session.add(other)
    db.session.commit()
    make_order(customer, [(other, 1)])
    login(producer)
    client.post(f"/producer/products/{product.id}/delete")
    assert [p.name for p in all_products()] == ["にんじん 3本"]
