import logging
import os
from datetime import datetime

from flask import Flask, render_template

from marche.extensions import csrf, db, login_manager, migrate


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "dev",
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///marche.sqlite"
        ),
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        UPLOAD_FOLDER=os.path.join(app.root_path, "static", "uploads"),
        # 写真は5MBまで。フォームの他の項目の分の余裕を見て、リクエスト全体は6MBまで
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    )
    if config is not None:
        app.config.update(config)
    if app.config["SECRET_KEY"] == "dev" and not (app.debug or app.testing):
        # 本番で SECRET_KEY を設定し忘れると、セッションを偽造される。起動を止めて気づかせる
        raise RuntimeError(
            "SECRET_KEY を .env（本番は EnvironmentFile）に設定してください"
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "ログインしてください"

    from marche.views import auth, cart, orders, producer, shop

    for module in (auth, cart, orders, producer, shop):
        app.register_blueprint(module.bp)

    from marche import commands

    commands.init_app(app)

    @app.template_filter("yen")
    def yen(value: int) -> str:
        return f"{value:,}円"

    @app.template_filter("datetime_jp")
    def datetime_jp(value: datetime) -> str:
        # strftime の曜日（%a）はロケールで変わるので、自分で日本語にする
        weekday = "月火水木金土日"[value.weekday()]
        return f"{value.month}月{value.day}日({weekday}) {value:%H:%M}"

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template("errors/413.html"), 413

    logging.basicConfig(level=logging.INFO)
    return app
