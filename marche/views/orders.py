from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order
from marche.services.inventory import OrderError, create_order
from marche.views.cart import clear_cart, get_cart

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """自分の注文を返す。無ければ 404、本人でも管理者でもなければ 403"""
    order = db.session.get(Order, order_id)
    if order is None:
        abort(404)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


@bp.route("/", methods=["POST"])
@login_required
def create():
    # フォームから受け取るものは無い（金額も数量もセッションのカートと DB から決める）
    try:
        order = create_order(current_user, get_cart())
    except OrderError as e:
        flash(e.message)
        return redirect(url_for("cart.show"))
    clear_cart()
    flash("注文を受け付けました")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)
