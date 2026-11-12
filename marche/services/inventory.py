from datetime import timedelta

from sqlalchemy import func, select, update

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, User, now_jst

# 支払い待ちの注文が在庫を押さえる時間（分）。
# Checkout Session の期限（31分）より少し長くして、期限切れの決済ページの分は数えないようにする
PENDING_MINUTES = 35

MAX_QUANTITY = 10  # 1商品あたり1回に買える数


class OrderError(Exception):
    """注文を作れない理由。message は画面にそのまま出せる日本語"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


def _normalize_cart(cart: dict) -> dict[int, int]:
    """カートを {商品ID(int): 数量(int)} に整える。
    セッションに保存したカートは JSON になり、キーが文字列（"1"）になるので int に直す"""
    normalized = {}
    for key, quantity in cart.items():
        # bool は int の仲間なので、先に弾く（True が 1 として通らないように）
        if isinstance(key, bool) or isinstance(quantity, bool):
            raise OrderError("カートの中身が正しくありません")
        if not isinstance(quantity, int):
            raise OrderError("カートの中身が正しくありません")
        try:
            product_id = int(key)
        except TypeError, ValueError:
            raise OrderError("カートの中身が正しくありません") from None
        normalized[product_id] = quantity
    return normalized


def create_order(user: User, cart: dict) -> Order:
    """カート（{商品ID: 数量}）から、支払い待ちの注文を作る。

    先に全ての商品を確かめ、1つでも駄目なら OrderError を投げる（このとき DB には何も追加しない）。
    金額は引数で受け取らず、DB の価格から計算する。"""
    if not cart:
        raise OrderError("カートが空です")
    quantities = _normalize_cart(cart)

    products = {
        product.id: product
        for product in db.session.scalars(
            select(Product).where(Product.id.in_(quantities))
        )
    }

    # 確認がすべて終わるまで、セッションには何も add しない
    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        if product is None or not product.is_published:
            # 商品名は出さない（ID を書き換えたカートで、非公開の商品の名前が分からないように）
            raise OrderError(
                "購入できない商品が含まれています。カートから外してください"
            )
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError(
                f"「{product.name}」の数量は1から{MAX_QUANTITY}の間で選んでください"
            )
        stock = available_stock(product)
        if stock == 0:
            raise OrderError(f"「{product.name}」は売り切れです")
        if quantity > stock:
            raise OrderError(
                f"「{product.name}」は在庫が足りません（いま買える数: {stock}）"
            )

    # 金額は DB の価格だけを使う。product=product で結ぶとリレーションを通して
    # 先にセッションに入るので、product_id で結ぶ
    items = [
        OrderItem(
            product_id=product_id,
            product_name=products[product_id].name,
            unit_price=products[product_id].price,
            quantity=quantity,
            subtotal=products[product_id].price * quantity,
        )
        for product_id, quantity in sorted(quantities.items())
    ]
    order = Order(
        user_id=user.id,
        status=OrderStatus.PENDING,
        total_amount=sum(item.subtotal for item in items),
        items=items,
    )
    db.session.add(order)
    db.session.commit()  # 注文と明細を1回でまとめて保存する
    return order


def cancel(order: Order) -> bool:
    """支払い待ちの注文を取り消す。取り消せたら True。

    「読んで、pending なら書く」と2段階にすると、支払いの確定と同時に来たときに両方が通るので、
    WHERE status = 'pending' の条件付き更新を1つの UPDATE 文で行う。
    支払い済み・取り消し済みの注文は変わらない（False）"""
    result = db.session.execute(
        update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.PENDING)
        .values(status=OrderStatus.CANCELLED)
    )
    db.session.commit()
    db.session.refresh(order)
    # 取り消すと、在庫の押さえ（reserved_quantity は pending だけを数える）が戻る
    return result.rowcount == 1
