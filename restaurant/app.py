"""Restaurant system - Flask entry point (also the Vercel entry point)."""
import hmac
import csv
import io
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import HTTPException

import auth
import logger
import services
import storage
from auth import role_required
from storage import AppError

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}


def load_secret():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        if len(key) < 32:
            raise RuntimeError("SECRET_KEY must contain at least 32 characters")
        return key
    if os.environ.get("VERCEL") or os.environ.get("APP_ENV", "").lower() == "production":
        raise RuntimeError("Set SECRET_KEY to a stable random value before starting in production")
    path = os.path.join(storage.DATA_DIR, "secret.key")
    try:
        os.makedirs(storage.DATA_DIR, exist_ok=True)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                key = f.read().strip()
            if len(key) < 32:
                raise RuntimeError("Local secret key is invalid; remove data/secret.key and restart")
            return key
        key = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        return key
    except OSError as e:
        raise RuntimeError("Cannot persist the local session key; set SECRET_KEY") from e


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


app = Flask(__name__)
is_production = bool(os.environ.get("VERCEL")) or os.environ.get("APP_ENV", "").lower() == "production"
if is_production and not storage.USE_REMOTE_DB:
    raise RuntimeError("Set DATABASE_URL or MONGODB_URI to persistent storage before starting in production")
app.config.update(SECRET_KEY=load_secret(), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=env_flag("COOKIE_SECURE", is_production), MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                  PERMANENT_SESSION_LIFETIME=8 * 3600)
app.json.ensure_ascii = False
app.register_blueprint(auth.bp)


# ---------- security + errors ----------
def csrf_token():
    return session.setdefault("csrf", secrets.token_hex(16))


