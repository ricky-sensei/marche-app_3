"""marche/services/payment.py そのもののテスト。本物の Stripe は呼ばず、_client を差し替える"""

import time
from types import SimpleNamespace

import pytest
import stripe

from marche import create_app
from marche.extensions import db
from marche.services import payment
from marche.services.payment import (
    CHECKOUT_EXPIRES_MINUTES,
    PaymentError,
    create_checkout_session,
    expire_session,
    retrieve_session,
)


class FakeSessions:
    """client.v1.checkout.sessions の代わり。渡された値を記録する"""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.error:
            raise self.error
        return SimpleNamespace(
            id="cs_test_123", url="https://checkout.stripe.com/c/pay/x"
        )

    def create(self, params):
        return self._record("create", params)

    def retrieve(self, session_id):
        return self._record("retrieve", session_id)

    def expire(self, session_id):
        return self._record("expire", session_id)


@pytest.fixture
def sessions(app, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_dummy"
    fake = FakeSessions()
    client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=fake))
    )
    monkeypatch.setattr(payment, "_client", lambda: client)
    return fake


@pytest.fixture
def order(customer, product, other_product, make_order):
    other_product.price = 1200
    db.session.commit()
    return make_order(customer, [(product, 2), (other_product, 3)])


def create_params(app, sessions, order):
    with app.test_request_context(base_url="https://marche.example.com"):
        result = create_checkout_session(order)
    ((name, params),) = sessions.calls
    assert name == "create"
    return result, params


def test_Sessionを作るとIDとURLを返す(app, sessions, order):
    result, _ = create_params(app, sessions, order)
    assert result == ("cs_test_123", "https://checkout.stripe.com/c/pay/x")


def test_line_itemsは明細の単価と数量で円のまま(app, sessions, order):
    _, params = create_params(app, sessions, order)
    assert params["mode"] == "payment"
    assert params["line_items"] == [
        {
            "quantity": 2,
            "price_data": {
                "currency": "jpy",
                "unit_amount": 300,  # JPY は100倍しない
                "product_data": {"name": "ほうれん草 1束"},
            },
        },
        {
            "quantity": 3,
            "price_data": {
                "currency": "jpy",
                "unit_amount": 1200,
                "product_data": {"name": "りんご 3個"},
            },
        },
    ]


def test_金額と商品名は注文明細から取る(app, sessions, order, product):
    # 注文のあとで商品が変わっても、Stripe には注文時点の値を渡す
    product.price = 999
    product.name = "変更後の名前"
    db.session.commit()
    _, params = create_params(app, sessions, order)
    first = params["line_items"][0]["price_data"]
    assert first["unit_amount"] == 300
    assert first["product_data"]["name"] == "ほうれん草 1束"


def test_metadataとメールとURLと期限(app, sessions, order, customer):
    before = int(time.time())
    _, params = create_params(app, sessions, order)
    assert params["metadata"] == {"order_id": str(order.id)}
    assert params["customer_email"] == customer.email
    # {CHECKOUT_SESSION_ID} は Stripe が置き換えるので、エスケープされずにそのまま入っている
    assert params["success_url"] == (
        f"https://marche.example.com/orders/{order.id}/complete"
        "?session_id={CHECKOUT_SESSION_ID}"
    )
    assert params["cancel_url"] == (
        f"https://marche.example.com/orders/{order.id}/cancel"
    )
    expected = before + CHECKOUT_EXPIRES_MINUTES * 60
    assert expected <= params["expires_at"] <= expected + 5


def test_取得と期限切れにはSessionのIDを渡す(app, sessions):
    retrieve_session("cs_test_123")
    expire_session("cs_test_123")
    assert sessions.calls == [("retrieve", "cs_test_123"), ("expire", "cs_test_123")]


@pytest.mark.parametrize(
    "call",
    [
        lambda o: create_checkout_session(o),
        lambda o: retrieve_session("cs_test_123"),
        lambda o: expire_session("cs_test_123"),
    ],
)
def test_StripeErrorはPaymentErrorになる(app, sessions, order, call):
    sessions.error = stripe.APIConnectionError("通信エラー")
    with app.test_request_context(), pytest.raises(PaymentError) as excinfo:
        call(order)
    assert excinfo.value.message == (
        "決済の処理に失敗しました。時間をおいてもう一度お試しください"
    )


def test_シークレットキーが無ければStripeを呼ばずにPaymentError(
    app, order, monkeypatch
):
    app.config["STRIPE_SECRET_KEY"] = ""
    monkeypatch.setattr(
        stripe, "StripeClient", lambda *a, **k: pytest.fail("Stripe を呼んではいけない")
    )
    with app.test_request_context(), pytest.raises(PaymentError) as excinfo:
        create_checkout_session(order)
    assert "決済の設定がされていません" in excinfo.value.message


def test_設定が無ければ本物のpaymentモジュールを使う():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert app.extensions["payment"] is payment


def test_テストではフェイクに差し替わっている(app, fake_payment):
    assert app.extensions["payment"] is fake_payment
