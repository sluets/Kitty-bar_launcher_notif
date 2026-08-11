#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".bmp", ".tif", ".tiff", ".avif", ".jxl",
}


def find_imagemagick() -> str | None:
    # ImageMagick 7 uses "magick". Keep "convert" as a fallback for older installs.
    return shutil.which("magick") or shutil.which("convert")


def make_thumbnail(
    magick: str,
    source: Path,
    destination: Path,
    size: int,
    quality: int,
) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Preserve the complete original filename inside the thumbnail filename:
    #   mountain.jpg -> mountain.jpg.png
    # This lets the wallpaper picker recover the exact source filename later.
    cmd = [
        magick,
        str(source),
        "-auto-orient",
        "-thumbnail", f"{size}x{size}^",
        "-gravity", "center",
        "-extent", f"{size}x{size}",
        "-strip",
        "-quality", str(quality),
        f"PNG32:{destination}",
    ]

    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        print(f"[FAIL] {source.name}", file=sys.stderr)
        if proc.stderr.strip():
            print(f"       {proc.stderr.strip()}", file=sys.stderr)
        return False

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild ~/.thumbs as normalized square PNG thumbnails."
    )
    parser.add_argument(
        "wallpaper_dir",
        nargs="?",
        default="~/Pictures/Wallpapers",
        help="Wallpaper directory (default: ~/Pictures/Wallpapers)",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=256,
        help="Thumbnail width/height in pixels (default: 256)",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=90,
        help="PNG/ImageMagick quality setting (default: 90)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing files inside .thumbs before rebuilding.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate thumbnails even if the output already exists.",
    )
    args = parser.parse_args()

    wallpaper_dir = Path(args.wallpaper_dir).expanduser().resolve()
    thumb_dir = wallpaper_dir / ".thumbs"

    if not wallpaper_dir.is_dir():
        print(f"Wallpaper directory does not exist: {wallpaper_dir}", file=sys.stderr)
        return 1

    magick = find_imagemagick()
    if magick is None:
        print(
            "ImageMagick was not found. On Arch install it with:\n"
            "  sudo pacman -S imagemagick",
            file=sys.stderr,
        )
        return 1

    thumb_dir.mkdir(parents=True, exist_ok=True)

    if args.clear:
        for item in thumb_dir.iterdir():
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

    sources = sorted(
        (
            p for p in wallpaper_dir.iterdir()
            if p.is_file() and p.suffix.casefold() in IMAGE_EXTS
        ),
        key=lambda p: p.name.casefold(),
    )

    if not sources:
        print(f"No supported images found in {wallpaper_dir}")
        return 0

    made = 0
    skipped = 0
    failed = 0

    print(f"Wallpapers : {wallpaper_dir}")
    print(f"Thumbnails : {thumb_dir}")
    print(f"Size       : {args.size}x{args.size}")
    print(f"Images     : {len(sources)}")
    print()

    for index, source in enumerate(sources, start=1):
        # Example:
        #   foo.jpg  -> .thumbs/foo.jpg.png
        #   bar.png  -> .thumbs/bar.png.png
        #
        # Keeping the original extension in the generated name removes
        # ambiguity if foo.jpg and foo.png both exist.
        destination = thumb_dir / f"{source.name}.png"

        if destination.exists() and not args.force:
            skipped += 1
            print(f"[{index:>4}/{len(sources)}] skip  {source.name}")
            continue

        print(f"[{index:>4}/{len(sources)}] make  {source.name}")
        if make_thumbnail(magick, source, destination, args.size, args.quality):
            made += 1
        else:
            failed += 1

    print()
    print(f"Done: {made} created, {skipped} skipped, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
