from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def role_required(*roles):
    """指定したロールのユーザーだけが開ける。未ログインはログイン画面へ、ロールが違えば 403"""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
