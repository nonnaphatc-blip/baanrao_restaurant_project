"""Admin console (run: python cli.py). Loops until you choose 0."""
import getpass
import os
import shutil

import auth
import services
import storage
from storage import AppError


def read_password(prompt):
    """Read a password with visible masking in Windows terminals."""
    if os.name != "nt":
        return getpass.getpass(prompt)

    import msvcrt

    print(prompt, end="", flush=True)
    password = []
    while True:
        char = msvcrt.getwch()
        if char in ("\r", "\n"):
            print()
            return "".join(password)
        if char == "\003":
            raise KeyboardInterrupt
        if char in ("\b", "\x7f"):
            if password:
                password.pop()
                print("\b \b", end="", flush=True)
            continue
        if char in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if char.isprintable():
            password.append(char)
            print("*", end="", flush=True)


def show_menu():
    print("\n=== BAANRAO Console ===")
    print("1) สรุปยอดขายวันนี้")
    print("2) รายชื่อผู้ใช้")
    print("3) รีเซ็ตรหัสผ่านผู้ใช้")
    print("4) สำรองข้อมูล")
    print("0) ออกจากโปรแกรม")


def cmd_summary():
    report = services.daily_report(storage.load(), storage.today_str())
    print(f"วันที่ {report['date']}: {report['bills']} บิล ยอดขาย ฿{report['sales']:.2f}")
    for n, row in enumerate(report["top"], 1):
        print(f"  {n}. {row['name']} x{row['qty']}")


def cmd_users():
    for user in storage.load()["users"]:
        state = "ใช้งาน" if user["active"] else "ปิด"
        print(f"  #{user['id']} {user['username']:<15} {user['role']:<9} {state}")


def cmd_reset():
    username = input("ชื่อผู้ใช้: ").strip()
    password = read_password("รหัสผ่านใหม่: ")
    auth.check_password_policy(password)
    with storage.transaction() as db:
        user = auth.find_user(db, username)
        if user is None:
            raise AppError("ไม่พบผู้ใช้นี้")
        user["password_hash"] = auth.hash_password(password)
        user["session_version"] = user.get("session_version", 0) + 1
    print("เปลี่ยนรหัสผ่านแล้ว")


def cmd_backup():
    os.makedirs(os.path.join(storage.DATA_DIR, "backups"), exist_ok=True)
    target = os.path.join(storage.DATA_DIR, "backups", f"db-{storage.now_str().replace(':', '').replace(' ', '_')}.json")
    shutil.copy(storage.DB_FILE, target)
    print("สำรองข้อมูลไปที่", target)


def main():
    actions = {"1": cmd_summary, "2": cmd_users, "3": cmd_reset, "4": cmd_backup}
    while True:
        show_menu()
        choice = input("เลือก: ").strip()
        if choice == "0":
            print("ออกจากโปรแกรมเรียบร้อย")
            break
        action = actions.get(choice)
        if action is None:
            print("กรุณาเลือกเลข 0-4")
            continue
        try:
            action()
        except (AppError, OSError, ValueError) as e:
            print("ผิดพลาด:", getattr(e, "message", e))


if __name__ == "__main__":
    main()
