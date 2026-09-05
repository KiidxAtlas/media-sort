"""Core media organizer — scan and move video/image files."""

import os
import shutil
from pathlib import Path


VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".3gp", ".ogv", ".vob", ".ts", ".m2ts",
}

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp",
    ".svg", ".ico", ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw",
    ".psd", ".ai", ".eps",
}

ALL_EXTS = VIDEO_EXTS | IMAGE_EXTS


def scan_media_files(root: Path, category: str) -> list[Path]:
    """Recursively scan root for files in the given category (videos or images)."""
    result = []
    ext_set = VIDEO_EXTS if category == "videos" else IMAGE_EXTS
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            filepath = Path(dirpath) / fname
            if filepath.suffix.lower() in ext_set:
                result.append(filepath)
    return sorted(result)


def move_file(src: Path, dst: Path) -> str | None:
    """
    Safely move src to dst. Never deletes on failure.
    Uses copy2 + remove so source is preserved if anything fails.
    Returns None on success, error string on failure.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        return "exists"

    try:
        shutil.copy2(str(src), str(dst))
    except (shutil.Error, OSError) as e:
        return f"copy failed: {e}"

    try:
        os.remove(str(src))
    except OSError:
        # Copy succeeded, remove failed — file exists at destination, source stays
        return None

    return None


def organize(
    video_srcs: list[Path],
    image_srcs: list[Path],
    video_dest: Path | None,
    image_dest: Path | None,
    dry_run: bool = False,
):
    """
    Organize media files from multiple sources per category to separate destinations.
    Yields status events:
      ("found", count)
      ("moving", relative_path, dest_relative_path)
      ("moved", relative_path)
      ("skipped", relative_path, reason)
      ("error", relative_path, error_msg)
      ("done", moved, skipped, errors, total)
    """
    total = 0
    moved = 0
    skipped = 0
    errors = 0

    def process_src(src: Path, dest: Path, category: str):
        nonlocal total, moved, skipped, errors

        if not src.is_dir():
            yield ("skip_src", category, f"Not a directory: {src}")
            return

        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)

        files = scan_media_files(src, category)
        total += len(files)
        yield ("found", len(files))

        for filepath in files:
            ext = filepath.suffix.lower()
            ext_clean = ext.lstrip(".")
            target_dir = dest / ext_clean

            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)

            target_file = target_dir / filepath.name
            rel_src = filepath.relative_to(src)

            if target_file.exists():
                skipped += 1
                yield ("skipped", rel_src, "already exists at destination")
                continue

            if dry_run:
                rel_dst = target_file.relative_to(dest)
                yield ("moving", rel_src, rel_dst)
                moved += 1
            else:
                err = move_file(filepath, target_file)
                if err is None:
                    moved += 1
                    yield ("moved", rel_src)
                elif err == "exists":
                    skipped += 1
                    yield ("skipped", rel_src, "already exists")
                else:
                    errors += 1
                    yield ("error", rel_src, err)

    yield ("start",)

    if video_dest and video_srcs:
        for src in video_srcs:
            yield from process_src(src, video_dest, "videos")
    if image_dest and image_srcs:
        for src in image_srcs:
            yield from process_src(src, image_dest, "images")

    yield ("done", moved, skipped, errors, total)
