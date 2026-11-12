from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services import inventory
from marche.services.inventory import OrderError, create_order
from marche.services.payment import PaymentError
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

    payment = current_app.extensions["payment"]
    try:
        session_id, checkout_url = payment.create_checkout_session(order)
    except PaymentError as e:
        # 決済ページを作れなかったら、作った注文を取り消して（在庫の押さえも戻る）カートへ戻す
        inventory.cancel(order)
        flash(e.message)
        return redirect(url_for("cart.show"))

    order.stripe_checkout_session_id = session_id
    db.session.commit()
    clear_cart()
    # 303: POST のあと、決済ページを GET で開かせる
    return redirect(checkout_url, code=303)


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)


@bp.route("/<int:order_id>/complete")
@login_required
def complete(order_id: int):
    # 仮の画面。支払いの確定（Stripe への問い合わせ）は 08 章で足す
    order = get_own_order_or_403(order_id)
    return render_template("orders/complete.html", order=order)


@bp.route("/<int:order_id>/cancel", methods=["GET", "POST"])
@login_required
def cancel(order_id: int):
    order = get_own_order_or_403(order_id)
    if request.method == "GET":
        return render_template("orders/cancel.html", order=order)

    detail = redirect(url_for("orders.show", order_id=order.id))
    if order.status != OrderStatus.PENDING:
        flash(f"この注文は{order.status_label}のため、取り消せません")
        return detail
    if order.stripe_checkout_session_id:
        # 先に決済ページを期限切れにする。失敗したら（支払いが進んでいるかもしれないので）取り消さない
        try:
            current_app.extensions["payment"].expire_session(
                order.stripe_checkout_session_id
            )
        except PaymentError:
            flash(
                "お支払いが進んでいる可能性があるため、取り消せませんでした。"
                "注文詳細で状態を確かめてください"
            )
            return detail
    if inventory.cancel(order):
        flash("注文を取り消しました")
    else:
        flash(f"この注文は{order.status_label}のため、取り消せません")
    return detail
