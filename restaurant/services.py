"""Business logic. Every function works on the `db` dict and raises AppError on bad input."""
import hmac
import math
import os
import re
import secrets
from datetime import datetime, timedelta

from auth import PHONE_RE, ROLES, USERNAME_RE, check_password_policy, find_user, hash_password
from storage import FMT, AppError, next_id, now_str, today_str

SPICE = ("ไม่เผ็ด", "เผ็ดน้อย", "เผ็ดกลาง", "เผ็ดมาก")
SIZES = ("ธรรมดา", "พิเศษ")
TABLE_STATUS = ("free", "occupied", "billing")
PAY_METHODS = ("cash", "card", "qr")
TRANSITIONS = {"pending": ("cooking", "cancelled"), "cooking": ("ready", "cancelled"),
               "ready": ("served", "cancelled"), "served": ()}
IMAGE_RE = r"[a-f0-9]{32}\.(png|jpg|jpeg|webp)"


# ---------- small helpers ----------
def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def to_float(value, default=0.0):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return default
    return number if math.isfinite(number) else default


def r2(number):
    return round(number + 1e-9, 2)


def get(db, coll, id_):
    return next((r for r in db[coll] if r["id"] == id_), None)


def need(db, coll, id_, message="ไม่พบข้อมูล"):
    row = get(db, coll, id_)
    if row is None:
        raise AppError(message, 404)
    return row


def valid_date(text):
    try:
        datetime.strptime(str(text), "%Y-%m-%d")
        return str(text)
    except ValueError:
        return None


