"""Register / Login module (separate from the main app). Role based access."""
import hashlib
import hmac
import math
import os
import re
import secrets
import time
from functools import wraps

from flask import Blueprint, g, redirect, render_template, request, session, url_for

import logger
import storage
from storage import AppError

ROLES = ("admin", "cashier", "kitchen", "customer")
STAFF = ("admin", "cashier", "kitchen")
HOME = {"admin": "/admin", "cashier": "/pos", "kitchen": "/kitchen", "customer": "/"}
USERNAME_RE = r"[A-Za-z0-9_]{3,20}"
PHONE_RE = r"\+?[0-9\-]{8,15}"
AUTH_SESSION_MAX_AGE = 8 * 3600

bp = Blueprint("auth", __name__)


# ---------- password hashing (scrypt, standard library) ----------
def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, salt, digest = stored.split("$")
        new = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(new.hex(), digest)
    except (ValueError, AttributeError):
        return False


DUMMY_HASH = hash_password("dummy-password-1")


def check_password_policy(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 100:
        raise AppError("รหัสผ่านต้องยาว 8-100 ตัวอักษร")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"[0-9]", password)):
        raise AppError("รหัสผ่านต้องมีทั้งตัวอักษรและตัวเลข")


def find_user(db, username):
    name = str(username).strip().lower()
    return next((u for u in db["users"] if u["username"].lower() == name), None)


# ---------- rate limiting ----------
def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or request.remote_addr or "?"


def take_rate_limit(key, limit, window):
    return storage.take_rate_limit(key, limit, window)


def rate_limit(key, limit, window):
    if take_rate_limit(key, limit, window):
        raise AppError("ส่งคำขอถี่เกินไป กรุณารอสักครู่", 429)


