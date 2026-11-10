import pytest

from marche import create_app
from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, Role, User, now_jst

# 本物の外部サービス（Stripe、拡張で足す Claude API）は、テストでは呼ばない。
# 決済の章（と AI の拡張）で、ここにフェイク（偽物）のクラスを足し、create_app() の設定で差し替える。


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            # テストの写真が marche/static/uploads に溜まらないように、テストごとの一時フォルダーに置く
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(role, email, name):
    user = User(name=name, email=email, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def customer(app):
    return _make_user(Role.CUSTOMER, "customer@example.com", "購入者")


@pytest.fixture
def producer(app):
    return _make_user(Role.PRODUCER, "producer@example.com", "高原ファーム")


@pytest.fixture
def other_producer(app):
    return _make_user(Role.PRODUCER, "other@example.com", "別の農園")


@pytest.fixture
def admin(app):
    return _make_user(Role.ADMIN, "admin@example.com", "管理者")


def _make_product(producer, name, **values):
    values = {"price": 300, "stock": 10, "is_published": True} | values
    product = Product(producer_id=producer.id, name=name, **values)
    db.session.add(product)
    db.session.commit()
    return product


@pytest.fixture
def product(producer):
    return _make_product(producer, "ほうれん草 1束", description="朝採れです")


@pytest.fixture
def other_product(other_producer):
    return _make_product(other_producer, "りんご 3個")


@pytest.fixture
def make_order(app):
    """注文を作る。items は [(商品, 数量), ...]。明細には注文時点の商品名・単価・小計を写す"""

    def do_make_order(
        user, items, status=OrderStatus.PENDING, created_at=None, session_id=None
    ):
        order_items = [
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
                subtotal=product.price * quantity,
            )
            for product, quantity in items
        ]
        order = Order(
            user_id=user.id,
            status=status,
            total_amount=sum(item.subtotal for item in order_items),
            stripe_checkout_session_id=session_id,
            created_at=created_at or now_jst(),
            items=order_items,
        )
        db.session.add(order)
        db.session.commit()
        return order

    return do_make_order


# 本物の画像ではなく、先頭のバイト（マジックナンバー）だけが正しいデータ
@pytest.fixture
def jpeg_bytes():
    return b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"


@pytest.fixture
def png_bytes():
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def webp_bytes():
    return b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


@pytest.fixture
def login(client):
    def do_login(user):
        return client.post("/login", data={"email": user.email, "password": "password"})

    return do_login
