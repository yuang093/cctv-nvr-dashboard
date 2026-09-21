"""Week 5 #012：login / logout / change-password 路由。"""
from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from audit.log import write_audit_event
from web.auth.users import (
    change_password,
    get_user_by_username,
    record_login,
)


bp = Blueprint("auth", __name__, template_folder="../templates")


@bp.route("/login", methods=["GET", "POST"])
def login() -> object:
    """GET 顯示登入頁；POST 驗證帳密。"""
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user_row = get_user_by_username(current_app.config["NVR_DB_PATH"], username)
        ip = request.remote_addr or "0.0.0.0"
        if user_row and user_row.verify_password(password):
            login_user(user_row)
            record_login(current_app.config["NVR_DB_PATH"], user_row.id)
            write_audit_event(
                db_path=current_app.config["NVR_DB_PATH"],
                event="login",
                ip=ip,
                user_agent=request.headers.get("User-Agent"),
                user_id=user_row.id,
            )
            if user_row.must_change_password:
                return redirect(url_for("auth.change_password"))
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)
        # 登入失敗
        write_audit_event(
            db_path=current_app.config["NVR_DB_PATH"],
            event="login_failed",
            ip=ip,
            user_agent=request.headers.get("User-Agent"),
            payload={"username": username},
        )
        flash("帳號或密碼錯誤", "error")
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout() -> object:
    """登出 + 寫 audit。"""
    write_audit_event(
        db_path=current_app.config["NVR_DB_PATH"],
        event="logout",
        ip=request.remote_addr or "0.0.0.0",
        user_agent=request.headers.get("User-Agent"),
        user_id=current_user.id,
    )
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password() -> object:
    """強制改密碼頁（首次登入後）。"""
    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        if len(new_pw) < 8:
            flash("密碼至少 8 個字元", "error")
            return redirect(url_for("auth.change_password"))
        change_password(current_app.config["NVR_DB_PATH"], current_user.id, new_pw)
        write_audit_event(
            db_path=current_app.config["NVR_DB_PATH"],
            event="password_changed",
            ip=request.remote_addr or "0.0.0.0",
            user_agent=request.headers.get("User-Agent"),
            user_id=current_user.id,
        )
        flash("密碼已更新", "success")
        return redirect(url_for("dashboard.index"))
    return render_template("change_password.html")
