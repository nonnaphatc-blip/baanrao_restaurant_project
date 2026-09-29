"""Audit log: one JSON line per important change (data/audit.log)."""
import json
import os

import storage

LOG_FILE = os.path.join(storage.DATA_DIR, "audit.log")


def log_action(user, action, entity, ref="", detail=""):
    row = {"ts": storage.now_str(), "user": user, "action": action,
           "entity": entity, "ref": str(ref), "detail": str(detail)[:300]}
    if storage.USE_REMOTE_DB or storage.ON_VERCEL:
        try:
            with storage.transaction() as db:
                logs = db.setdefault("logs", [])
                row["id"] = storage.next_id(db, "logs")
                logs.append(row)
        except storage.AppError:
            pass  # auditing must never break the user's request
        return
    try:
        os.makedirs(storage.DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # a broken log file must never break the user's request


def read_logs():
    """Return all log rows, newest first."""
    if storage.USE_REMOTE_DB or storage.ON_VERCEL:
        return list(reversed(storage.load().get("logs", [])))
    rows = []
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # skip a damaged line
                row["id"] = n
                rows.append(row)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise storage.StorageError("อ่านไฟล์ log ไม่ได้") from e
    rows.reverse()
    return rows
