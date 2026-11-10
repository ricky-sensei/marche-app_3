from datetime import timedelta

import pytest
from sqlalchemy import event

from marche.extensions import db
from marche.models import OrderStatus, now_jst
from marche.services.inventory import (
    PENDING_MINUTES,
    available_stock,
    reserved_quantity,
)


def minutes_ago(minutes):
    return now_jst() - timedelta(minutes=minutes)


def test_押さえる時間は35分():
    assert PENDING_MINUTES == 35


def test_注文が無ければstockのまま(product):
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 10


def test_35分以内の支払い待ちは引く(customer, product, make_order):
    make_order(customer, [(product, 3)])
    assert reserved_quantity(product) == 3
    assert available_stock(product) == 7


def test_複数の注文の数量を合計する(customer, producer, product, make_order):
    make_order(customer, [(product, 3)])
    make_order(producer, [(product, 2)])  # 生産者も購入者として買える
    assert available_stock(product) == 5


@pytest.mark.parametrize(
    "minutes, reserved",
    [(0, 4), (PENDING_MINUTES - 1, 4), (PENDING_MINUTES + 1, 0), (60 * 24, 0)],
)
def test_PENDING_MINUTES分を過ぎた支払い待ちは数えない(
    customer, product, make_order, minutes, reserved
):
    make_order(customer, [(product, 4)], created_at=minutes_ago(minutes))
    assert reserved_quantity(product) == reserved


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_支払い済みとキャンセルは引かない(customer, product, make_order, status):
    # 支払い済みの分は、支払い確定時に stock から減らしてあるので、ここで引くと二重になる
    make_order(customer, [(product, 3)], status=status)
    assert available_stock(product) == 10


def test_他の商品の注文は数えない(customer, product, other_product, make_order):
    make_order(customer, [(other_product, 5)])
    assert available_stock(product) == 10


def test_同じ注文の中の他の商品は数えない(customer, product, other_product, make_order):
    make_order(customer, [(product, 1), (other_product, 5)])
    assert reserved_quantity(product) == 1
    assert reserved_quantity(other_product) == 5


def test_0未満にはならない(customer, product, make_order):
    product.stock = 2
    db.session.commit()
    make_order(customer, [(product, 5)])
    assert available_stock(product) == 0


def test_在庫0なら0(product):
    product.stock = 0
    db.session.commit()
    assert available_stock(product) == 0


def test_数えるSQLは1回だけ(customer, product, other_product, make_order):
    make_order(customer, [(product, 1), (other_product, 2)])
    make_order(customer, [(product, 3)])
    make_order(customer, [(product, 2)], status=OrderStatus.PAID)
    product_stock = product.stock  # 属性を先に読み込んで、数える対象から外す

    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        result = available_stock(product)
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert result == product_stock - 4
    assert len(statements) == 1
    assert "sum" in statements[0].lower()
