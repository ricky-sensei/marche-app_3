import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product
from marche.services.inventory import (
    MAX_QUANTITY,
    OrderError,
    available_stock,
    create_order,
)


def test_注文は支払い待ちで作られる(customer, product, make_order):
    order = make_order(customer, [(product, 2)])
    assert order.status == OrderStatus.PENDING
    assert order.total_amount == 600
    assert order.paid_at is None
    assert order.stripe_checkout_session_id is None


@pytest.mark.parametrize(
    "status, label",
    [
        (OrderStatus.PENDING, "支払い待ち"),
        (OrderStatus.PAID, "支払い済み"),
        (OrderStatus.CANCELLED, "キャンセル"),
    ],
)
def test_状態のラベルは日本語(customer, product, make_order, status, label):
    assert make_order(customer, [(product, 1)], status=status).status_label == label


def test_リレーションでたどれる(customer, product, other_product, make_order):
    order = make_order(customer, [(product, 1), (other_product, 2)])
    assert customer.orders == [order]
    assert order.user == customer
    assert [item.product for item in order.items] == [product, other_product]
    assert product.order_items == [order.items[0]]
    assert order.items[0].order == order


def test_注文を消すと明細も消える(customer, product, make_order):
    order = make_order(customer, [(product, 1)])
    db.session.delete(order)
    db.session.commit()
    assert db.session.scalars(db.select(OrderItem)).all() == []


def test_商品が変わっても明細は注文時点のまま(customer, product, make_order):
    order = make_order(customer, [(product, 2)])
    product.name = "ほうれん草 2束"
    product.price = 999
    db.session.commit()
    db.session.refresh(order)
    (item,) = order.items
    assert (item.product_name, item.unit_price, item.subtotal) == (
        "ほうれん草 1束",
        300,
        600,
    )
    assert order.total_amount == 600


def test_Checkout_SessionのIDは重複できない(customer, product, make_order):
    make_order(customer, [(product, 1)], session_id="cs_test_1")
    with pytest.raises(IntegrityError):
        make_order(customer, [(product, 1)], session_id="cs_test_1")
    db.session.rollback()


def test_Checkout_SessionのIDが無い注文は複数あってよい(customer, product, make_order):
    make_order(customer, [(product, 1)])
    make_order(customer, [(product, 1)])
    assert len(db.session.scalars(db.select(Order)).all()) == 2


# --- create_order --------------------------------------------------------


def count_orders():
    db.session.expire_all()
    orders = db.session.scalars(db.select(Order)).all()
    items = db.session.scalars(db.select(OrderItem)).all()
    return len(orders), len(items)


def assert_order_error(user, cart, message=None):
    """OrderError になり、注文も明細も1件も作られないことを確かめる"""
    before = count_orders()
    with pytest.raises(OrderError) as excinfo:
        create_order(user, cart)
    assert count_orders() == before
    if message is not None:
        assert message in excinfo.value.message
    return excinfo.value


def test_買える数は10まで():
    assert MAX_QUANTITY == 10


def test_合計はDBの価格と数量で計算される(customer, product, other_product):
    other_product.price = 1200
    db.session.commit()
    order = create_order(customer, {product.id: 2, other_product.id: 3})
    assert order.total_amount == 300 * 2 + 1200 * 3
    assert order.status == OrderStatus.PENDING
    assert order.user_id == customer.id
    assert [
        (i.product_id, i.product_name, i.unit_price, i.quantity, i.subtotal)
        for i in order.items
    ] == [
        (product.id, "ほうれん草 1束", 300, 2, 600),
        (other_product.id, "りんご 3個", 1200, 3, 3600),
    ]
    assert count_orders() == (1, 2)


def test_注文した分だけ買える数が減りstockは変わらない(customer, product):
    create_order(customer, {product.id: 4})
    db.session.refresh(product)
    assert product.stock == 10  # 在庫を減らすのは支払いが確定したとき
    assert available_stock(product) == 6


def test_いま買える数ちょうどなら注文できる(customer, product):
    product.stock = 3
    db.session.commit()
    create_order(customer, {product.id: 3})
    assert count_orders() == (1, 1)


def test_MAX_QUANTITYちょうどなら注文できる(customer, product):
    create_order(customer, {product.id: MAX_QUANTITY})
    assert count_orders() == (1, 1)


