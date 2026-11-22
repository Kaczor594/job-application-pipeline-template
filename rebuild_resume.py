#!/usr/bin/env python3
"""Rebuild resume.pdf from a saved application directory without any API calls.

Usage:
    python rebuild_resume.py applications/<slug>
    python rebuild_resume.py applications/<slug> --no-photo
"""
import argparse
import json
import sys
from pathlib import Path

from apply import (
    PHOTO_PATH,
    build_resume_tex,
    compile_tex,
    load_profile,
)


def main():
    parser = argparse.ArgumentParser(description="Rebuild resume.pdf from saved JSON")
    parser.add_argument("app_dir", help="Application directory under applications/")
    parser.add_argument("--no-photo", action="store_true", help="Omit photo")
    args = parser.parse_args()

    app_dir = Path(args.app_dir)
    if not app_dir.is_dir():
        print(f"Error: {app_dir} is not a directory")
        sys.exit(1)

    jd_file      = app_dir / "jd_analysis.json"
    matched_file = app_dir / "matched_profile_final.json"
    if not jd_file.exists() or not matched_file.exists():
        print(f"Error: missing jd_analysis.json or matched_profile_final.json in {app_dir}")
        sys.exit(1)

    profile = load_profile()
    jd      = json.loads(jd_file.read_text(encoding="utf-8"))
    matched = json.loads(matched_file.read_text(encoding="utf-8"))

    photo_path = None if args.no_photo else (PHOTO_PATH if PHOTO_PATH.exists() else None)
    extra      = {photo_path.name: photo_path} if photo_path else {}

    tex = build_resume_tex(profile, jd, matched, photo_path=photo_path)
    (app_dir / "resume.tex").write_text(tex, encoding="utf-8")

    pdf_path = app_dir / "resume.pdf"
    ok = compile_tex(tex, pdf_path, extra_files=extra, label="rebuild")
    if ok:
        print(f"✓ {pdf_path}")
    else:
        print(f"✗ compile failed — see {pdf_path.with_suffix('.compile.log')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
