from datetime import timedelta

from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 支払い待ちの注文が在庫を押さえる時間（分）。
# Checkout Session の期限（31分）より少し長くして、期限切れの決済ページの分は数えないようにする
PENDING_MINUTES = 35


def reserved_quantity(product: Product) -> int:
    """支払い待ちで、作成から PENDING_MINUTES 分以内の注文の、この商品の数量の合計"""
    # 時刻は Python で計算して渡す（SQLite 専用の日付関数を使わず、MySQL でも同じに動く）
    cutoff = now_jst() - timedelta(minutes=PENDING_MINUTES)
    return db.session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order)
        .where(
            OrderItem.product_id == product.id,
            Order.status == OrderStatus.PENDING,
            Order.created_at >= cutoff,
        )
    )


def available_stock(product: Product) -> int:
    """いま買える数。stock は支払い確定時に減らすので、支払い済みの分は引かない"""
    return max(product.stock - reserved_quantity(product), 0)
