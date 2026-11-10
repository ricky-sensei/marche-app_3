from flask import Blueprint, abort, render_template
from flask_login import current_user
from sqlalchemy.orm import selectinload

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("shop", __name__)


def can_view(product: Product) -> bool:
    """公開中なら誰でも見られる。非公開は、出品した生産者と管理者だけ"""
    if product.is_published:
        return True
    if not current_user.is_authenticated:
        return False
    return product.producer_id == current_user.id or current_user.is_admin


@bp.route("/")
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(is_published=True)
        .options(selectinload(Product.producer))  # 生産者名を1件ずつ取りに行かない
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    # 商品ごとに SUM が1回走る。1回にまとめるのは拡張 E16
    stocks = {product.id: available_stock(product) for product in products}
    return render_template("shop/index.html", products=products, stocks=stocks)


@bp.route("/products/<int:product_id>")
def show(product_id: int):
    product = db.session.get(Product, product_id)
    # 見られない非公開の商品は 403 ではなく 404（「ある」ことも教えない）
    if product is None or not can_view(product):
        abort(404)
    stock = available_stock(product)
    return render_template(
        "shop/show.html",
        product=product,
        stock=stock,
        max_quantity=min(MAX_QUANTITY, stock),  # 数量のセレクトの上限
    )