def test_キーが文字列のカートでも注文できる(customer, product):
    # セッションに保存したカートは JSON になり、キーが文字列になる
    order = create_order(customer, {str(product.id): 2})
    assert order.total_amount == 600


def test_注文のあとで価格が変わっても注文は変わらない(customer, product):
    order = create_order(customer, {product.id: 2})
    product.price = 999
    db.session.commit()
    db.session.refresh(order)
    assert order.total_amount == 600
    assert order.items[0].unit_price == 300


def test_在庫を超えるとOrderError(customer, product):
    product.stock = 5
    db.session.commit()
    assert_order_error(
        customer,
        {product.id: 6},
        "「ほうれん草 1束」は在庫が足りません（いま買える数: 5）",
    )


def test_支払い待ちに押さえられて足りなければOrderError(
    customer, producer, product, make_order
):
    make_order(producer, [(product, 7)])  # いま買える数は 3
    assert_order_error(customer, {product.id: 4}, "いま買える数: 3")


def test_在庫0は売り切れでOrderError(customer, product):
    product.stock = 0
    db.session.commit()
    assert_order_error(customer, {product.id: 1}, "「ほうれん草 1束」は売り切れです")


def test_非公開の商品はOrderError(customer, product):
    product.is_published = False
    db.session.commit()
    error = assert_order_error(customer, {product.id: 1}, "購入できない商品")
    assert "ほうれん草" not in error.message  # 非公開の商品の名前は出さない


def test_存在しない商品はOrderError(customer):
    assert_order_error(customer, {9999: 1}, "購入できない商品")


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1])
def test_数量が範囲外ならOrderError(customer, product, quantity):
    assert_order_error(customer, {product.id: quantity}, "数量は1から10の間")


@pytest.mark.parametrize("quantity", ["3", True, 1.5, None])
def test_数量が整数でなければOrderError(customer, product, quantity):
    assert_order_error(
        customer, {product.id: quantity}, "カートの中身が正しくありません"
    )


def test_商品IDが整数でなければOrderError(customer):
    assert_order_error(customer, {"abc": 1}, "カートの中身が正しくありません")


def test_空のカートはOrderError(customer):
    assert_order_error(customer, {}, "カートが空です")


def test_1つでも駄目なら他の商品の注文も作られない(customer, product, other_product):
    other_product.stock = 1
    db.session.commit()
    assert_order_error(customer, {product.id: 2, other_product.id: 5})
    assert available_stock(product) == 10


def test_OrderErrorのメッセージは画面に出せる日本語(customer):
    error = assert_order_error(customer, {})
    assert str(error) == error.message == "カートが空です"


def test_生産者も購入者として注文できる(producer, other_product):
    order = create_order(producer, {other_product.id: 1})
    assert order.user_id == producer.id
    assert isinstance(order.items[0].product, Product)


# --- カートと注文の画面 -----------------------------------------------------


def test_カートに入れて注文するとDBの価格で合計される(
    client, customer, login, product, other_product
):
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": 2})
    client.post(f"/cart/add/{other_product.id}", data={"quantity": 3})
    # カートに入れたあとで価格が変わった。注文には今の DB の価格が使われる
    product.price = 500
    other_product.price = 1200
    db.session.commit()

    response = client.post(
        "/orders/",
        data={"total_amount": "1", "price": "1", "unit_price": "1"},  # 無視される
    )

    db.session.expire_all()
    (order,) = db.session.scalars(db.select(Order)).all()
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{order.id}"
    assert order.user_id == customer.id
    assert order.total_amount == 500 * 2 + 1200 * 3
    assert sorted(item.unit_price for item in order.items) == [500, 1200]
    with client.session_transaction() as sess:
        assert sess.get("cart") is None


def test_未ログインでPOST_ordersするとログイン画面へ移り注文は作られない(
    client, product
):
    client.post(f"/cart/add/{product.id}", data={"quantity": 1})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert count_orders() == (0, 0)


def test_他人の注文詳細は403(
    client, customer, other_producer, product, make_order, login
):
    order = make_order(customer, [(product, 1)])
    login(other_producer)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_未ログインのカートにはログインして注文するリンクが出る(client, product):
    client.post(f"/cart/add/{product.id}", data={"quantity": 1})
    text = client.get("/cart/").text
    assert "ログインして注文する" in text
    assert 'href="/login?next=/cart/"' in text
