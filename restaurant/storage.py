"""Local JSON storage and transactional Postgres storage for serverless hosting."""
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
MONGODB_URI = (os.environ.get("MONGODB_URI") or os.environ.get("MONGODB_URL")
               or os.environ.get("MONGO_URL"))
MONGODB_DB = os.environ.get("MONGODB_DB", "baanrao")
DB_BACKEND = os.environ.get("DB_BACKEND", "").strip().lower()
USE_MONGODB = bool(MONGODB_URI) and DB_BACKEND != "postgres"
USE_POSTGRES = bool(DATABASE_URL) and not USE_MONGODB
USE_REMOTE_DB = USE_MONGODB or USE_POSTGRES
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN")
ON_VERCEL = bool(os.environ.get("VERCEL"))
DATA_DIR = os.environ.get("DATA_DIR") or ("/tmp/restaurant-data" if ON_VERCEL else os.path.join(BASE, "data"))
DB_FILE = os.path.join(DATA_DIR, "db.json")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
TZ = timezone(timedelta(hours=7))  # Thailand
FMT = "%Y-%m-%d %H:%M:%S"
COLLECTIONS = ("users", "categories", "menu", "tables", "orders", "bills",
               "reservations", "queue", "ingredients", "events")
_lock = threading.RLock()
_PG_TABLE = "restaurant_state"
_MONGO_STATE_ID = "restaurant_state"
_MONGO_LOCK_ID = "restaurant_state_lock"
_mongo_client = None
_mongo_client_lock = threading.Lock()