@app.context_processor
def inject():
    return {"user": g.get("user"), "csrf_token": csrf_token, "brand": "BAANRAO"}


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        if not sent or not hmac.compare_digest(str(sent), session.get("csrf", "")):
            raise AppError("เซสชันหมดอายุ กรุณารีเฟรชหน้าแล้วลองใหม่", 400)


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https://*.public.blob.vercel-storage.com; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if (request.path.startswith("/api/") or request.path == "/table-code" or
            request.path.startswith("/t/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def fail(message, status):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error=message), status
    return render_template("error.html", message=message, status=status), status


@app.errorhandler(AppError)
def on_app_error(e):
    return fail(e.message, e.status)


@app.errorhandler(HTTPException)
def on_http_error(e):
    known = {404: "ไม่พบหน้าที่ต้องการ", 405: "ไม่รองรับคำขอนี้", 413: "ไฟล์ใหญ่เกินไป (สูงสุด 2MB)"}
    return fail(known.get(e.code, "คำขอไม่ถูกต้อง"), e.code or 400)


@app.errorhandler(Exception)
def on_error(e):
    app.logger.exception("unhandled error")  # traceback goes to the server log only
    return fail("เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง", 500)


# ---------- helpers ----------
def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise AppError("ข้อมูลที่ส่งมาไม่ถูกต้อง")
    return data


def ok(**payload):
    return jsonify(ok=True, **payload)


def audit(action, entity, ref="", detail=""):
    logger.log_action(g.user["username"] if g.user else "guest", action, entity, ref, detail)


def public_limit(name, limit=30, window=60):
    auth.rate_limit(f"{name}:{auth.client_ip()}", limit, window)


# ---------- pages ----------
@app.get("/")
def home():
    db = storage.load()
    return render_template("home.html", categories=sorted(db["categories"], key=lambda c: c["sort"]),
                           menu=db["menu"], today=storage.today_str())


@app.get("/reserve")
@role_required(*auth.ROLES)
def reserve():
    if g.user["role"] == "customer" and services.customer_has_active_reservation(storage.load(), g.user):
        return redirect("/customer-notifications")
    return render_template("reserve.html", today=storage.today_str())


@app.get("/popular")
def popular():
    db = storage.load()
    menu_by_name = {item["name"]: item for item in db["menu"]}
    menu_by_id = {item["id"]: item for item in db["menu"]}
    menu_by_name_unit = {}
    for menu_item in db["menu"]:
        menu_by_name_unit.setdefault(menu_item["name"], {})[menu_item.get("unit") or "จาน"] = menu_item
    sold = {}
    for bill in db["bills"]:
        for line in bill.get("items", []):
            name = str(line.get("name") or "เมนูไม่ระบุชื่อ")
            menu_id = line.get("menu_id")
            menu = (menu_by_id.get(menu_id) or
                    menu_by_name_unit.get(name, {}).get(line.get("unit")) or menu_by_name.get(name))
            unit = line.get("unit") or (menu or {}).get("unit") or "จาน"
            qty = services.to_int(line.get("qty"), 0)
            if qty <= 0:
                continue
            key = ("menu", menu_id) if menu_id is not None else ("legacy", name, unit)
            row = sold.setdefault(key, {"name": name, "menu_id": menu_id, "unit": unit,
                                        "qty": 0, "revenue": 0.0})
            row["qty"] += qty
            amount = services.to_float(line.get("amount"), 0.0)
            unit_price = services.to_float(line.get("price"), amount / qty)
            row["revenue"] += amount if amount else unit_price * qty
    top = sorted(sold.values(), key=lambda item: (-item["qty"], item["name"]))[:10]
    for item in top:
        item["avg_price"] = round(item["revenue"] / item["qty"], 2)
    return render_template("popular.html", top=top, menu_by_name=menu_by_name,
                           menu_by_name_unit=menu_by_name_unit, menu_by_id=menu_by_id)


@app.route("/table-code", methods=("GET", "POST"))
@role_required(*auth.ROLES)
def table_code():
    had_table_access = "customer_table" in session
    table = bound_customer_table()
    if table is not None:
        return redirect(url_for("customer_table", table_id=table["id"], k=table["qr_token"]))
    if request.method == "GET":
        expired = request.args.get("expired") == "1" or had_table_access
        return render_template("table_code.html", expired=expired)
    public_limit("table-code", 10, 60)
    with storage.transaction() as db:
        table = services.check_table_code(db, request.form.get("code", ""))
        bind_customer_table(table, db)
    return redirect(url_for("customer_table", table_id=table["id"], k=table["qr_token"]))


def bound_customer_table():
    """Return this browser session's table while its QR token remains valid."""
    access = session.get("customer_table")
    if not isinstance(access, dict):
        return None
    try:
        table_id = int(access.get("id"))
        token = str(access.get("token", ""))
        table = services.check_table_token(storage.load(), table_id, token)
        if table["status"] == "free":
            session.pop("customer_table", None)
            return None
        return table
    except (TypeError, ValueError, AppError):
        session.pop("customer_table", None)
        return None


def bind_customer_table(table, db):
    access = session.get("customer_table")
    if isinstance(access, dict):
        try:
            current = services.check_table_token(db, int(access.get("id")), str(access.get("token", "")))
        except (TypeError, ValueError, AppError):
            session.pop("customer_table", None)
            current = None
        else:
            if current["status"] == "free":
                session.pop("customer_table", None)
                current = None
        if current is not None and current["id"] != table["id"]:
            raise AppError("เซสชันนี้เข้าใช้งานโต๊ะอื่นอยู่แล้ว กรุณากลับไปยังโต๊ะเดิม", 409)
    if table["status"] == "free":
        session.pop("customer_table", None)
        raise AppError("โต๊ะนี้ถูกเคลียร์แล้ว กรุณาเข้ารหัสโต๊ะอีกครั้ง", 410)
    session["customer_table"] = {"id": table["id"], "token": table["qr_token"]}


@app.get("/pos")
@role_required("admin", "cashier")
def pos():
    return render_template("pos.html")


@app.get("/kitchen")
@role_required("admin", "cashier", "kitchen")
def kitchen():
    return render_template("kitchen.html")


@app.get("/admin")
@role_required("admin", "cashier")
def admin():
    return render_template("admin.html")


@app.get("/notifications")
@role_required("admin", "cashier", "kitchen")
def notifications():
    return render_template("notifications.html")


@app.get("/customer-notifications")
@role_required("customer")
def customer_notifications():
    return render_template("customer_notifications.html")


@app.get("/t/<int:table_id>")
def customer_table(table_id):
    token = request.args.get("k", "")
    had_table_access = "customer_table" in session
    current = bound_customer_table()
    if had_table_access and current is None:
        return redirect(url_for("table_code", expired="1"))
    if current is not None and (current["id"] != table_id or current["qr_token"] != token):
        return redirect(url_for("customer_table", table_id=current["id"], k=current["qr_token"]))
    if current is not None:
        return render_template("order.html", table=current, token=token)
    try:
        with storage.transaction() as db:
            table = services.enter_table(db, services.check_table_token(db, table_id, token))
            bind_customer_table(table, db)
    except AppError as error:
        if error.status == 404:
            return redirect(url_for("table_code", expired="1"))
        raise
    return render_template("order.html", table=table, token=token)


@app.get("/receipt/<int:bill_id>")
@role_required("admin", "cashier")
def receipt(bill_id):
    return render_template("receipt.html", bill=services.need(storage.load(), "bills", bill_id, "ไม่พบใบเสร็จ"))


@app.get("/uploads/<name>")
def uploads(name):
    if not re.fullmatch(services.IMAGE_RE, name):
        raise AppError("ไม่พบไฟล์", 404)
    blob_url = storage.upload_url(name)
    if blob_url:
        return redirect(blob_url)
    mongo_upload = storage.load_upload(name)
    if mongo_upload:
        content, content_type = mongo_upload
        return Response(content, mimetype=content_type,
                        headers={"Cache-Control": "public, max-age=3600"})
    return send_from_directory(storage.UPLOAD_DIR, name)


# ---------- API: menu, tables, orders ----------
@app.get("/api/menu")
def api_menu():
    return ok(**services.menu_view(storage.load()))


@app.get("/api/tables")
@role_required(*auth.STAFF)
def api_tables():
    db = storage.load()
    tables = services.tables_view(db)
    if g.user["role"] in ("admin", "cashier"):
        by_id = {table["id"]: table for table in db["tables"]}
        for table in tables:
            source = by_id[table["id"]]
            table["qr_token"] = source["qr_token"]
            table["access_code"] = source["access_code"]
    return ok(tables=tables)


@app.patch("/api/tables/<int:table_id>")
@role_required("admin", "cashier")
def api_table_status(table_id):
    with storage.transaction() as db:
        detail = services.set_table_status(db, table_id, str(body().get("status", "")))
    audit("table_status", "tables", table_id, detail)
    return ok()


@app.post("/api/tables/<int:table_id>/move")
@role_required("admin", "cashier")
def api_table_move(table_id):
    data = body()
    with storage.transaction() as db:
        detail = services.move_table(db, table_id, services.to_int(data.get("to")), bool(data.get("merge")))
    audit("table_move", "tables", table_id, detail)
    return ok()


@app.get("/api/orders/<int:table_id>")
@role_required("admin", "cashier")
def api_order(table_id):
    return ok(**services.order_view(storage.load(), table_id))


@app.post("/api/orders/<int:table_id>/items")
@role_required("admin", "cashier")
def api_add_item(table_id):
    data = body()
    with storage.transaction() as db:
        item = services.add_item(db, table_id, data.get("menu_id"), data.get("qty", 1),
                                 data.get("options"), data.get("note"), "staff")
    return ok(item=item)


@app.patch("/api/orders/<int:order_id>/items/<int:item_id>")
@role_required("admin", "cashier", "kitchen")
def api_update_item(order_id, item_id):
    data = body()
    with storage.transaction() as db:
        detail = services.update_item(db, order_id, item_id, data, g.user["role"])
    if data.get("status") == "cancelled" or "qty" in data:
        audit("order_item", "orders", order_id, detail)
    return ok()


@app.post("/api/orders/<int:order_id>/checkout")
@role_required("admin", "cashier")
def api_checkout(order_id):
    data = body()
    if data.get("preview"):
        return ok(bill=services.checkout(storage.load(), order_id, data, g.user, commit=False))
    with storage.transaction() as db:
        bill = services.checkout(db, order_id, data, g.user)
    audit("checkout", "bills", bill["id"], f"{bill['receipt_no']} {bill['table_name']} ฿{bill['total']} ({bill['method']})")
    return ok(bill=bill)


@app.get("/api/kitchen")
@role_required("admin", "cashier", "kitchen")
def api_kitchen():
    return ok(tickets=services.kitchen_view(storage.load()))


@app.get("/api/events")
@role_required(*auth.STAFF)
def api_events():
    since = services.to_int(request.args.get("since"), -1)
    return ok(**services.events_for(storage.load(), g.user["role"], since))


@app.get("/api/notifications")
@role_required(*auth.STAFF)
def api_notifications():
    return ok(**services.notification_view(storage.load(), g.user, session.get("notification_session")))


@app.get("/api/notifications/unread")
@role_required(*auth.STAFF)
def api_notification_unread():
    return ok(unread=services.notification_unread(storage.load(), g.user, session.get("notification_session")))


@app.post("/api/notifications/<int:event_id>/read")
@role_required(*auth.STAFF)
def api_notification_read(event_id):
    with storage.transaction() as db:
        services.mark_notification_read(db, g.user, session.get("notification_session"), event_id)
    return ok()


# ---------- API: customer (QR, reservation, queue) ----------
@app.get("/api/public/order/<int:table_id>")
def api_public_order(table_id):
    db = storage.load()
    table = services.check_table_token(db, table_id, request.args.get("k", ""))
    bind_customer_table(table, db)
    return ok(table=table["name"], billing=table["status"] == "billing", **services.order_view(db, table_id))


@app.post("/api/public/order/<int:table_id>")
def api_public_send(table_id):
    public_limit("order")
    data = body()
    try:
        with storage.transaction() as db:
            bind_customer_table(services.check_table_token(db, table_id, data.get("k", "")), db)
            services.customer_order(db, table_id, data.get("k", ""), data.get("items"))
    except AppError as error:
        # Keep inventory terminology on staff screens; customers only need a clear next step.
        if error.message.startswith("วัตถุดิบไม่พอ:"):
            raise AppError("ขออภัย เมนูนี้มีไม่พอแล้ว ลองลดจำนวนหรือเลือกเมนูอื่นนะ", error.status) from None
        raise
    return ok()


@app.get("/api/customer/reservation-notifications")
@role_required("customer")
def api_customer_reservation_notifications():
    return ok(events=services.customer_reservation_notifications(storage.load(), g.user))


@app.post("/api/public/order/<int:table_id>/bill")
def api_public_bill(table_id):
    public_limit("bill")
    with storage.transaction() as db:
        table = services.check_table_token(db, table_id, body().get("k", ""))
        bind_customer_table(table, db)
        if table["status"] == "billing":
            raise AppError("ส่งคำขอเช็คบิลแล้ว กรุณารอพนักงาน", 409)
        if not services.live_items(services.open_order(db, table_id)):
            raise AppError("ยังไม่มีรายการอาหารให้เช็คบิล", 400)
        services.set_table_status(db, table_id, "billing")
    return ok()


@app.post("/api/public/reservations")
@role_required(*auth.ROLES)
def api_public_reserve():
    public_limit("reserve", 5, 600)
    with storage.transaction() as db:
        services.public_reservation(db, body(), g.user)
    return ok(message="ส่งคำขอจองแล้ว ร้านจะติดต่อกลับเพื่อยืนยัน")


@app.post("/api/public/queue")
@role_required(*auth.ROLES)
def api_public_queue():
    public_limit("queue", 5, 600)
    with storage.transaction() as db:
        result = services.public_queue(db, body())
    return ok(message=f"คิวของคุณคือหมายเลข {result['number']} (รอก่อนหน้า {result['ahead']} คิว)")


# ---------- API: admin panel (CRUD, dashboard, logs, settings, upload) ----------
def entity_or_404(name):
    cfg = services.ENTITIES.get(name)
    if cfg is None:
        raise AppError("ไม่พบข้อมูล", 404)
    if g.user["role"] not in cfg["roles"]:
        raise AppError("คุณไม่มีสิทธิ์จัดการข้อมูลนี้", 403)
    return name


@app.get("/api/admin/dashboard")
@role_required("admin")
def api_dashboard():
    date = services.valid_date(request.args.get("date")) or storage.today_str()
    return ok(dashboard=services.dashboard(storage.load(), date))


@app.get("/api/admin/dashboard/export")
@role_required("admin")
def api_dashboard_export():
    end_date = services.valid_date(request.args.get("date")) or storage.today_str()
    end_day = datetime.strptime(end_date, "%Y-%m-%d")
    db = storage.load()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("วันที่", "จำนวนบิล", "ยอดขาย (บาท)", "เฉลี่ยต่อบิล (บาท)",
                     "เงินสด (บาท)", "บัตร (บาท)", "QR (บาท)"))
    for offset in range(29, -1, -1):
        day = (end_day - timedelta(days=offset)).strftime("%Y-%m-%d")
        report = services.daily_report(db, day)
        methods = report["methods"]
        writer.writerow((day, report["bills"], report["sales"], report["avg"],
                         methods.get("cash", 0), methods.get("card", 0), methods.get("qr", 0)))
    filename = f"dashboard-{end_date}-30-days.csv"
    return Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/admin/logs")
