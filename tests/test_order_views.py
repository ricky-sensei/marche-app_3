import re
from datetime import datetime

import pytest

from marche.extensions import db
from marche.models import JST, Order, OrderStatus


def jst(*args):
    """日本時間の日時。DB にはタイムゾーンを外して保存するので、now_jst() と同じ形にする"""
    return datetime(*args, tzinfo=JST).replace(tzinfo=None)


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = {str(product_id): qty for product_id, qty in cart.items()}


def session_cart(client):
    with client.session_transaction() as sess:
        return sess.get("cart")


def all_orders():
    db.session.expire_all()
    return db.session.scalars(db.select(Order)).all()


# --- 注文を作る -----------------------------------------------------------


def test_未ログインでは注文できない(client, product):
    set_cart(client, {product.id: 1})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert all_orders() == []


def test_注文するとカートが空になり注文詳細へ移る(
    client, customer, login, product, other_product
):
    other_product.price = 1200
    db.session.commit()
    login(customer)
    set_cart(client, {product.id: 2, other_product.id: 3})
    response = client.post("/orders/")
    (order,) = all_orders()
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{order.id}"
    assert order.user_id == customer.id
    assert order.status == OrderStatus.PENDING
    assert order.total_amount == 300 * 2 + 1200 * 3  # DB の価格 × 数量
    assert session_cart(client) is None


def test_フォームに金額を混ぜても無視される(client, customer, login, product):
    login(customer)
    set_cart(client, {product.id: 2})
    client.post(
        "/orders/",
        data={"price": "1", "unit_price": "1", "total_amount": "1", "subtotal": "1"},
    )
    (order,) = all_orders()
    assert order.total_amount == 600
    assert [item.unit_price for item in order.items] == [300]


def test_在庫が足りなければカートに戻り注文は作られない(
    client, customer, producer, login, product, make_order
):
    login(customer)
    set_cart(client, {product.id: 5})
    make_order(producer, [(product, 8)])  # カートに入れたあとで、ほかの人が買った
    response = client.post("/orders/", follow_redirects=True)
    assert response.request.path == "/cart/"
    assert "「ほうれん草 1束」は在庫が足りません（いま買える数: 2）" in response.text
    assert len(all_orders()) == 1  # ほかの人の注文だけ
    assert session_cart(client) == {str(product.id): 5}  # カートは残る


def test_非公開になった商品があると注文できない(client, customer, login, product):
    login(customer)
    set_cart(client, {product.id: 1})
    product.is_published = False
    db.session.commit()
    response = client.post("/orders/", follow_redirects=True)
    assert response.request.path == "/cart/"
    assert all_orders() == []


def test_空のカートでは注文できない(client, customer, login):
    login(customer)
    response = client.post("/orders/", follow_redirects=True)
    assert "カートが空です" in response.text
    assert all_orders() == []


def test_注文はGETでは作れない(client, customer, login, product):
    login(customer)
    set_cart(client, {product.id: 1})
    assert client.get("/orders/").status_code == 405
    assert all_orders() == []


def test_ログイン中のカートには注文ボタンがある(client, customer, login, product):
    login(customer)
    set_cart(client, {product.id: 1})
    text = client.get("/cart/").text
    assert 'action="/orders/"' in text
    assert "ログインして注文する" not in text


def test_カートから注文詳細まで通して使える(client, customer, product):
    # 未ログインでカートに入れ、ログインしてカートに戻り、注文する
    client.post(f"/cart/add/{product.id}", data={"quantity": 3})
    response = client.post(
        "/login?next=/cart/", data={"email": customer.email, "password": "password"}
    )
    assert response.headers["Location"] == "/cart/"
    response = client.post("/orders/", follow_redirects=True)
    assert "注文を受け付けました" in response.text
    assert "支払い待ち" in response.text
    assert "900円" in response.text


# --- 注文詳細 -------------------------------------------------------------


@pytest.fixture
def order(customer, product, make_order):
    return make_order(customer, [(product, 2)], created_at=jst(2026, 11, 14, 10, 5))


def test_本人は注文詳細を見られる(client, customer, login, order):
    login(customer)
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 200
    text = response.text
    assert f"注文 #{order.id}" in text
    assert "支払い待ち" in text
    assert "ほうれん草 1束" in text
    assert "300円" in text
    assert "600円" in text
    assert "11月14日(土) 10:05" in text


def test_注文詳細は注文時点の商品名と単価のまま(
    client, customer, login, order, product
):
    product.name = "ほうれん草 2束"
    product.price = 999
    db.session.commit()
    login(customer)
    text = client.get(f"/orders/{order.id}").text
    assert "ほうれん草 1束" in text
    assert "999円" not in text


def test_他の購入者の注文詳細は403(client, login, order):
    from conftest import _make_user

    from marche.models import Role

    other = _make_user(Role.CUSTOMER, "other-customer@example.com", "別の購入者")
    login(other)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_生産者でも他人の注文詳細は403(client, producer, login, order):
    # order は product（producer の商品）の注文だが、出品者でも注文者でなければ見られない
    login(producer)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_管理者は注文詳細を見られる(client, admin, login, order):
    login(admin)
    assert client.get(f"/orders/{order.id}").status_code == 200


def test_存在しない注文は404(client, customer, login):
    login(customer)
    assert client.get("/orders/9999").status_code == 404


def test_未ログインで注文詳細を開くとログイン画面へ(client, order):
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 302
    assert re.search(r"/login\?next=", response.headers["Location"])


# --- 日付の表示 -----------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (jst(2026, 11, 14, 10, 0), "11月14日(土) 10:00"),
        (jst(2026, 11, 16, 9, 5), "11月16日(月) 09:05"),
        (jst(2027, 1, 3, 23, 59), "1月3日(日) 23:59"),
    ],
)
def test_日付は日本語の形式で表示する(app, value, expected):
    assert app.jinja_env.filters["datetime_jp"](value) == expected
