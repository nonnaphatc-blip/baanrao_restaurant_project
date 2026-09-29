"""Prepare static files for Vercel's public asset CDN."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT / "static"
target = ROOT / "public" / "static"
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, target, dirs_exist_ok=True)
