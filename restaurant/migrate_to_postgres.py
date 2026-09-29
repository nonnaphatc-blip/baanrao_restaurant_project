"""Copy the local JSON database and menu images to PostgreSQL and Vercel Blob."""
import argparse
import mimetypes
import os
import re

import storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=storage.DB_FILE, help="local db.json path")
    parser.add_argument("--replace", action="store_true", help="replace data already in PostgreSQL")
    args = parser.parse_args()

    upload_dir = os.path.join(os.path.dirname(os.path.abspath(args.source)), "uploads")
    images = []
    if os.path.isdir(upload_dir):
        images = [name for name in os.listdir(upload_dir)
                  if re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|jpeg|webp)", name)]
    if images and not storage.BLOB_TOKEN:
        raise SystemExit("ตั้งค่า BLOB_READ_WRITE_TOKEN ก่อน เพื่อย้ายรูปเมนูไป Vercel Blob")

    for name in images:
        path = os.path.join(upload_dir, name)
        with open(path, "rb") as source:
            storage.save_upload(name, source.read(), mimetypes.guess_type(name)[0] or "application/octet-stream")

    storage.import_json_file(args.source, replace=args.replace)
    print(f"นำเข้าฐานข้อมูลและ log สำเร็จ พร้อมย้ายรูป {len(images)} รูป")


if __name__ == "__main__":
    main()