@role_required("admin")
def api_logs():
    return ok(**services.query(logger.read_logs(), request.args, ("user", "action", "entity", "detail"),
                               ("action", "entity", "user"), "id", "desc",
                               sort_fields=("id", "ts", "user", "action", "entity", "ref", "detail")))


@app.route("/api/admin/settings", methods=("GET", "PUT"))
@role_required("admin")
def api_settings():
    if request.method == "GET":
        return ok(settings=storage.load()["settings"])
    data = body()
    with storage.transaction() as db:
        detail = services.update_settings(db, data)
    audit("update", "settings", "", detail)
    return ok()


@app.post("/api/admin/upload")
@role_required("admin")
def api_upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        raise AppError("กรุณาเลือกไฟล์")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXT:
        raise AppError("รองรับเฉพาะไฟล์ png, jpg, webp")
    try:
        content = file.stream.read()
        if not services.sniff_image(content[:12]):
            raise AppError("ไฟล์นี้ไม่ใช่รูปภาพ")
        name = f"{uuid.uuid4().hex}.{ext}"
        storage.save_upload(name, content, file.mimetype or "application/octet-stream")
    except OSError:
        raise AppError("บันทึกไฟล์ไม่สำเร็จ", 500) from None
    audit("upload", "menu", name)
    return ok(name=name)


@app.get("/api/admin/<entity>")
@role_required("admin", "cashier")
def api_list(entity):
    return ok(**services.list_entity(storage.load(), entity_or_404(entity), request.args))


@app.post("/api/admin/<entity>")
@role_required("admin", "cashier")
def api_create(entity):
    data = body()
    with storage.transaction() as db:
        row, detail = services.create_entity(db, entity_or_404(entity), data)
    audit("create", entity, row["id"], detail)
    return ok(item=row)


@app.put("/api/admin/<entity>/<int:id_>")
@role_required("admin", "cashier")
def api_update(entity, id_):
    data = body()
    with storage.transaction() as db:
        row, detail = services.update_entity(db, entity_or_404(entity), id_, data)
    audit("update", entity, id_, detail)
    return ok(item=row)


@app.delete("/api/admin/<entity>/<int:id_>")
@role_required("admin", "cashier")
def api_delete(entity, id_):
    with storage.transaction() as db:
        detail = services.delete_entity(db, entity_or_404(entity), id_, g.user)
    audit("delete", entity, id_, detail)
    return ok()


def init_data():
    with storage.transaction() as db:
        services.seed(db)

init_data()

if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", 8000)))
    
