import re

import pytest

from marche.extensions import db


def session_cart(client):
    with client.session_transaction() as sess:
        return sess.get("cart")


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = cart


def add(client, product, quantity):
    return client.post(f"/cart/add/{product.id}", data={"quantity": quantity})


def flashed(client):
    """直前のリクエストで出したメッセージ（まだ表示していないもの）"""
    with client.session_transaction() as sess:
        return [message for _, message in sess.get("_flashes", [])]


# --- 追加 ---------------------------------------------------------------


def test_カートに入れられる(client, product):
    response = add(client, product, 2)
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert session_cart(client) == {str(product.id): 2}  # JSON なのでキーは文字列


def test_2回入れると数量が合計される(client, product):
    add(client, product, 2)
    add(client, product, 3)
    assert session_cart(client) == {str(product.id): 5}


def test_合計が10を超えると入らない(client, product):
    product.stock = 50
    db.session.commit()
    add(client, product, 7)
    response = add(client, product, 4)
    assert response.headers["Location"] == f"/products/{product.id}"
    assert session_cart(client) == {str(product.id): 7}
    assert "「ほうれん草 1束」はあと3個までカートに入れられます" in flashed(client)


def test_いま買える数を超えると入らない(client, customer, product, make_order):
    make_order(customer, [(product, 8)])  # いま買える数は 2
    add(client, product, 3)
    assert session_cart(client) is None
    assert "「ほうれん草 1束」はあと2個までカートに入れられます" in flashed(client)


def test_上限まで入っているとそれ以上は入らない(client, product):
    add(client, product, 10)
    add(client, product, 1)
    assert session_cart(client) == {str(product.id): 10}
    assert "「ほうれん草 1束」はこれ以上カートに入れられません" in flashed(client)


def test_売り切れは入らない(client, product):
    product.stock = 0
    db.session.commit()
    add(client, product, 1)
    assert session_cart(client) is None
    assert "「ほうれん草 1束」は売り切れです" in flashed(client)


@pytest.mark.parametrize("quantity", ["0", "-1", "abc", ""])
def test_不正な数量は入らない(client, product, quantity):
    add(client, product, 1)
    add(client, product, quantity)
    assert session_cart(client) == {str(product.id): 1}


def test_存在しない商品は404(client):
    assert client.post("/cart/add/9999", data={"quantity": 1}).status_code == 404


def test_非公開の商品は404(client, product):
    product.is_published = False
    db.session.commit()
    assert add(client, product, 1).status_code == 404
    assert session_cart(client) is None


def test_カートに入れるのはGETでは動かない(client, product):
    assert client.get(f"/cart/add/{product.id}").status_code == 405


def test_未ログインでもカートを使える(client, product):
    add(client, product, 1)
    assert client.get("/cart/").status_code == 200


# --- 表示 ---------------------------------------------------------------


def test_カートに商品名と単価と小計と合計が出る(client, product, other_product):
    other_product.price = 1200
    db.session.commit()
    add(client, product, 2)
    add(client, other_product, 3)
    text = client.get("/cart/").text
    assert "ほうれん草 1束" in text
    assert "りんご 3個" in text
    assert "300円" in text
    assert "600円" in text  # 300円 × 2
    assert "3,600円" in text  # 1,200円 × 3
    assert "4,200円" in text  # 合計


def test_金額はDBの今の価格で計算される(client, product):
    add(client, product, 2)
    product.price = 500
    db.session.commit()
    text = client.get("/cart/").text
    assert "1,000円" in text


def test_非公開になった商品は表示されずカートからも外れる(
    client, product, other_product
):
    add(client, product, 1)
    add(client, other_product, 1)
    client.get("/cart/")  # 「カートに入れました」のメッセージを先に表示して消しておく
    product.is_published = False
    db.session.commit()
    text = client.get("/cart/").text
    assert "ほうれん草 1束" not in text
    assert "りんご 3個" in text
    assert "購入できなくなった商品をカートから外しました" in text
    assert session_cart(client) == {str(other_product.id): 1}


def test_削除された商品はカートから外れる(client, product):
    set_cart(client, {"9999": 1, str(product.id): 1})
    client.get("/cart/")
    assert session_cart(client) == {str(product.id): 1}


def test_壊れたカートでもエラーにならない(client, product):
    set_cart(client, {"abc": 1, str(product.id): "2", "x": None})
    response = client.get("/cart/")
    assert response.status_code == 200
    assert "カートは空です" in response.text


def test_空のカートの案内(client):
    assert "カートは空です" in client.get("/cart/").text


# --- 数量の変更 -----------------------------------------------------------


def update(client, product, quantity):
    return client.post(f"/cart/update/{product.id}", data={"quantity": quantity})


def test_数量を変えられる(client, product):
    add(client, product, 1)
    response = update(client, product, 4)
    assert response.headers["Location"] == "/cart/"
    assert session_cart(client) == {str(product.id): 4}


@pytest.mark.parametrize("quantity", ["0", "-3"])
def test_0以下にするとカートから外れる(client, product, other_product, quantity):
    add(client, product, 1)
    add(client, other_product, 1)
    update(client, product, quantity)
    assert session_cart(client) == {str(other_product.id): 1}


def test_上限を超える数には変えられない(client, product):
    add(client, product, 1)
    update(client, product, 11)
    assert session_cart(client) == {str(product.id): 1}
    assert "「ほうれん草 1束」は10個までにしてください" in flashed(client)


def test_いま買える数を超える数には変えられない(client, product):
    product.stock = 3
    db.session.commit()
    add(client, product, 1)
    update(client, product, 4)
    assert session_cart(client) == {str(product.id): 1}


def test_整数でない数には変えられない(client, product):
    add(client, product, 2)
    update(client, product, "abc")
    assert session_cart(client) == {str(product.id): 2}


def test_カートに無い商品の変更は何もしない(client, product):
    update(client, product, 3)
    assert session_cart(client) is None


# --- ヘッダーと商品詳細 ------------------------------------------------------


def test_ヘッダーにカートのリンクがある(client, customer, login):
    assert 'href="/cart/"' in client.get("/").text
    login(customer)
    assert 'href="/cart/"' in client.get("/").text


def test_未ログインはログインして注文するリンク(client, product):
    add(client, product, 1)
    text = client.get("/cart/").text
    assert "ログインして注文する" in text
    assert 'href="/login?next=/cart/"' in text


def options(text):
    return [int(n) for n in re.findall(r'<option value="(\d+)">', text)]


@pytest.mark.parametrize("stock, expected", [(3, [1, 2, 3]), (20, list(range(1, 11)))])
def test_数量の選択肢はいま買える数と10の小さいほうまで(
    client, product, stock, expected
):
    product.stock = stock
    db.session.commit()
    assert options(client.get(f"/products/{product.id}").text) == expected


def test_売り切れの商品にはカートに入れるボタンが無い(client, product):
    product.stock = 0
    db.session.commit()
    text = client.get(f"/products/{product.id}").text
    assert "カートに入れる" not in text
    assert "売り切れ" in text


def test_商品詳細にカートに入れるボタンがある(client, product):
    text = client.get(f"/products/{product.id}").text
    assert f'action="/cart/add/{product.id}"' in text
    assert "カートに入れる" in text
