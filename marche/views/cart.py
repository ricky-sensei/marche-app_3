from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from marche.extensions import db
from marche.forms import QuantityForm
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("cart", __name__, url_prefix="/cart")

CART_KEY = "cart"


def get_cart() -> dict[int, int]:
    """セッションのカートを {商品ID(int): 数量(int)} に直して返す。
    セッションは JSON なのでキーは文字列。整数にできない項目は捨てる"""
    raw = session.get(CART_KEY, {})
    if not isinstance(raw, dict):
        return {}
    cart = {}
    for key, quantity in raw.items():
        try:
            product_id = int(key)
        except TypeError, ValueError:
            continue
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            continue
        cart[product_id] = quantity
    return cart


def save_cart(cart: dict[int, int]) -> None:
    session[CART_KEY] = {str(product_id): qty for product_id, qty in cart.items()}


def clear_cart() -> None:
    session.pop(CART_KEY, None)


def purchasable_limit(product: Product) -> int:
    """1回の注文で買える上限。MAX_QUANTITY と、いま買える数の小さいほう"""
    return min(MAX_QUANTITY, available_stock(product))


def get_published_product_or_404(product_id: int) -> Product:
    product = db.session.get(Product, product_id)
    if product is None or not product.is_published:
        abort(404)
    return product


def first_error(form: QuantityForm) -> str:
    return next(iter(form.quantity.errors), "数量を入力してください")


@bp.route("/")
def show():
    cart = get_cart()
    products = {}
    if cart:
        products = {
            product.id: product
            for product in db.session.scalars(
                db.select(Product).where(Product.id.in_(cart))
            )
        }
    lines = []
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product is None or not product.is_published:
            continue
        lines.append(
            {
                "product": product,
                "quantity": quantity,
                "subtotal": product.price * quantity,  # 金額は DB の価格で計算する
                "limit": purchasable_limit(product),
            }
        )
    if len(lines) < len(cart):
        # 消えた・非公開になった商品は、表示だけでなくカートからも外す
        # （残すと、画面に出ない商品のせいで注文できなくなる）
        save_cart({line["product"].id: line["quantity"] for line in lines})
        flash("購入できなくなった商品をカートから外しました")
    total = sum(line["subtotal"] for line in lines)
    return render_template("cart/show.html", lines=lines, total=total)


@bp.route("/add/<int:product_id>", methods=["POST"])
def add(product_id: int):
    product = get_published_product_or_404(product_id)
    back = redirect(url_for("shop.show", product_id=product.id))
    form = QuantityForm()
    if not form.validate_on_submit():
        flash(first_error(form))
        return back
    quantity = form.quantity.data
    if quantity < 1:
        flash("数量は1以上を選んでください")
        return back

    cart = get_cart()
    in_cart = cart.get(product.id, 0)
    limit = purchasable_limit(product)
    if limit == 0:
        flash(f"「{product.name}」は売り切れです")
        return back
    if in_cart + quantity > limit:
        remaining = limit - in_cart
        if remaining > 0:
            flash(f"「{product.name}」はあと{remaining}個までカートに入れられます")
        else:
            flash(f"「{product.name}」はこれ以上カートに入れられません")
        return back

    cart[product.id] = in_cart + quantity
    save_cart(cart)
    flash(f"「{product.name}」をカートに入れました")
    return redirect(url_for("cart.show"))


@bp.route("/update/<int:product_id>", methods=["POST"])
def update(product_id: int):
    back = redirect(url_for("cart.show"))
    cart = get_cart()
    if product_id not in cart:
        return back
    form = QuantityForm()
    if not form.validate_on_submit():
        flash(first_error(form))
        return back
    quantity = form.quantity.data

    product = db.session.get(Product, product_id)
    if quantity <= 0 or product is None or not product.is_published:
        del cart[product_id]  # 0 以下なら外す（商品が消えていても外せる）
        save_cart(cart)
        flash("カートから外しました")
        return back
    limit = purchasable_limit(product)
    if quantity > limit:
        flash(f"「{product.name}」は{limit}個までにしてください")
        return back
    cart[product_id] = quantity
    save_cart(cart)
    flash("数量を変更しました")
    return back
