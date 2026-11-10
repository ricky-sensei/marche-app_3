from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    RadioField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Regexp

from marche.models import Role

# email-validator パッケージを足さずに済むよう、形だけを正規表現で確かめる
EMAIL = Regexp(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="メールアドレスの形式で入力してください"
)


class RegisterForm(FlaskForm):
    name = StringField("名前", validators=[DataRequired(), Length(max=50)])
    email = EmailField(
        "メールアドレス", validators=[DataRequired(), EMAIL, Length(max=255)]
    )
    password = PasswordField("パスワード", validators=[DataRequired(), Length(min=8)])
    # 選択肢に admin を入れない。送られてきても RadioField の検証で弾かれる
    role = RadioField(
        "登録の種類",
        choices=[(Role.CUSTOMER, "購入者"), (Role.PRODUCER, "生産者")],
        default=Role.CUSTOMER,
    )


class LoginForm(FlaskForm):
    email = EmailField("メールアドレス", validators=[DataRequired(), EMAIL])
    password = PasswordField("パスワード", validators=[DataRequired()])


class JapaneseIntegerField(IntegerField):
    """整数でない入力のメッセージを日本語にする（標準は英語の "Not a valid integer value."）"""

    def process_formdata(self, valuelist):
        if not valuelist:
            return
        try:
            self.data = int(valuelist[0])
        except ValueError as exc:
            self.data = None
            raise ValueError("整数を入力してください") from exc


class ProductForm(FlaskForm):
    name = StringField(
        "商品名",
        validators=[
            DataRequired("商品名を入力してください"),
            Length(max=50, message="商品名は50文字以内で入力してください"),
        ],
    )
    # 0 を入力できるように、DataRequired ではなく InputRequired を使う
    price = JapaneseIntegerField(
        "価格（円）",
        validators=[
            InputRequired("価格を入力してください"),
            NumberRange(50, 100000, "価格は50円から100,000円の間で入力してください"),
        ],
    )
    stock = JapaneseIntegerField(
        "在庫数",
        validators=[
            InputRequired("在庫数を入力してください"),
            NumberRange(0, 999, "在庫数は0から999の間で入力してください"),
        ],
    )
    description = TextAreaField(
        "紹介文",
        validators=[
            Length(max=2000, message="紹介文は2,000文字以内で入力してください")
        ],
    )
    is_published = BooleanField("公開する")
    # 拡張子とサイズはここで確かめる。中身（先頭のバイト）は uploads.detect_media_type で確かめる
    image = FileField(
        "写真",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"], "JPEG・PNG・WebP の画像を選んでください"
            ),
            FileSize(max_size=5 * 1024 * 1024, message="写真は5MB以下にしてください"),
        ],
    )
