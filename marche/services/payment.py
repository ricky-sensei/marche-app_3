"""Stripe を呼ぶのはこのファイルだけ。views からは current_app.extensions["payment"] 経由で呼ぶ
（テストでは、同じ名前の関数を持つ FakePayment に差し替える）"""

import logging
import time

import stripe
from flask import current_app, url_for

from marche.models import Order

logger = logging.getLogger(__name__)

# Checkout Session の有効期限（分）。Stripe の最短は30分。
# 在庫を押さえる時間（inventory.PENDING_MINUTES = 35）より短くして、期限が切れた決済ページの分は数えない
CHECKOUT_EXPIRES_MINUTES = 31


class PaymentError(Exception):
    """決済の処理に失敗した。message は画面にそのまま出せる日本語"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _client() -> stripe.StripeClient:
    """Stripe のクライアント。テストでは monkeypatch で差し替える"""
    key = current_app.config["STRIPE_SECRET_KEY"]
    if not key:
        raise PaymentError("決済の設定がされていません。管理者に連絡してください")
    return stripe.StripeClient(key)


def _call(request):
    """Stripe を呼び、失敗（stripe.StripeError）は PaymentError にして投げ直す"""
    client = _client()
    try:
        return request(client)
    except stripe.StripeError as e:
        # 種類だけをログに残す（キーや個人情報は出さない）
        logger.warning("Stripe の呼び出しに失敗しました: %s", type(e).__name__)
        raise PaymentError(
            "決済の処理に失敗しました。時間をおいてもう一度お試しください"
        ) from e


def create_checkout_session(order: Order) -> tuple[str, str]:
    """注文の Checkout Session を作り、(Session の ID, 決済ページの URL) を返す"""
    params = {
        "mode": "payment",
        # 金額は注文明細（DB）から取る。JPY は小数の無い通貨なので、円のまま（100倍しない）
        "line_items": [
            {
                "quantity": item.quantity,
                "price_data": {
                    "currency": "jpy",
                    "unit_amount": item.unit_price,
                    "product_data": {"name": item.product_name},
                },
            }
            for item in order.items
        ],
        "customer_email": order.user.email,
        "metadata": {"order_id": str(order.id)},
        # {CHECKOUT_SESSION_ID} は Stripe が置き換える。url_for に渡すと {} がエスケープされるので、後ろにつなぐ
        "success_url": url_for("orders.complete", order_id=order.id, _external=True)
        + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": url_for("orders.cancel", order_id=order.id, _external=True),
        "expires_at": int(time.time()) + CHECKOUT_EXPIRES_MINUTES * 60,
    }
    session = _call(lambda client: client.v1.checkout.sessions.create(params))
    return session.id, session.url


def retrieve_session(session_id: str):
    """Checkout Session を取得する（完了ページで支払いを確かめるのに使う）"""
    return _call(lambda client: client.v1.checkout.sessions.retrieve(session_id))


def expire_session(session_id: str):
    """Checkout Session を期限切れにする（中断ページで取り消すときに使う）"""
    return _call(lambda client: client.v1.checkout.sessions.expire(session_id))
