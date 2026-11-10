import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus


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