def sniff_image(head):
    """Check magic bytes so a renamed file cannot pass as an image."""
    return head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or \
        (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


# ---------- validation ----------
def F(kind, label, **rule):
    return {"type": kind, "label": label, **rule}


SCHEMAS = {
    "categories": {"name": F(str, "ชื่อหมวดหมู่", req=True, max=50),
                   "sort": F(int, "ลำดับ", min=0, max=999, default=0)},
    "menu": {"name": F(str, "ชื่อเมนู", req=True, max=80),
             "category_id": F(int, "หมวดหมู่", req=True, min=1),
             "price": F(float, "ราคา", req=True, min=0, max=100000),
             "unit": F(str, "หน่วยเมนู", max=24, default="จาน"),
             "description": F(str, "รายละเอียด", max=200, default=""),
             "image": F(str, "รูปภาพ", pattern=IMAGE_RE, default=""),
             "available": F(bool, "สถานะมีของ", default=True),
             "has_spice": F(bool, "ความเผ็ด", default=False),
             "has_egg": F(bool, "เพิ่มไข่", default=False),
             "has_size": F(bool, "ขนาด", default=False)},
    "tables": {"name": F(str, "ชื่อโต๊ะ", req=True, max=20),
               "seats": F(int, "จำนวนที่นั่ง", min=1, max=50, default=4),
               "status": F(str, "สถานะโต๊ะ", choices=TABLE_STATUS, default="free")},
    "ingredients": {"name": F(str, "ชื่อวัตถุดิบ", req=True, max=50),
                    "unit": F(str, "หน่วย", req=True, max=10),
                    "stock": F(float, "สต็อก", min=0, max=10000000, default=0.0),
                    "min_stock": F(float, "สต็อกขั้นต่ำ", min=0, max=10000000, default=0.0)},
    "users": {"username": F(str, "ชื่อผู้ใช้", req=True, pattern=USERNAME_RE),
              "name": F(str, "ชื่อ", req=True, max=50),
              "phone": F(str, "เบอร์โทร", pattern=PHONE_RE, default=""),
              "role": F(str, "บทบาท", req=True, choices=ROLES),
              "active": F(bool, "เปิดใช้งาน", default=True),
              "points": F(int, "แต้มสะสม", min=0, max=10000000, default=0)},
    "reservations": {"name": F(str, "ชื่อผู้จอง", req=True, max=50),
                     "phone": F(str, "เบอร์โทร", req=True, pattern=PHONE_RE),
                     "date": F(str, "วันที่", req=True, pattern=r"\d{4}-\d{2}-\d{2}"),
                     "time": F(str, "เวลา", req=True, pattern=r"\d{2}:\d{2}"),
                     "party": F(int, "จำนวนคน", req=True, min=1, max=50),
                     "table_id": F(int, "โต๊ะ", min=1, default=None),
                     "note": F(str, "หมายเหตุ", max=100, default=""),
                     "status": F(str, "สถานะ", choices=("pending", "confirmed", "seated", "cancelled"),
                                 default="pending")},
    "queue": {"name": F(str, "ชื่อ", req=True, max=50),
              "phone": F(str, "เบอร์โทร", req=True, pattern=PHONE_RE),
              "party": F(int, "จำนวนคน", req=True, min=1, max=50),
              "status": F(str, "สถานะ", choices=("waiting", "called", "seated", "cancelled"),
                          default="waiting")},
    "settings": {"vat": F(float, "VAT (%)", req=True, min=0, max=30),
                 "service": F(float, "ค่าบริการ (%)", req=True, min=0, max=30),
                 "point_per_baht": F(float, "บาทต่อ 1 แต้ม", req=True, min=1, max=1000),
                 "egg_price": F(float, "ราคาไข่เพิ่ม", req=True, min=0, max=1000),
                 "size_price": F(float, "ราคาขนาดพิเศษ", req=True, min=0, max=1000)},
}

ENTITIES = {  # search fields, allowed filters, roles allowed to read/write
    "categories": {"search": ("name",), "filters": (), "roles": ("admin",)},
    "menu": {"search": ("name", "description"), "filters": ("category_id", "available"), "roles": ("admin",)},
    "tables": {"search": ("name",), "filters": ("status",), "roles": ("admin",)},
    "ingredients": {"search": ("name",), "filters": (), "roles": ("admin",)},
    "users": {"search": ("username", "name", "phone"), "filters": ("role", "active"), "roles": ("admin",)},
    "reservations": {"search": ("name", "phone", "date"), "filters": ("status", "date"), "roles": ("admin", "cashier")},
    "queue": {"search": ("name", "phone"), "filters": ("status",), "roles": ("admin", "cashier")},
}
UNIQUE = {"categories": "name", "menu": "name", "tables": "name", "ingredients": "name", "users": "username"}
EXTRA_FIELDS = {"menu": {"recipe"}, "users": {"password"}}


def _convert(value, rule):
    kind, label = rule["type"], rule["label"]
    try:
        if kind is bool:
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in ("1", "true", "on", "yes"):
                return True
            if normalized in ("0", "false", "off", "no"):
                return False
            raise ValueError
        if kind is str:
            value = str(value).strip()
            if len(value) > rule.get("max", 200):
                raise ValueError
            if rule.get("pattern") and not re.fullmatch(rule["pattern"], value):
                raise ValueError
        elif kind is int:
            value = int(str(value).strip())
        else:
            value = round(float(value), 3)
            if not math.isfinite(value):
                raise ValueError
    except (ValueError, TypeError):
        raise AppError(f"{label} ไม่ถูกต้อง") from None
    if kind in (int, float) and not rule.get("min", -1e12) <= value <= rule.get("max", 1e12):
        raise AppError(f"{label} อยู่นอกช่วงที่กำหนด")
    if "choices" in rule and value not in rule["choices"]:
        raise AppError(f"{label} ไม่ถูกต้อง")
    return value


def clean(data, schema, partial=False, extra_fields=()):
    """Validate + convert user input against a schema; reject misspelled fields."""
    if not isinstance(data, dict):
        raise AppError("รูปแบบข้อมูลไม่ถูกต้อง")
    unknown = set(data) - set(schema) - set(extra_fields)
    if unknown:
        raise AppError("พบชื่อช่องข้อมูลที่ไม่รองรับ: " + ", ".join(sorted(map(str, unknown))))
    out = {}
    for key, rule in schema.items():
        present = key in data
        blank = (not present) or data[key] is None or data[key] == ""
        if blank:
            if rule.get("req") and (not partial or present):
                raise AppError(f"กรุณากรอก{rule['label']}")
            if not partial or present:
                out[key] = rule.get("default")
            continue
        out[key] = _convert(data[key], rule)
    return out


# ---------- list / search / filter / sort / paginate ----------
def _sort_key(value):
    if value is None:
        return (2, 0, "")
    if isinstance(value, (int, float)):
        return (0, value, "")
    return (1, 0, str(value).lower())


def query(rows, params, search=(), filters=(), default_sort="id", default_order="asc", sort_fields=()):
    text = str(params.get("q", "")).strip().lower()
    if text:
        rows = [r for r in rows if any(text in str(r.get(f, "")).lower() for f in search)]
    field, _, value = str(params.get("filter", "")).partition(":")
    raw_filter = str(params.get("filter", ""))
    if raw_filter and (":" not in raw_filter or field not in filters or not value):
        raise AppError("ตัวกรองข้อมูลไม่ถูกต้อง")
    if field in filters:
        rows = [r for r in rows if str(r.get(field)).lower() == value.lower()]
    sort = str(params.get("sort") or default_sort)
    allowed_sorts = set(sort_fields) or {key for row in rows for key in row}
    if sort not in allowed_sorts:
        raise AppError("ช่องที่ใช้เรียงข้อมูลไม่ถูกต้อง")
    order = str(params.get("order") or default_order).lower()
    if order not in ("asc", "desc"):
        raise AppError("ทิศทางการเรียงข้อมูลไม่ถูกต้อง")
    descending = order == "desc"
    rows = sorted(rows, key=lambda r: _sort_key(r.get(sort)), reverse=descending)
    raw_per, raw_page = params.get("per"), params.get("page")
    if raw_per not in (None, "") and to_int(raw_per, 0) <= 0:
        raise AppError("จำนวนรายการต่อหน้าไม่ถูกต้อง")
    if raw_page not in (None, "") and to_int(raw_page, 0) <= 0:
        raise AppError("เลขหน้าไม่ถูกต้อง")
    per = min(to_int(raw_per, 10), 200)
    total = len(rows)
    pages = max(1, -(-total // per))
    page = min(max(to_int(params.get("page"), 1), 1), pages)
    return {"items": rows[(page - 1) * per: page * per], "total": total, "page": page, "pages": pages}


def public_row(entity, row):
    return {k: v for k, v in row.items() if k not in ("password_hash", "session_version")} if entity == "users" else row


def new_table_access(db):
    codes = {str(t.get("access_code", "")) for t in db["tables"]}
    while True:
        code = f"{secrets.randbelow(1000000):06d}"
        if code not in codes:
            return code


def ensure_table_access(db):
    for table in db["tables"]:
        if not table.get("qr_token"):
            table["qr_token"] = secrets.token_hex(16)
        if not table.get("access_code"):
            table["access_code"] = new_table_access(db)


def list_entity(db, entity, params):
    cfg = ENTITIES[entity]
    rows = [public_row(entity, r) for r in db[entity]]
    default_sort = "sort" if entity == "categories" else "id"
    sort_fields = (*SCHEMAS[entity].keys(), "id")
    return query(rows, params, cfg["search"], cfg["filters"], default_sort, sort_fields=sort_fields)


# ---------- generic CRUD ----------
def clean_recipe(db, recipe):
    if recipe in (None, ""):
        return []
    if not isinstance(recipe, list) or len(recipe) > 30:
        raise AppError("สูตรอาหารไม่ถูกต้อง")
    out, seen = [], set()
    for line in recipe:
        try:
            ing_id, qty = int(line["ingredient_id"]), float(line["qty"])
        except (KeyError, TypeError, ValueError):
            raise AppError("สูตรอาหาร: กรุณาเลือกวัตถุดิบและใส่ปริมาณ") from None
        if ing_id in seen or not math.isfinite(qty) or qty <= 0:
            raise AppError("สูตรอาหาร: วัตถุดิบซ้ำหรือปริมาณไม่ถูกต้อง")
        need(db, "ingredients", ing_id, "ไม่พบวัตถุดิบในสูตร")
        seen.add(ing_id)
        out.append({"ingredient_id": ing_id, "qty": round(qty, 3)})
    return out


def _check_admin_left(db, old, row):
    if old["role"] != "admin":
        return
    demoted = row.get("role", "admin") != "admin" or row.get("active", True) is False
    others = [u for u in db["users"] if u["role"] == "admin" and u["active"] and u["id"] != old["id"]]
    if demoted and not others:
        raise AppError("ต้องมีผู้ดูแลระบบที่ใช้งานได้อย่างน้อย 1 คน")


def _prepare(db, entity, row, data, old):
    """Entity specific rules. Runs before anything is written."""
    field = UNIQUE.get(entity)
    if field and field in row:
        me = old["id"] if old else None
        if any(r["id"] != me and str(r[field]).lower() == row[field].lower() for r in db[entity]):
            raise AppError(f"{SCHEMAS[entity][field]['label']}นี้ถูกใช้แล้ว")
    if entity == "menu":
        if "category_id" in row:
            need(db, "categories", row["category_id"], "ไม่พบหมวดหมู่")
        if old is None or "recipe" in data:
            row["recipe"] = clean_recipe(db, data.get("recipe"))
    elif entity == "users":
        password = data.get("password") or ""
        if old is None or password:
            check_password_policy(password)
            row["password_hash"] = hash_password(password)
            row["session_version"] = old.get("session_version", 0) + 1 if old else 0
        if old:
            _check_admin_left(db, old, row)
    elif entity == "tables" and old is None:
        row["qr_token"] = secrets.token_hex(16)
        row["access_code"] = new_table_access(db)
    elif entity == "reservations":
        for key, fmt in (("date", "%Y-%m-%d"), ("time", "%H:%M")):
            if key in row:
                try:
                    datetime.strptime(row[key], fmt)
                except ValueError:
                    raise AppError(f"{SCHEMAS[entity][key]['label']} ไม่ถูกต้อง") from None
        if row.get("table_id"):
            need(db, "tables", row["table_id"], "ไม่พบโต๊ะ")
    if entity == "reservations":
        table_id = row.get("table_id", old.get("table_id") if old else None)
        date = row.get("date", old.get("date") if old else None)
        time = row.get("time", old.get("time") if old else None)
        party = row.get("party", old.get("party", 0) if old else 0)
        status = row.get("status", old.get("status", "pending") if old else "pending")
        if table_id:
            table = need(db, "tables", table_id)
            if party > table["seats"]:
                raise AppError("จำนวนผู้จองเกินจำนวนที่นั่งของโต๊ะ")
            if status != "cancelled":
                for reservation in db["reservations"]:
                    if old and reservation["id"] == old["id"]:
                        continue
                    if (reservation.get("table_id") == table_id and
                            reservation.get("date") == date and reservation.get("time") == time and
                            reservation.get("status") != "cancelled"):
                        raise AppError("โต๊ะนี้มีรายการจองในวันและเวลาดังกล่าวแล้ว")
        if date and date < today_str() and status not in ("seated", "cancelled"):
            raise AppError("ไม่สามารถบันทึกรายการจองที่เป็นวันผ่านมาแล้วได้")
    if old is None and entity in ("reservations", "queue"):
        row["created_at"] = now_str()
    if old is None and entity == "queue":
        row["date"] = today_str()
        row["number"] = max((q["number"] for q in db["queue"] if q["date"] == row["date"]), default=0) + 1


def create_entity(db, entity, data):
    row = clean(data, SCHEMAS[entity], extra_fields=EXTRA_FIELDS.get(entity, ()))
    _prepare(db, entity, row, data, None)
    row["id"] = next_id(db, entity)
    db[entity].append(row)
    return public_row(entity, row), f"{row.get('name') or row.get('username') or row['id']}"


def update_entity(db, entity, id_, data):
    old = need(db, entity, id_)
    row = clean(data, SCHEMAS[entity], partial=True, extra_fields=EXTRA_FIELDS.get(entity, ()))
    _prepare(db, entity, row, data, old)
    changes = [f"{k}: {old.get(k)} → {v}" for k, v in row.items()
               if old.get(k) != v and k not in ("password_hash", "recipe")]
    if "password_hash" in row:
        changes.append("เปลี่ยนรหัสผ่าน")
    if "recipe" in row and old.get("recipe") != row["recipe"]:
        changes.append("แก้สูตรอาหาร")
    old.update(row)
    return public_row(entity, old), ", ".join(changes) or "ไม่มีการเปลี่ยนแปลง"


def delete_entity(db, entity, id_, actor):
    row = need(db, entity, id_)
    if entity == "categories" and any(m["category_id"] == id_ for m in db["menu"]):
        raise AppError("ลบไม่ได้ เพราะยังมีเมนูในหมวดนี้")
    if entity == "tables" and open_order(db, id_):
        raise AppError("ลบไม่ได้ เพราะโต๊ะนี้ยังมีออเดอร์ที่เปิดอยู่")
    if entity == "ingredients" and any(r["ingredient_id"] == id_ for m in db["menu"] for r in m.get("recipe", [])):
        raise AppError("ลบไม่ได้ เพราะวัตถุดิบนี้อยู่ในสูตรอาหาร")
    if entity == "users":
        if id_ == actor["id"]:
            raise AppError("ลบบัญชีของตัวเองไม่ได้")
        _check_admin_left(db, row, {"role": "deleted"})
    db[entity].remove(row)
    return row.get("name") or row.get("username") or str(id_)


def update_settings(db, data):
    row = clean(data, SCHEMAS["settings"])
    db["settings"].update(row)
    return ", ".join(f"{k}={v}" for k, v in row.items())


# ---------- menu ----------
def menu_view(db):
    keys = ("id", "name", "category_id", "price", "unit", "description", "image", "available",
            "has_spice", "has_egg", "has_size")
    s = db["settings"]
    return {"categories": sorted(db["categories"], key=lambda c: c["sort"]),
            "items": [{k: m.get(k, "จาน" if k == "unit" else "") for k in keys} for m in db["menu"]],
            "spice": SPICE, "sizes": SIZES, "egg_price": s["egg_price"], "size_price": s["size_price"]}


def clean_options(db, menu, options):
    options = options if isinstance(options, dict) else {}
    s, chosen, extra = db["settings"], {}, 0.0
    if menu["has_spice"]:
        spice = options.get("spice") or SPICE[0]
        if spice not in SPICE:
            raise AppError("ระดับความเผ็ดไม่ถูกต้อง")
        chosen["spice"] = spice
    if menu["has_egg"] and options.get("egg") in (True, "true", 1):
        chosen["egg"] = "เพิ่มไข่"
        extra += s["egg_price"]
    if menu["has_size"]:
        size = options.get("size") or SIZES[0]
        if size not in SIZES:
            raise AppError("ขนาดไม่ถูกต้อง")
        chosen["size"] = size
        if size == SIZES[1]:
            extra += s["size_price"]
    return chosen, extra


def change_stock(db, menu, qty, sign):
    """sign=-1 uses ingredients (qty > 0) or returns them (qty < 0); sign=+1 returns them."""
    for line in menu.get("recipe", []):
        ing = get(db, "ingredients", line["ingredient_id"])
        if ing is None:
            continue
        new = round(ing["stock"] + sign * line["qty"] * qty, 3)
        if new < 0:
            raise AppError(f"วัตถุดิบไม่พอ: {ing['name']}")
        ing["stock"] = new


# ---------- events (real-time by polling) ----------
def add_event(db, target, text):
    db["events"].append({"id": next_id(db, "events"), "target": target, "text": text, "at": now_str()})
    del db["events"][:-200]


def events_for(db, role, since):
    last = db["seq"].get("events", 0)
    if since < 0:
        return {"last": last, "events": []}
    targets = {"kitchen": ("kitchen",), "cashier": ("staff",), "admin": ("kitchen", "staff")}[role]
    return {"last": last, "events": [e for e in db["events"] if e["id"] > since and e["target"] in targets]}


def notification_targets(role):
    return {"kitchen": ("kitchen",), "cashier": ("staff",), "admin": ("kitchen", "staff")}[role]


def notification_view(db, user, session_id):
    targets = notification_targets(user["role"])
    state = _notification_session(db, user, session_id)
    if state is None:
        return {"events": [], "unread": 0}
    events = [event for event in db["events"]
              if event["id"] > state["start_event_id"] and event["target"] in targets]
    read_ids = set(state["read_ids"])
    rows = [{**event, "read": event["id"] in read_ids} for event in events]
    return {"events": sorted(rows, key=lambda event: event["id"], reverse=True),
            "unread": sum(not event["read"] for event in rows)}


def _notification_session(db, user, session_id):
    state = db.setdefault("notification_sessions", {}).get(str(session_id or ""))
    if not state or state.get("user_id") != user["id"]:
        return None
    return state


def notification_unread(db, user, session_id):
    targets = notification_targets(user["role"])
    state = _notification_session(db, user, session_id)
    if state is None:
        return 0
    read_ids = set(state["read_ids"])
    return sum(event["target"] in targets and event["id"] > state["start_event_id"] and event["id"] not in read_ids
               for event in db["events"])


def mark_notification_read(db, user, session_id, event_id):
    targets = notification_targets(user["role"])
    state = _notification_session(db, user, session_id)
    if state is None:
        raise AppError("เซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่", 401)
    event = next((event for event in db["events"]
                 if event["id"] == to_int(event_id, -1) and event["target"] in targets
                 and event["id"] > state["start_event_id"]), None)
    if event is None:
        raise AppError("ไม่พบการแจ้งเตือนนี้", 404)
    retained = {item["id"] for item in db["events"]
                if item["id"] > state["start_event_id"] and item["target"] in targets}
    state["read_ids"] = sorted((set(state["read_ids"]) | {event["id"]}) & retained)


# ---------- tables and orders ----------
def amount(item):
    return r2((item["price"] + item["extra"]) * item["qty"])


def live_items(order):
    if order is None:
        return []
    return [i for i in order["items"] if i["status"] != "cancelled" and not i["paid"]]


def open_order(db, table_id, create=False):
    for order in db["orders"]:
        if order["table_id"] == table_id and order["status"] == "open":
            return order
    if not create:
        return None
    order = {"id": next_id(db, "orders"), "table_id": table_id, "items": [],
             "status": "open", "created_at": now_str()}
    db["orders"].append(order)
    return order


def tables_view(db):
    out = []
    for t in sorted(db["tables"], key=lambda t: (len(t["name"]), t["name"])):
        items = live_items(open_order(db, t["id"]))
        out.append({"id": t["id"], "name": t["name"], "seats": t["seats"], "status": t["status"],
                    "count": len(items), "total": r2(sum(amount(i) for i in items))})
    return out


def item_view(item):
    return {"id": item["id"], "name": item["name"], "qty": item["qty"], "status": item["status"],
            "unit": item.get("unit", "จาน"),
            "options": " · ".join(item["options"].values()), "note": item["note"],
            "amount": amount(item), "source": item["source"]}


def order_view(db, table_id):
    need(db, "tables", table_id, "ไม่พบโต๊ะ")
    order = open_order(db, table_id)
    items = live_items(order)
    return {"order_id": order["id"] if order else None, "items": [item_view(i) for i in items],
            "subtotal": r2(sum(amount(i) for i in items))}


def add_item(db, table_id, menu_id, qty, options, note, source):
    table = need(db, "tables", table_id, "ไม่พบโต๊ะ")
    menu = need(db, "menu", to_int(menu_id), "ไม่พบเมนู")
    qty = to_int(qty)
    if not 1 <= qty <= 99:
        raise AppError("จำนวนต้องอยู่ระหว่าง 1-99")
    if not menu["available"]:
        raise AppError(f"{menu['name']} หมดแล้ว")
    chosen, extra = clean_options(db, menu, options)
    change_stock(db, menu, qty, -1)
    order = open_order(db, table_id, create=True)
    item = {"id": next_id(db, "items"), "menu_id": menu["id"], "name": menu["name"],
            "price": menu["price"], "extra": extra, "qty": qty, "unit": menu.get("unit") or "จาน", "options": chosen,
            "note": str(note or "").strip()[:100], "status": "pending",
            "created_at": now_str(), "source": source, "paid": False}
    order["items"].append(item)
    table["status"] = "occupied"
    add_event(db, "kitchen", f"{table['name']}: {menu['name']} x{qty}")
    return item_view(item)


def update_item(db, order_id, item_id, data, role):
    order = need(db, "orders", order_id, "ไม่พบออเดอร์")
    item = next((i for i in order["items"] if i["id"] == item_id), None)
    if item is None or order["status"] != "open":
        raise AppError("ไม่พบรายการ", 404)
    if item["paid"]:
        raise AppError("รายการนี้ชำระเงินแล้ว")
    menu = get(db, "menu", item["menu_id"])
    table = get(db, "tables", order["table_id"])
    if "status" in data:
        new = str(data["status"])
        if new not in TRANSITIONS.get(item["status"], ()):
            raise AppError("เปลี่ยนสถานะรายการนี้ไม่ได้")
        if new == "cancelled" and role == "kitchen":
            raise AppError("ครัวไม่มีสิทธิ์ยกเลิกรายการ", 403)
        if new == "cancelled" and item["status"] == "pending" and menu:
            change_stock(db, menu, item["qty"], +1)
        item["status"] = new
        if new == "ready":
            add_event(db, "staff", f"{table['name']}: {item['name']} พร้อมเสิร์ฟ")
        return f"{table['name']} {item['name']} → {new}"
    if "qty" in data:
        qty = to_int(data["qty"])
        if role == "kitchen":
            raise AppError("ครัวไม่มีสิทธิ์แก้จำนวน", 403)
        if not 1 <= qty <= 99:
            raise AppError("จำนวนต้องอยู่ระหว่าง 1-99")
        if item["status"] != "pending":
            raise AppError("แก้จำนวนได้เฉพาะรายการที่ยังไม่เริ่มทำ")
        if menu:
            change_stock(db, menu, qty - item["qty"], -1)
        old, item["qty"] = item["qty"], qty
        return f"{table['name']} {item['name']} จำนวน {old} → {qty}"
    raise AppError("ไม่มีข้อมูลที่ต้องแก้ไข")


def set_table_status(db, table_id, status):
    table = need(db, "tables", table_id, "ไม่พบโต๊ะ")
    if status not in TABLE_STATUS:
        raise AppError("สถานะโต๊ะไม่ถูกต้อง")
    order = open_order(db, table_id)
    if status == "free":
        if live_items(order):
            raise AppError("โต๊ะนี้ยังมีรายการที่ยังไม่ชำระเงิน")
        if order:
            order["status"] = "void"
    elif status == "billing":
        if not live_items(order):
            raise AppError("โต๊ะนี้ยังไม่มีรายการสั่ง")
        add_event(db, "staff", f"{table['name']} ขอเช็คบิล")
    table["status"] = status
    return f"{table['name']} → {status}"


def move_table(db, src_id, dst_id, merge):
    src, dst = need(db, "tables", src_id, "ไม่พบโต๊ะต้นทาง"), need(db, "tables", dst_id, "ไม่พบโต๊ะปลายทาง")
    if src_id == dst_id:
        raise AppError("เลือกโต๊ะปลายทางที่ต่างจากเดิม")
    src_order, dst_order = open_order(db, src_id), open_order(db, dst_id)
    if not live_items(src_order):
        raise AppError("โต๊ะต้นทางไม่มีรายการ")
    if dst_order and live_items(dst_order):
        if not merge:
            raise AppError("โต๊ะปลายทางไม่ว่าง (ใช้ปุ่มรวมโต๊ะแทน)")
        dst_order["items"].extend(src_order["items"])
        src_order["status"] = "merged"
    else:
        if dst_order:
            dst_order["status"] = "void"
        src_order["table_id"] = dst_id
    dst["status"], src["status"] = "occupied", "free"
    return f"{'รวม' if merge else 'ย้าย'}โต๊ะ {src['name']} → {dst['name']}"


# ---------- billing ----------
def calc_bill(subtotal, discount, vat, service):
    base = max(subtotal - discount, 0)
    svc = r2(base * service / 100)
    tax = r2((base + svc) * vat / 100)
    return {"service": svc, "vat": tax, "total": r2(base + svc + tax)}


def checkout(db, order_id, data, cashier, commit=True):
    order = need(db, "orders", to_int(order_id), "ไม่พบออเดอร์")
    if order["status"] != "open":
        raise AppError("ออเดอร์นี้ปิดแล้ว")
    items = live_items(order)
    if isinstance(data.get("item_ids"), list) and data["item_ids"]:   # split bill
        wanted = {to_int(x) for x in data["item_ids"]}
        items = [i for i in items if i["id"] in wanted]
    if not items:
        raise AppError("ไม่มีรายการให้คิดเงิน")
    method = data.get("method") or "cash"
    kind = data.get("discount_type") or "amount"
    value = to_float(data.get("discount_value"), 0.0)
    if method not in PAY_METHODS or kind not in ("amount", "percent") or value < 0 or (kind == "percent" and value > 100):
        raise AppError("ข้อมูลการชำระเงินไม่ถูกต้อง")
    subtotal = r2(sum(amount(i) for i in items))
    discount = r2(subtotal * value / 100) if kind == "percent" else r2(value)
    if discount > subtotal:
        raise AppError("ส่วนลดมากกว่ายอดรวม")
    member, redeem = None, 0
    username = str(data.get("member") or "").strip()
    if username:
        member = find_user(db, username)
        if member is None or member["role"] != "customer" or not member["active"]:
            raise AppError("ไม่พบสมาชิกนี้")
        redeem = to_int(data.get("redeem_points"), 0)
        if redeem < 0 or redeem > member["points"]:
            raise AppError("แต้มสะสมไม่พอ")
        if redeem > subtotal - discount:
            raise AppError("ใช้แต้มมากกว่ายอดที่ต้องชำระ")
    s = db["settings"]
    calc = calc_bill(subtotal, discount + redeem, s["vat"], s["service"])
    earned = int(calc["total"] // s["point_per_baht"]) if member else 0
    table = get(db, "tables", order["table_id"])
    bill = {"id": 0, "order_id": order["id"], "table_id": table["id"], "table_name": table["name"],
            "items": [{"menu_id": i.get("menu_id"), "name": i["name"],
                       "options": " · ".join(i["options"].values()), "qty": i["qty"],
                       "unit": i.get("unit", "จาน"),
                       "price": r2(i["price"] + i["extra"]), "amount": amount(i)} for i in items],
            "subtotal": subtotal, "discount": discount, "redeem": redeem, **calc,
            "vat_rate": s["vat"], "service_rate": s["service"], "method": method,
            "member": member["username"] if member else "", "earned": earned,
            "cashier": cashier["username"], "created_at": now_str(), "date": today_str()}
    if not commit:
        return bill
    bill["id"] = next_id(db, "bills")
    bill["receipt_no"] = f"R{bill['date'].replace('-', '')}-{bill['id']:04d}"
    db["bills"].append(bill)
    for item in items:
        item["paid"] = True
    if member:
        member["points"] += earned - redeem
    if live_items(order):
        table["status"] = "occupied"
    else:
        order["status"], table["status"] = "paid", "free"
        table["qr_token"] = secrets.token_hex(16)
        table["access_code"] = new_table_access(db)
    return bill


# ---------- reports ----------
def daily_report(db, date):
    bills = [b for b in db["bills"] if b["date"] == date]
    menu_units = {menu["name"]: menu.get("unit") or "จาน" for menu in db["menu"]}
    sold, methods = {}, {}
    for bill in bills:
        methods[bill["method"]] = r2(methods.get(bill["method"], 0) + bill["total"])
        for line in bill["items"]:
            unit = line.get("unit") or menu_units.get(line["name"], "จาน")
            key = (line["name"], unit)
            row = sold.setdefault(key, {"name": line["name"], "unit": unit, "qty": 0, "revenue": 0.0})
            row["qty"] += line["qty"]
            row["revenue"] = r2(row["revenue"] + line["amount"])
    sales = r2(sum(b["total"] for b in bills))
    return {"date": date, "bills": len(bills), "sales": sales,
            "avg": r2(sales / len(bills)) if bills else 0.0, "methods": methods,
            "top": sorted(sold.values(), key=lambda s: (-s["qty"], s["name"]))[:10]}


def dashboard(db, date):
    report = daily_report(db, date)
    day = datetime.strptime(date, "%Y-%m-%d")
    week = []
    for back in range(6, -1, -1):
        d = (day - timedelta(days=back)).strftime("%Y-%m-%d")
        week.append({"date": d, "sales": r2(sum(b["total"] for b in db["bills"] if b["date"] == d))})
    counts = {status: 0 for status in TABLE_STATUS}
    for t in db["tables"]:
        counts[t["status"]] += 1
    report.update({
        "week": week, "tables": counts,
        "open_orders": sum(1 for o in db["orders"] if o["status"] == "open" and live_items(o)),
        "low_stock": [{"name": i["name"], "stock": i["stock"], "unit": i["unit"]}
                      for i in db["ingredients"] if i["stock"] <= i["min_stock"]],
        "reservations": sum(1 for r in db["reservations"] if r["date"] == today_str() and r["status"] in ("pending", "confirmed")),
        "queue": sum(1 for q in db["queue"] if q["date"] == today_str() and q["status"] == "waiting")})
    return report


def kitchen_view(db):
    now = datetime.strptime(now_str(), FMT)
    tickets = []
    for order in db["orders"]:
        if order["status"] != "open":
            continue
        rows = [i for i in order["items"] if i["status"] in ("pending", "cooking", "ready")]
        if not rows:
            continue
        table = get(db, "tables", order["table_id"])
        items = [{**item_view(i), "age": int((now - datetime.strptime(i["created_at"], FMT)).total_seconds() // 60)}
                 for i in rows]
        tickets.append({"order_id": order["id"], "table": table["name"], "since": rows[0]["created_at"], "items": items})
    return sorted(tickets, key=lambda t: t["since"])


# ---------- public (customer) features ----------
def check_table_token(db, table_id, token):
    table = get(db, "tables", table_id)
    if table is None or not hmac.compare_digest(table["qr_token"], str(token)):
        raise AppError("QR Code ไม่ถูกต้อง กรุณาสแกนใหม่หรือเรียกพนักงาน", 404)
    return table


def enter_table(db, table):
    if table["status"] == "billing":
        raise AppError("โต๊ะกำลังเช็คบิล กรุณารอพนักงาน", 409)
    table["status"] = "occupied"
    return table


def check_table_code(db, code):
    normalized = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise AppError("รหัสโต๊ะไม่ถูกต้อง", 404)
    table = next((t for t in db["tables"] if hmac.compare_digest(str(t.get("access_code", "")), normalized)), None)
    if table is None:
        raise AppError("รหัสโต๊ะไม่ถูกต้อง", 404)
    return enter_table(db, table)


def customer_order(db, table_id, token, items):
    table = enter_table(db, check_table_token(db, table_id, token))
    if table["status"] == "billing":
        raise AppError("โต๊ะกำลังเช็คบิล กรุณารอพนักงาน", 409)
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise AppError("กรุณาเลือกรายการอาหาร (สูงสุด 20 รายการต่อครั้ง)")
    for line in items:
        if not isinstance(line, dict):
            raise AppError("รูปแบบรายการไม่ถูกต้อง")
        add_item(db, table_id, line.get("menu_id"), line.get("qty"), line.get("options"), line.get("note"), "customer")


def public_reservation(db, data):
    fields = {k: data.get(k) for k in ("name", "phone", "date", "time", "party", "note")}
    fields["status"] = "pending"
    if valid_date(fields["date"]) is None or fields["date"] < today_str():
        raise AppError("กรุณาเลือกวันที่ตั้งแต่วันนี้เป็นต้นไป")
    row, _ = create_entity(db, "reservations", fields)
    add_event(db, "staff", f"จองโต๊ะใหม่: {row['name']} {row['party']} คน {row['date']} {row['time']}")
    return row


def public_queue(db, data):
    fields = {k: data.get(k) for k in ("name", "phone", "party")}
    row, _ = create_entity(db, "queue", fields)
    ahead = sum(1 for q in db["queue"] if q["date"] == row["date"] and q["status"] == "waiting" and q["number"] < row["number"])
    add_event(db, "staff", f"คิวใหม่ #{row['number']}: {row['name']} {row['party']} คน")
    return {"number": row["number"], "ahead": ahead}


# ---------- first-run data ----------
def seed(db):
    if not db["users"]:
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not password:
            raise RuntimeError("Set ADMIN_PASSWORD before initializing an empty database")
        check_password_policy(password)
        db["users"].append({"id": next_id(db, "users"), "username": "admin", "name": "ผู้ดูแลระบบ",
                            "phone": "", "role": "admin", "active": True, "points": 0,
                            "session_version": 0,
                            "password_hash": hash_password(password)})
    ensure_table_access(db)
    for item in db["menu"]:
        if not item.get("unit"):
            item["unit"] = "จาน"
    for queue_item in db["queue"]:
        if queue_item.get("status") not in ("waiting", "called", "seated", "cancelled"):
            queue_item["status"] = "waiting"
    if db["categories"] or db["menu"] or db["tables"]:
        return
    for n, name in enumerate(("อาหารจานเดียว", "ต้ม / ยำ", "เครื่องดื่ม"), 1):
        db["categories"].append({"id": next_id(db, "categories"), "name": name, "sort": n})
    for name, unit, stock, low in (("ข้าวสวย", "จาน", 100, 20), ("หมูสับ", "กรัม", 5000, 500),
                                   ("ไข่ไก่", "ฟอง", 120, 20), ("กุ้ง", "ตัว", 200, 30),
                                   ("น้ำอัดลม", "ขวด", 60, 12)):
        db["ingredients"].append({"id": next_id(db, "ingredients"), "name": name, "unit": unit,
                                  "stock": float(stock), "min_stock": float(low)})
    samples = (("กะเพราหมูสับ", 1, 60, True, True, False, ((1, 1), (2, 100))),
               ("ข้าวผัดกุ้ง", 1, 80, False, True, True, ((1, 1), (4, 4))),
               ("ผัดไทยกุ้งสด", 1, 90, True, True, True, ((4, 5),)),
               ("ต้มยำกุ้ง", 2, 150, True, False, True, ((4, 6),)),
               ("ยำวุ้นเส้น", 2, 90, True, False, False, ((2, 80),)),
               ("น้ำอัดลม", 3, 20, False, False, False, ((5, 1),)))
    for name, cat, price, spice, egg, size, recipe in samples:
        db["menu"].append({"id": next_id(db, "menu"), "name": name, "category_id": cat, "price": float(price),
                           "unit": "ขวด" if cat == 3 else "จาน",
                           "description": "", "image": "", "available": True, "has_spice": spice,
                           "has_egg": egg, "has_size": size,
                           "recipe": [{"ingredient_id": i, "qty": float(q)} for i, q in recipe]})
    for n in range(1, 7):
        db["tables"].append({"id": next_id(db, "tables"), "name": f"T{n}", "seats": 4 if n <= 4 else 6,
                             "status": "free", "qr_token": secrets.token_hex(16),
                             "access_code": new_table_access(db)})
