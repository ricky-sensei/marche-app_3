from collections import Counter
from pathlib import Path

import pytest

from marche.extensions import db
from marche.models import Order, Product, Role, User
from marche.uploads import save_image

# --- 一覧 ---------------------------------------------------------------


@pytest.fixture
def hidden_product(producer):
    product = Product(
        producer_id=producer.id,
        name="試作中の干し野菜セット",
        price=600,
        stock=5,
        is_published=False,
    )
    db.session.add(product)
    db.session.commit()
    return product


def test_一覧には公開中の商品だけが出る(client, product, other_product, hidden_product):
    text = client.get("/").text
    assert product.name in text
    assert other_product.name in text
    assert hidden_product.name not in text


def test_一覧に価格と生産者名と在庫が出る(client, product):
    product.price = 1200
    db.session.commit()
    text = client.get("/").text
    assert "1,200円" in text
    assert "高原ファーム" in text
    assert "在庫 10" in text


def test_一覧で在庫0は売り切れ(client, product):
    product.stock = 0
    db.session.commit()
    text = client.get("/").text
    assert "売り切れ" in text
    assert "在庫 0" not in text


def test_一覧の在庫は支払い待ちの分だけ減る(client, customer, product, make_order):
    make_order(customer, [(product, 3)])
    assert "在庫 7" in client.get("/").text


def test_一覧は新しい順(client, product, other_product):
    text = client.get("/").text
    assert text.index(other_product.name) < text.index(product.name)


def test_商品が無いときは案内が出る(client):
    assert "いま出品されている商品はありません" in client.get("/").text


def test_一覧に写真が出る(client, product, jpeg_bytes):
    product.image_filename = save_image(jpeg_bytes, "image/jpeg")
    db.session.commit()
    assert f"uploads/{product.image_filename}" in client.get("/").text


# --- 詳細 ---------------------------------------------------------------


def test_公開中の商品の詳細は未ログインでも見られる(client, product):
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert product.name in response.text
    assert "300円" in response.text
    assert "高原ファーム" in response.text
    assert "いま買える数: 10" in response.text
    assert "朝採れです" in response.text


def test_非公開の詳細は未ログインには404(client, hidden_product):
    assert client.get(f"/products/{hidden_product.id}").status_code == 404


def test_非公開の詳細は購入者には404(client, customer, login, hidden_product):
    login(customer)
    assert client.get(f"/products/{hidden_product.id}").status_code == 404


def test_非公開の詳細は他の生産者には404(client, other_producer, login, hidden_product):
    login(other_producer)
    assert client.get(f"/products/{hidden_product.id}").status_code == 404


def test_非公開の詳細は出品した生産者には見られる(
    client, producer, login, hidden_product
):
    login(producer)
    response = client.get(f"/products/{hidden_product.id}")
    assert response.status_code == 200
    assert "非公開（購入者には表示されません）" in response.text


def test_非公開の詳細は管理者には見られる(client, admin, login, hidden_product):
    login(admin)
    assert client.get(f"/products/{hidden_product.id}").status_code == 200


def test_存在しない商品の詳細は404(client):
    assert client.get("/products/9999").status_code == 404


def test_詳細で在庫0は売り切れ(client, product):
    product.stock = 0
    db.session.commit()
    text = client.get(f"/products/{product.id}").text
    assert "売り切れ" in text
    assert "いま買える数" not in text


def test_紹介文は改行を保ちエスケープされる(client, product):
    product.description = "1行目\n<script>alert(1)</script>"
    db.session.commit()
    text = client.get(f"/products/{product.id}").text
    assert "white-space:pre-line" in text
    assert "1行目\n&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert(1)</script>" not in text


# --- flask seed ---------------------------------------------------------


def run_seed(app, *args, input=None):
    return app.test_cli_runner().invoke(args=["seed", *args], input=input)


def test_seedで5人と8商品が入る(app):
    result = run_seed(app, "--yes")
    assert result.exit_code == 0, result.output
    assert "ユーザー5人、商品8件を入れました" in result.output
    users = db.session.scalars(db.select(User)).all()
    assert Counter(user.role for user in users) == {
        Role.ADMIN: 1,
        Role.PRODUCER: 2,
        Role.CUSTOMER: 2,
    }
    products = db.session.scalars(db.select(Product)).all()
    assert len(products) == 8
    assert sum(p.is_published for p in products) == 7
    pickles = next(p for p in products if p.name == "地熱野菜のピクルス")
    assert pickles.stock == 0
    assert pickles.producer.name == "岩手山麓工房"


def test_seedは何度実行しても同じ件数(app):
    run_seed(app, "--yes")
    run_seed(app, "--yes")
    db.session.expire_all()
    assert len(db.session.scalars(db.select(User)).all()) == 5
    assert len(db.session.scalars(db.select(Product)).all()) == 8


def test_seedは既存のデータを消す(app, customer, product, make_order, jpeg_bytes):
    product.image_filename = save_image(jpeg_bytes, "image/jpeg")
    db.session.commit()
    make_order(customer, [(product, 1)])
    run_seed(app, "--yes")
    db.session.expire_all()
    assert db.session.scalars(db.select(Order)).all() == []
    emails = {u.email for u in db.session.scalars(db.select(User))}
    assert "customer@example.com" not in emails
    # テストの「ほうれん草 1束」（300円）は消え、初期データの同名の商品（180円）だけが残る
    spinach = db.session.scalars(
        db.select(Product).filter_by(name="ほうれん草 1束")
    ).all()
    assert [p.price for p in spinach] == [180]
    assert list(Path(app.config["UPLOAD_FOLDER"]).glob("*")) == []  # 古い写真も消える


def test_seedのユーザーでログインできる(app, client):
    run_seed(app, "--yes")
    response = client.post(
        "/login", data={"email": "producer1@example.com", "password": "password"}
    )
    assert response.headers["Location"] == "/producer/products"


def test_seedは確認でnなら何も消さない(app, customer):
    result = run_seed(app, input="n\n")
    assert result.exit_code != 0
    db.session.expire_all()
    emails = [u.email for u in db.session.scalars(db.select(User))]
    assert emails == ["customer@example.com"]
