import click
from sqlalchemy import delete

from marche.extensions import db
from marche.models import Order, OrderItem, Product, Role, User
from marche.uploads import delete_image

SEED_PASSWORD = "password"

SEED_USERS = [
    # (メールアドレス, 名前, ロール)
    ("admin@example.com", "管理者", Role.ADMIN),
    ("producer1@example.com", "高原ファーム", Role.PRODUCER),
    ("producer2@example.com", "岩手山麓工房", Role.PRODUCER),
    ("customer1@example.com", "購入者1", Role.CUSTOMER),
    ("customer2@example.com", "購入者2", Role.CUSTOMER),
]

SEED_PRODUCTS = [
    # (生産者のメールアドレス, 商品名, 価格, 在庫, 公開するか)
    ("producer1@example.com", "八幡平マッシュルーム 200g", 450, 20, True),
    ("producer1@example.com", "安代りんどう 切り花 5本", 800, 10, True),
    ("producer1@example.com", "ほうれん草 1束", 180, 30, True),
    ("producer1@example.com", "試作中の干し野菜セット", 600, 5, False),
    ("producer2@example.com", "八幡平ポーク ロース 300g", 1200, 8, True),
    ("producer2@example.com", "手づくり味噌 1kg", 1300, 12, True),
    ("producer2@example.com", "りんごジュース 1L", 700, 15, True),
    ("producer2@example.com", "地熱野菜のピクルス", 550, 0, True),  # 売り切れの例
]


def init_app(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt="メールアドレス")
    @click.option("--name", prompt="名前", default="管理者")
    @click.password_option("--password", prompt="パスワード")
    def create_admin(email, name, password):
        """管理者を1人作る（画面からは作れないため）"""
        email = email.strip().lower()
        if db.session.scalar(db.select(User).filter_by(email=email)):
            raise click.ClickException("このメールアドレスは登録済みです")
        if len(password) < 8:
            raise click.ClickException("パスワードは8文字以上にしてください")
        user = User(name=name, email=email, role=Role.ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"管理者 {email} を作りました")

    @app.cli.command("seed")
    @click.confirmation_option(
        prompt="全てのデータを消して初期データを入れます。よろしいですか?"
    )
    def seed():
        """開発用の初期データを入れる（既存のデータは全て消す。本番では実行しない）"""
        image_filenames = db.session.scalars(
            db.select(Product.image_filename).where(Product.image_filename.is_not(None))
        ).all()
        # 外部キーの向きに合わせて、参照する側から消す（MySQL の restrict でも通る順）
        for model in (OrderItem, Order, Product, User):
            db.session.execute(delete(model))
        # 消した行のオブジェクトがセッションに残らないようにする（同じ ID の新しい行とぶつかるため）
        db.session.expunge_all()

        users = {}
        for email, name, role in SEED_USERS:
            user = User(name=name, email=email, role=role)
            user.set_password(SEED_PASSWORD)
            db.session.add(user)
            users[email] = user
        for email, name, price, stock, is_published in SEED_PRODUCTS:
            db.session.add(
                Product(
                    producer=users[email],
                    name=name,
                    price=price,
                    stock=stock,
                    is_published=is_published,
                )
            )
        db.session.commit()

        for filename in image_filenames:  # DB を入れ替えられてから、古い写真を消す
            delete_image(filename)
        click.echo(
            f"ユーザー{len(SEED_USERS)}人、商品{len(SEED_PRODUCTS)}件を入れました"
        )