class AppError(Exception):
    """Error whose message is safe to show to the user."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class StorageError(AppError):
    def __init__(self, message):
        super().__init__(message, 500)


def now_str():
    return datetime.now(TZ).strftime(FMT)


def today_str():
    return now_str()[:10]


def default_db():
    db = {name: [] for name in COLLECTIONS}
    db["logs"] = []
    db["seq"] = {}
    db["notification_sessions"] = {}
    db["rate_limits"] = {}
    db["settings"] = {"vat": 7.0, "service": 10.0, "point_per_baht": 10.0,
                      "egg_price": 10.0, "size_price": 20.0}
    return db


def _with_defaults(db):
    for key, value in default_db().items():
        db.setdefault(key, value)
    return db


def _postgres_modules():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as e:
        raise StorageError("ติดตั้ง psycopg เพื่อเชื่อมต่อ PostgreSQL") from e
    return psycopg, Jsonb


def _connect():
    if not DATABASE_URL:
        if ON_VERCEL:
            raise StorageError("ยังไม่ได้ตั้งค่า DATABASE_URL สำหรับ PostgreSQL บน Vercel")
        return None
    psycopg, _ = _postgres_modules()
    try:
        return psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.Error as e:
        raise StorageError("เชื่อมต่อฐานข้อมูล PostgreSQL ไม่สำเร็จ") from e


def _ensure_postgres(conn):
    _, Jsonb = _postgres_modules()
    conn.execute(f"CREATE TABLE IF NOT EXISTS {_PG_TABLE} (id SMALLINT PRIMARY KEY CHECK (id = 1), data JSONB NOT NULL)")
    conn.execute(f"INSERT INTO {_PG_TABLE} (id, data) VALUES (1, %s) ON CONFLICT (id) DO NOTHING",
                 (Jsonb(default_db()),))


def _mongo_collections():
    """Return MongoDB collections, reusing the client in warm serverless instances."""
    global _mongo_client
    if not MONGODB_URI:
        raise StorageError("Set MONGODB_URI to connect to MongoDB Atlas")
    try:
        from pymongo import MongoClient
        with _mongo_client_lock:
            if _mongo_client is None:
                _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000,
                                           connectTimeoutMS=5000, appname="baanrao-restaurant")
        db = _mongo_client[MONGODB_DB]
        return db["state"], db["locks"], db["uploads"]
    except ImportError as e:
        raise StorageError("Install pymongo to connect to MongoDB Atlas") from e


def _mongo_ensure(state):
    from pymongo.errors import PyMongoError
    try:
        state.update_one({"_id": _MONGO_STATE_ID},
                         {"$setOnInsert": {"data": default_db()}}, upsert=True)
        row = state.find_one({"_id": _MONGO_STATE_ID}, {"data": 1})
        if row is None:
            raise StorageError("Restaurant state was not found in MongoDB")
        return _with_defaults(row["data"])
    except PyMongoError as e:
        raise StorageError("Could not read restaurant data from MongoDB Atlas") from e


def _mongo_acquire(locks):
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError, PyMongoError
    from datetime import timedelta

    try:
        locks.update_one({"_id": _MONGO_LOCK_ID},
                         {"$setOnInsert": {"locked_until": datetime(1970, 1, 1, tzinfo=timezone.utc)}},
                         upsert=True)
    except DuplicateKeyError:
        pass
    except PyMongoError as e:
        raise StorageError("Could not initialize the MongoDB transaction lock") from e

    owner = uuid.uuid4().hex
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        now = datetime.now(timezone.utc)
        try:
            lock = locks.find_one_and_update(
                {"_id": _MONGO_LOCK_ID, "locked_until": {"$lt": now}},
                {"$set": {"owner": owner, "locked_until": now + timedelta(seconds=60)}},
                return_document=ReturnDocument.AFTER)
        except PyMongoError as e:
            raise StorageError("Could not acquire the MongoDB transaction lock") from e
        if lock:
            return owner
        time.sleep(0.05)
    raise StorageError("MongoDB is busy; please retry the operation")


@contextmanager
def _mongo_transaction(state, locks):
    from pymongo.errors import PyMongoError

    owner = _mongo_acquire(locks)
    try:
        db = _mongo_ensure(state)
        yield db
        state.replace_one({"_id": _MONGO_STATE_ID},
                          {"_id": _MONGO_STATE_ID, "data": db}, upsert=True)
    except PyMongoError as e:
        raise StorageError("Could not save restaurant data to MongoDB Atlas") from e
    finally:
        try:
            locks.update_one({"_id": _MONGO_LOCK_ID, "owner": owner},
                             {"$set": {"locked_until": datetime(1970, 1, 1, tzinfo=timezone.utc)},
                              "$unset": {"owner": ""}})
        except PyMongoError:
            pass  # The lease expires automatically if the function is interrupted.


def load():
    if USE_MONGODB:
        state, _, _ = _mongo_collections()
        return _mongo_ensure(state)
    if USE_POSTGRES:
        psycopg, _ = _postgres_modules()
        try:
            with _connect() as conn:
                _ensure_postgres(conn)
                row = conn.execute(f"SELECT data FROM {_PG_TABLE} WHERE id = 1").fetchone()
                if row is None:
                    raise StorageError("ไม่พบข้อมูลฐานข้อมูลของร้าน")
                return _with_defaults(row[0])
        except psycopg.Error as e:
            raise StorageError("อ่านข้อมูลจาก PostgreSQL ไม่สำเร็จ") from e
    with _lock:
        try:
            with open(DB_FILE, encoding="utf-8") as f:
                db = json.load(f)
        except FileNotFoundError:
            return default_db()
        except (OSError, ValueError) as e:
            raise StorageError("อ่านไฟล์ข้อมูลไม่ได้ กรุณาติดต่อผู้ดูแลระบบ") from e
        return _with_defaults(db)


def save(db):
    if USE_MONGODB:
        state, locks, _ = _mongo_collections()
        with _mongo_transaction(state, locks) as current:
            current.clear()
            current.update(db)
        return
    if USE_POSTGRES:
        psycopg, Jsonb = _postgres_modules()
        try:
            with _connect() as conn:
                _ensure_postgres(conn)
                conn.execute(f"SELECT id FROM {_PG_TABLE} WHERE id = 1 FOR UPDATE").fetchone()
                conn.execute(f"UPDATE {_PG_TABLE} SET data = %s WHERE id = 1", (Jsonb(db),))
        except psycopg.Error as e:
            raise StorageError("บันทึกข้อมูลใน PostgreSQL ไม่สำเร็จ") from e
        return
    with _lock:
        tmp = None
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False, indent=1)
            os.replace(tmp, DB_FILE)
        except (OSError, TypeError) as e:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
            raise StorageError("บันทึกข้อมูลไม่สำเร็จ") from e


@contextmanager
def transaction():
    """Lock and save one DB snapshot atomically; failed operations are rolled back."""
    if USE_MONGODB:
        state, locks, _ = _mongo_collections()
        with _mongo_transaction(state, locks) as db:
            yield db
        return
    if USE_POSTGRES:
        psycopg, Jsonb = _postgres_modules()
        try:
            with _connect() as conn:
                _ensure_postgres(conn)
                row = conn.execute(f"SELECT data FROM {_PG_TABLE} WHERE id = 1 FOR UPDATE").fetchone()
                if row is None:
                    raise StorageError("ไม่พบข้อมูลฐานข้อมูลของร้าน")
                db = _with_defaults(row[0])
                yield db
                conn.execute(f"UPDATE {_PG_TABLE} SET data = %s WHERE id = 1", (Jsonb(db),))
        except psycopg.Error as e:
            raise StorageError("บันทึกข้อมูลใน PostgreSQL ไม่สำเร็จ") from e
        return
    with _lock:
        db = load()
        yield db
        save(db)


def import_json_file(path, replace=False):
    """Import an existing local JSON database into the configured remote database."""
    if not USE_REMOTE_DB:
        raise StorageError("ตั้งค่า DATABASE_URL ก่อนนำเข้าฐานข้อมูล")
    try:
        with open(path, encoding="utf-8") as source:
            incoming = _with_defaults(json.load(source))
    except (OSError, ValueError) as e:
        raise StorageError("อ่านไฟล์ฐานข้อมูลต้นทางไม่ได้") from e
    if not incoming.get("logs"):
        audit_path = os.path.join(os.path.dirname(os.path.abspath(path)), "audit.log")
        try:
            with open(audit_path, encoding="utf-8") as source:
                logs = []
                for line in source:
                    try:
                        if line.strip():
                            logs.append(json.loads(line))
                    except ValueError:
                        continue
                incoming["logs"] = logs
        except FileNotFoundError:
            pass
        except OSError as e:
            raise StorageError("อ่านไฟล์ audit.log ต้นทางไม่ได้") from e
        for log_id, row in enumerate(incoming["logs"], 1):
            row["id"] = log_id
        incoming.setdefault("seq", {})["logs"] = len(incoming["logs"])

    if USE_MONGODB:
        state, locks, _ = _mongo_collections()
        with _mongo_transaction(state, locks) as current:
            has_data = any(current.get(key) for key in COLLECTIONS) or current.get("seq")
            if has_data and not replace:
                raise StorageError("MongoDB already has data; pass --replace to overwrite it")
            current.clear()
            current.update(incoming)
        return

    _, Jsonb = _postgres_modules()
    with _connect() as conn:
        _ensure_postgres(conn)
        row = conn.execute(f"SELECT data FROM {_PG_TABLE} WHERE id = 1 FOR UPDATE").fetchone()
        current = _with_defaults(row[0])
        has_data = any(current.get(key) for key in COLLECTIONS) or current.get("seq")
        if has_data and not replace:
            raise StorageError("ฐานข้อมูลปลายทางมีข้อมูลแล้ว หากต้องการเขียนทับให้ระบุ --replace")
        conn.execute(f"UPDATE {_PG_TABLE} SET data = %s WHERE id = 1", (Jsonb(incoming),))


def save_upload(name, content, content_type):
    if BLOB_TOKEN:
        try:
            from vercel.blob import BlobClient
            BlobClient(token=BLOB_TOKEN).put(f"uploads/{name}", content, access="public",
                                             content_type=content_type, add_random_suffix=False)
        except Exception as e:
            raise StorageError("บันทึกรูปภาพลง Vercel Blob ไม่สำเร็จ") from e
        return
    if USE_MONGODB:
        from pymongo.errors import PyMongoError
        _, _, uploads = _mongo_collections()
        try:
            uploads.replace_one({"_id": name},
                                {"_id": name, "data": bytes(content), "content_type": content_type},
                                upsert=True)
        except PyMongoError as e:
            raise StorageError("Could not save the image to MongoDB Atlas") from e
        return
    if ON_VERCEL:
        raise StorageError("ตั้งค่า BLOB_READ_WRITE_TOKEN เพื่อบันทึกรูปภาพบน Vercel")
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(UPLOAD_DIR, name), "wb") as target:
            target.write(content)
    except OSError as e:
        raise StorageError("บันทึกไฟล์ไม่สำเร็จ") from e


def upload_url(name):
    if not BLOB_TOKEN:
        return None
    try:
        from vercel.blob import BlobClient
        return BlobClient(token=BLOB_TOKEN).head(f"uploads/{name}").url
    except Exception as e:
        raise StorageError("อ่านรูปภาพจาก Vercel Blob ไม่สำเร็จ") from e


def load_upload(name):
    """Read an image stored in MongoDB; Vercel Blob images are served by URL instead."""
    if not USE_MONGODB or BLOB_TOKEN:
        return None
    from pymongo.errors import PyMongoError
    _, _, uploads = _mongo_collections()
    try:
        row = uploads.find_one({"_id": name}, {"data": 1, "content_type": 1})
        return (row["data"], row.get("content_type", "application/octet-stream")) if row else None
    except PyMongoError as e:
        raise StorageError("Could not read the image from MongoDB Atlas") from e


def next_id(db, coll):
    db["seq"][coll] = db["seq"].get(coll, 0) + 1
    return db["seq"][coll]


def take_rate_limit(key, limit, window):
    """Atomically consume a rate-limit slot across PostgreSQL-backed instances."""
    now = time.time()
    if USE_REMOTE_DB or ON_VERCEL:
        with transaction() as db:
            buckets = db.setdefault("rate_limits", {})
            hits = [stamp for stamp in buckets.get(key, []) if now - stamp < window]
            blocked = len(hits) >= limit
            if not blocked:
                hits.append(now)
            buckets[key] = hits
            if len(buckets) > 5000:
                for bucket_key, stamps in list(buckets.items()):
                    active = [stamp for stamp in stamps if now - stamp < 3600]
                    if active:
                        buckets[bucket_key] = active
                    else:
                        buckets.pop(bucket_key, None)
                while len(buckets) > 5000:
                    buckets.pop(next(iter(buckets)))
            return blocked

    with _lock:
        hits = [stamp for stamp in _local_rate_limits.get(key, []) if now - stamp < window]
        blocked = len(hits) >= limit
        if not blocked:
            hits.append(now)
        _local_rate_limits[key] = hits
        if len(_local_rate_limits) > 5000:
            for bucket_key, stamps in list(_local_rate_limits.items()):
                active = [stamp for stamp in stamps if now - stamp < 3600]
                if active:
                    _local_rate_limits[bucket_key] = active
                else:
                    _local_rate_limits.pop(bucket_key, None)
        return blocked


def clear_rate_limit(key):
    if USE_REMOTE_DB or ON_VERCEL:
        with transaction() as db:
            db.setdefault("rate_limits", {}).pop(key, None)
        return
    with _lock:
        _local_rate_limits.pop(key, None)


_local_rate_limits = {}