# ---------- current user + role decorator ----------
@bp.before_app_request
def load_user():
    g.user = None
    if request.endpoint == "static":
        return
    uid = session.get("uid")
    if uid:
        # Flask's permanent cookie expiry is refreshed as the browser uses it.
        # Keep a separate authentication timestamp so an active/replayed cookie
        # cannot extend an authenticated session forever.
        authenticated_at = session.get("authenticated_at")
        now = time.time()
        if (type(authenticated_at) not in (int, float) or
                authenticated_at > now or
                not math.isfinite(authenticated_at) or
                now - authenticated_at >= AUTH_SESSION_MAX_AGE):
            session.clear()
            return
        db = storage.load()
        user = next((u for u in db["users"] if u["id"] == uid), None)
        session_version = session.get("session_version", 0)
        if user and user["active"] and session_version == user.get("session_version", 0):
            notification_id = session.get("notification_session")
            if notification_id:
                state = db.get("notification_sessions", {}).get(notification_id)
                if not state or state.get("user_id") != user["id"]:
                    session.clear()
                    return
            else:
                session["notification_session"] = _new_notification_session(user)
            g.user = user
        else:
            session.clear()


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if g.user is None:
                if request.path.startswith("/api/"):
                    raise AppError("กรุณาเข้าสู่ระบบ", 401)
                return redirect(url_for("auth.login", next=request.full_path))
            if g.user["role"] not in roles:
                raise AppError("คุณไม่มีสิทธิ์เข้าถึงส่วนนี้", 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def _new_notification_session(user):
    notification_id = secrets.token_urlsafe(24)
    now = time.time()
    with storage.transaction() as db:
        sessions = db.setdefault("notification_sessions", {})
        for key, value in list(sessions.items()):
            if now - value.get("created_at", now) > 30 * 24 * 3600:
                sessions.pop(key, None)
        sessions[notification_id] = {
            "user_id": user["id"],
            "start_event_id": db.get("seq", {}).get("events", 0),
            "read_ids": [],
            "created_at": now,
        }
    return notification_id


def start_session(user):
    session.clear()
    session["uid"] = user["id"]
    session["session_version"] = user.get("session_version", 0)
    session["authenticated_at"] = time.time()
    session["csrf"] = secrets.token_hex(16)
    session["notification_session"] = _new_notification_session(user)
    session.permanent = True


# ---------- routes ----------
@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next", ""))
    username = str(request.form.get("username", "")).strip()
    password = str(request.form.get("password", ""))
    key = f"login:{client_ip()}:{username.lower()}"
    if take_rate_limit(key, 5, 300):
        return render_template("login.html", error="ลองผิดหลายครั้ง กรุณารอ 5 นาที"), 429
    user = find_user(storage.load(), username)
    valid = verify_password(password, user["password_hash"] if user else DUMMY_HASH)
    if not (user and valid and user["active"]):
        logger.log_action(username or "-", "login_failed", "auth", "", client_ip())
        return render_template("login.html", error="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"), 400
    storage.clear_rate_limit(key)
    start_session(user)
    logger.log_action(user["username"], "login", "auth")
    target = str(request.form.get("next", ""))
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return redirect(target)
    return redirect(HOME[user["role"]])


@bp.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "GET":
        return render_template("register.html")
    form = request.form
    try:
        username = str(form.get("username", "")).strip()
        name = str(form.get("name", "")).strip()
        phone = str(form.get("phone", "")).strip()
        password = str(form.get("password", ""))
        if not re.fullmatch(USERNAME_RE, username):
            raise AppError("ชื่อผู้ใช้ต้องเป็น a-z, 0-9, _ ยาว 3-20 ตัว")
        if not 1 <= len(name) <= 50:
            raise AppError("กรุณากรอกชื่อ (ไม่เกิน 50 ตัวอักษร)")
        if phone and not re.fullmatch(PHONE_RE, phone):
            raise AppError("เบอร์โทรไม่ถูกต้อง")
        check_password_policy(password)
        if password != form.get("confirm", ""):
            raise AppError("รหัสผ่านยืนยันไม่ตรงกัน")
        rate_limit(f"register:{client_ip()}", 5, 600)
        with storage.transaction() as db:
            if find_user(db, username):
                raise AppError("ชื่อผู้ใช้นี้ถูกใช้แล้ว")
            user = {"id": storage.next_id(db, "users"), "username": username, "name": name,
                    "phone": phone, "role": "customer", "active": True, "points": 0,
                    "session_version": 0,
                    "password_hash": hash_password(password)}
            db["users"].append(user)
    except AppError as e:
        return render_template("register.html", error=e.message), e.status
    logger.log_action(username, "register", "users", user["id"])
    start_session(user)
    return redirect("/")


@bp.route("/change-password", methods=("GET", "POST"))
@role_required(*ROLES)
def change_password():
    if request.method == "GET":
        return render_template("change_password.html")

    current_password = str(request.form.get("current_password", ""))
    new_password = str(request.form.get("new_password", ""))
    confirm_password = str(request.form.get("confirm_password", ""))
    key = f"change-password:{g.user['id']}:{client_ip()}"
    if take_rate_limit(key, 5, 300):
        return render_template("change_password.html", error="ลองผิดหลายครั้ง กรุณารอ 5 นาที"), 429

    if not verify_password(current_password, g.user.get("password_hash", "")):
        return render_template("change_password.html", error="รหัสผ่านปัจจุบันไม่ถูกต้อง"), 400

    try:
        check_password_policy(new_password)
        if new_password != confirm_password:
            raise AppError("รหัสผ่านใหม่และการยืนยันไม่ตรงกัน")
        if verify_password(new_password, g.user.get("password_hash", "")):
            raise AppError("กรุณาเลือกรหัสผ่านใหม่ที่ต่างจากรหัสผ่านปัจจุบัน")
        with storage.transaction() as db:
            user = next((u for u in db["users"] if u["id"] == g.user["id"]), None)
            if not user or not user["active"]:
                session.clear()
                raise AppError("ไม่พบบัญชีผู้ใช้", 401)
            user["password_hash"] = hash_password(new_password)
            user["session_version"] = user.get("session_version", 0) + 1
            updated_version = user["session_version"]
    except AppError as e:
        return render_template("change_password.html", error=e.message), e.status

    storage.clear_rate_limit(key)
    session["session_version"] = updated_version
    logger.log_action(g.user["username"], "change_password", "auth")
    return render_template("change_password.html", success="เปลี่ยนรหัสผ่านเรียบร้อยแล้ว")


@bp.post("/logout")
def logout():
    notification_id = session.get("notification_session")
    if notification_id:
        with storage.transaction() as db:
            db.setdefault("notification_sessions", {}).pop(notification_id, None)
    if g.user:
        logger.log_action(g.user["username"], "logout", "auth")
    session.clear()
    return redirect("/")
