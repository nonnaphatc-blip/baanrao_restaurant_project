"""Local JSON, PostgreSQL, and MongoDB storage backends."""
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
MONGODB_URI = os.environ.get("MONGODB_URI")
HAS_DATABASE = bool(DATABASE_URL or MONGODB_URI)
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
_MONGO_COLLECTION = "restaurant_state"
_MONGO_LOCK_SECONDS = 300
_MONGO_CLIENT = None
_MONGO_CLIENT_LOCK = threading.Lock()


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


def _mongo_collection():
    global _MONGO_CLIENT
    if not MONGODB_URI:
        raise StorageError("Set MONGODB_URI to connect MongoDB Atlas")
    try:
        from pymongo import MongoClient
        with _MONGO_CLIENT_LOCK:
            if _MONGO_CLIENT is None:
                _MONGO_CLIENT = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000,
                                            connectTimeoutMS=5000)
        database_name = (os.environ.get("MONGODB_DATABASE") or
                         urlsplit(MONGODB_URI).path.lstrip("/").split("/", 1)[0] or
                         "baanrao_restaurant")
        return _MONGO_CLIENT[database_name][_MONGO_COLLECTION]
    except ImportError as e:
        raise StorageError("Install pymongo to connect MongoDB Atlas") from e


def _ensure_mongo_state(collection):
    from pymongo.errors import DuplicateKeyError
    try:
        collection.update_one({"_id": "main"}, {"$setOnInsert": {"data": default_db()}}, upsert=True)
    except DuplicateKeyError:
        # Another cold-start instance created the singleton document at the same time.
        pass


def _acquire_mongo_state():
    from pymongo import ReturnDocument
    from pymongo.errors import PyMongoError
    collection = _mongo_collection()
    try:
        collection.database.client.admin.command("ping")
        _ensure_mongo_state(collection)
        token = uuid.uuid4().hex
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            now = datetime.now(timezone.utc)
            state = collection.find_one_and_update(
                {"_id": "main", "$or": [
                    {"lock_until": {"$exists": False}},
                    {"lock_until": {"$lt": now}},
                ]},
                {"$set": {"lock_owner": token,
                          "lock_until": now + timedelta(seconds=_MONGO_LOCK_SECONDS)}},
                return_document=ReturnDocument.AFTER,
            )
            if state is not None:
                return collection, token, state
            time.sleep(0.05)
        raise StorageError("MongoDB is busy; please retry the request")
    except StorageError:
        raise
    except PyMongoError as e:
        raise StorageError("Could not connect to or read MongoDB Atlas") from e


def _release_mongo_state(collection, token):
    from pymongo.errors import PyMongoError
    try:
        collection.update_one({"_id": "main", "lock_owner": token},
                              {"$unset": {"lock_owner": "", "lock_until": ""}})
    except PyMongoError:
        # The lease expires automatically if the serverless instance has gone away.
        pass


def _mongo_store(db):
    from pymongo.errors import PyMongoError
    collection, token, _ = _acquire_mongo_state()
    try:
        result = collection.update_one(
            {"_id": "main", "lock_owner": token},
            {"$set": {"data": db}, "$unset": {"lock_owner": "", "lock_until": ""}},
        )
        if result.matched_count != 1:
            raise StorageError("MongoDB write lock expired; please retry the request")
    except StorageError:
        _release_mongo_state(collection, token)
        raise
    except PyMongoError as e:
        _release_mongo_state(collection, token)
        raise StorageError("Could not save restaurant data to MongoDB Atlas") from e


def _mongo_load():
    from pymongo.errors import PyMongoError
    try:
        collection = _mongo_collection()
        collection.database.client.admin.command("ping")
        _ensure_mongo_state(collection)
        state = collection.find_one({"_id": "main"}, {"data": 1})
        if state is None:
            raise StorageError("Restaurant database state was not found in MongoDB")
        return _with_defaults(state["data"])
    except StorageError:
        raise
    except PyMongoError as e:
        raise StorageError("Could not read restaurant data from MongoDB Atlas") from e


def _mongo_uploads():
    return _mongo_collection().database["restaurant_uploads"]


def load():
    if MONGODB_URI:
        return _mongo_load()
    if DATABASE_URL or ON_VERCEL:
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
    if MONGODB_URI:
        _mongo_store(db)
        return
    if DATABASE_URL or ON_VERCEL:
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
    if MONGODB_URI:
        from pymongo.errors import PyMongoError
        collection, token, state = _acquire_mongo_state()
        try:
            db = _with_defaults(state["data"])
            yield db
            result = collection.update_one(
                {"_id": "main", "lock_owner": token},
                {"$set": {"data": db}, "$unset": {"lock_owner": "", "lock_until": ""}},
            )
            if result.matched_count != 1:
                raise StorageError("MongoDB write lock expired; please retry the request")
        except StorageError:
            _release_mongo_state(collection, token)
            raise
        except PyMongoError as e:
            _release_mongo_state(collection, token)
            raise StorageError("Could not save restaurant data to MongoDB Atlas") from e
        except Exception:
            _release_mongo_state(collection, token)
            raise
        return
    if DATABASE_URL or ON_VERCEL:
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
    """Import an existing local JSON database into the configured database."""
    if not HAS_DATABASE:
        raise StorageError("Set DATABASE_URL or MONGODB_URI before importing restaurant data")
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

    if MONGODB_URI:
        collection, token, state = _acquire_mongo_state()
        try:
            current = _with_defaults(state["data"])
            has_data = any(current.get(key) for key in COLLECTIONS) or current.get("seq")
            if has_data and not replace:
                raise StorageError("Destination database has data; pass --replace to overwrite it")
            result = collection.update_one(
                {"_id": "main", "lock_owner": token},
                {"$set": {"data": incoming}, "$unset": {"lock_owner": "", "lock_until": ""}},
            )
            if result.matched_count != 1:
                raise StorageError("MongoDB write lock expired; please retry the import")
        except Exception:
            _release_mongo_state(collection, token)
            raise
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
    if MONGODB_URI:
        from pymongo.errors import PyMongoError
        try:
            _mongo_uploads().replace_one(
                {"_id": name}, {"_id": name, "content": bytes(content), "content_type": content_type},
                upsert=True,
            )
        except PyMongoError as e:
            raise StorageError("Could not save image to MongoDB Atlas") from e
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


def read_upload(name):
    if not MONGODB_URI:
        return None
    from pymongo.errors import PyMongoError
    try:
        item = _mongo_uploads().find_one({"_id": name}, {"content": 1, "content_type": 1})
        if item is None:
            return None
        return bytes(item["content"]), item.get("content_type") or "application/octet-stream"
    except PyMongoError as e:
        raise StorageError("Could not read image from MongoDB Atlas") from e


def next_id(db, coll):
    db["seq"][coll] = db["seq"].get(coll, 0) + 1
    return db["seq"][coll]


def take_rate_limit(key, limit, window):
    """Atomically consume a rate-limit slot across persistent database instances."""
    now = time.time()
    if HAS_DATABASE or ON_VERCEL:
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
    if HAS_DATABASE or ON_VERCEL:
        with transaction() as db:
            db.setdefault("rate_limits", {}).pop(key, None)
        return
    with _lock:
        _local_rate_limits.pop(key, None)


_local_rate_limits = {}
