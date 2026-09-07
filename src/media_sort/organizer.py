"""Read-only media plans and verified, non-overwriting transfers.

Safety assumes a cooperative filesystem, not hostile concurrent path replacement.
A published copy is never removed, including when source removal fails.
"""

import hashlib
import os
import shutil
import stat
import tempfile
import unicodedata
from datetime import datetime
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

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
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class PlanItem:
    source: Path
    target: Path
    category: str
    size: int
    status: str = "ready"
    reason: str = ""
    _fingerprint: tuple[int, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Plan:
    items: tuple[PlanItem, ...]
    warnings: tuple[str, ...]
    cancelled: bool = False


@dataclass(frozen=True)
class TransferResult:
    item: PlanItem
    status: str
    message: str


class _Cancelled(Exception):
    pass


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise _Cancelled


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    # Creation time is not stable across path and open-handle stats on
    # Windows; device, file ID, size, and modification time are stable.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _is_link(path: Path, info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or (
        hasattr(path, "is_junction") and path.is_junction()
    )

def _is_same_path(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except FileNotFoundError:
        return os.path.normcase(a) == os.path.normcase(b)


def _check_directory_path(path: Path, *, missing_ok: bool = False) -> None:
    """Reject links and non-directories in the usable path components.

    Some platforms expose standard directories through a system alias (for
    example, macOS ``/var``).  Those aliases are safe ancestors of a selected
    path; a link at the selected path or below it is still rejected.
    """
    trusted_ancestor_links = {Path("/var"), Path("/tmp")}
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if _is_link(part, info):
            if part != path and part in trusted_ancestor_links:
                continue
            raise ValueError(f"Symbolic links and junctions are not supported: {part}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Not a directory: {part}")
def _root(path: Path, *, destination: bool) -> Path:
    # Do not resolve away links before validating them.
    path = Path(os.path.abspath(path.expanduser()))
    try:
        _check_directory_path(path, missing_ok=destination)
        if not destination:
            # Opening, rather than os.access(), also works for ACL-based access.
            with os.scandir(path):
                pass
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid {'destination' if destination else 'source'} {path}: {exc}") from exc
    return path


def _case_insensitive(path: Path, cancel: Event | None) -> bool:
    """Inspect an existing spelling alias without writing filesystem probes."""
    while not path.exists():
        path = path.parent
    while path.name and not os.path.ismount(path):
        alternate = path.with_name(path.name.swapcase())
        if alternate != path:
            try:
                return path.samefile(alternate)
            except FileNotFoundError:
                return False
        path = path.parent
    # A mount point belongs to its parent filesystem; inspect its children
    # instead. If no spelling can be tested, reserve conservatively.
    with os.scandir(path) as entries:
        for entry in entries:
            _check_cancel(cancel)
            if _is_link(Path(entry.path), entry.stat(follow_symlinks=False)):
                continue
            alternate = entry.name.swapcase()
            if alternate != entry.name:
                try:
                    return os.path.samefile(entry.path, path / alternate)
                except FileNotFoundError:
                    return False
    return True


def _scan(
    root: Path,
    extensions: set[str],
    destinations: tuple[Path, ...],
    destination_ids: set[tuple[int, int]],
    seen_directories: set[tuple[int, int]],
    warnings: list[str],
    cancel: Event | None,
) -> Iterator[tuple[Path, os.stat_result]]:
    pending = [root]
    while pending:
        _check_cancel(cancel)
        directory = pending.pop()
        if any(os.path.normcase(directory) != os.path.normcase(dest) and directory.is_relative_to(dest) for dest in destinations):
            continue
        try:
            info = directory.lstat()
            if _is_link(directory, info):
                warnings.append(f"Not traversing symbolic link or junction: {directory}")
                continue
            identity = (info.st_dev, info.st_ino)
            if identity in seen_directories or identity in destination_ids:
                continue
            seen_directories.add(identity)
            entries = []
            with os.scandir(directory) as scan:
                for entry in scan:
                    _check_cancel(cancel)
                    entries.append(entry)
            for entry in sorted(entries, key=lambda entry: entry.name):
                _check_cancel(cancel)
                path = Path(entry.path)
                try:
                    info = path.lstat()
                    if _is_link(path, info):
                        warnings.append(f"Not traversing symbolic link or junction: {path}")
                    elif stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif path.suffix.lower() in extensions:
                        if stat.S_ISREG(info.st_mode):
                            yield path, info
                        else:
                            warnings.append(f"Not a regular file: {path}")
                except OSError as exc:
                    warnings.append(f"Cannot inspect {path}: {exc}")
        except OSError as exc:
            warnings.append(f"Cannot scan {directory}: {exc}")


def build_plan(
    video_srcs: list[Path],
    image_srcs: list[Path],
    video_dest: Path | None,
    image_dest: Path | None,
    *,
    cancel: Event | None = None,
    sort_by_date: bool = False,
    deduplicate: bool = False,
) -> Plan:
    """Snapshot supported regular files without creating or changing any paths.

    Invalid roots raise ValueError. Inaccessible descendants and ignored links
    are reported in warnings. Colliding targets stay skipped even if a preceding
    transfer fails, so execution agrees with the reviewed preview.
    """
    items: list[PlanItem] = []
    warnings: list[str] = []
    seen_files: set[tuple[int, int]] = set()
    reserved: set[str] = set()
    case_insensitive: dict[Path, bool] = {}
    try:
        _check_cancel(cancel)
        groups = []
        for category, sources, destination, extensions in (
            ("videos", video_srcs, video_dest, VIDEO_EXTS),
            ("images", image_srcs, image_dest, IMAGE_EXTS),
        ):
            if sources and destination is None:
                raise ValueError(f"Choose a destination for {category}.")
            dest = _root(destination, destination=True) if destination is not None else None
            roots = []
            for source in sources:
                _check_cancel(cancel)
                roots.append(_root(source, destination=False))
            groups.append((category, roots, dest, extensions))
        destinations = tuple(group[2] for group in groups if group[2] is not None)
        destination_ids = set()
        for destination in destinations:
            try:
                info = destination.lstat()
                destination_ids.add((info.st_dev, info.st_ino))
            except FileNotFoundError:
                pass
        for category, roots, dest, extensions in groups:
            seen_directories: set[tuple[int, int]] = set()
            for root in roots:
                ancestors = (ancestor.stat() for ancestor in (root, *root.parents))
                if any((info.st_dev, info.st_ino) in destination_ids for info in ancestors):
                    # Allow source == destination (same inode), but not source inside destination
                    if not any(
                        _is_same_path(root, dest)
                        for dest in destinations
                        if dest is not None
                    ):
                        raise ValueError(f"Source is inside a destination: {root}")
            for root in roots:
                for source, info in _scan(root, extensions, destinations, destination_ids, seen_directories, warnings, cancel):
                    identity = (info.st_dev, info.st_ino)
                    if identity in seen_files:
                        continue
                    seen_files.add(identity)
                    if sort_by_date:
                        mtime = datetime.fromtimestamp(info.st_mtime)
                        target = dest / str(mtime.year) / mtime.strftime("%B").lower() / source.name
                    else:
                        target = dest / source.suffix.lower().lstrip(".") / source.name
                    status, reason = "ready", ""
                    try:
                        _check_directory_path(target.parent, missing_ok=True)
                        if target.parent not in case_insensitive:
                            case_insensitive[target.parent] = _case_insensitive(target.parent, cancel)
                        key = str(target)
                        if case_insensitive[target.parent]:
                            key = unicodedata.normalize("NFC", key).casefold()
                        if os.path.lexists(target):
                            if deduplicate and info.st_size > 0:
                                try:
                                    target_info = target.lstat()
                                    if target_info.st_size == info.st_size:
                                        src_hash = _hash_file(source, cancel)
                                        tgt_hash = _hash_file(target, cancel)
                                        if src_hash == tgt_hash:
                                            status, reason = "deduplicate", "Duplicate of existing target; source will be removed."
                                except OSError:
                                    pass
                            if status == "ready":
                                status, reason = "skipped", "Target already exists; source will be kept."
                        elif key in reserved:
                            status, reason = "skipped", "Another planned file has the same target; source will be kept."
                        reserved.add(key)
                    except (OSError, ValueError) as exc:
                        status, reason = "error", str(exc)
                    items.append(PlanItem(source, target, category, info.st_size, status, reason, _fingerprint(info)))
    except _Cancelled:
        return Plan(tuple(items), tuple(warnings), cancelled=True)
    return Plan(tuple(items), tuple(warnings))


def _check_source(item: PlanItem) -> None:
    _check_directory_path(item.source.parent)
    info = item.source.lstat()
    if _is_link(item.source, info) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Source is no longer a regular file: {item.source}")
    if not item._fingerprint or _fingerprint(info) != item._fingerprint:
        raise ValueError(f"Source changed since preview: {item.source}")


def _hash_file(path: Path, cancel: Event | None) -> bytes:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            _check_cancel(cancel)
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()

def _transfer(item: PlanItem, cancel: Event | None, dirs: set[Path] | None = None) -> TransferResult:
    stage: Path | None = None
    stage_identity: tuple[int, int] | None = None
    published = False
    status, message = "error", "Transfer did not complete."
    try:
        _check_cancel(cancel)
        if item.status == "deduplicate":
            _check_source(item)
            if not os.path.lexists(item.target):
                raise ValueError("Target disappeared; source kept.")
            target_info = item.target.lstat()
            if target_info.st_size != item.size:
                raise ValueError("Target size changed; source kept.")
            src_hash = _hash_file(item.source, cancel)
            tgt_hash = _hash_file(item.target, cancel)
            if src_hash != tgt_hash:
                raise ValueError("Target content differs; source kept.")
            item.source.unlink()
            status, message = "deduplicated", "Duplicate removed; target retained."
            return TransferResult(item, status, message)
        _check_source(item)
        _check_directory_path(item.target.parent, missing_ok=True)
        if os.path.lexists(item.target):
            return TransferResult(item, "skipped", "Target already exists; source kept.")
        item.target.parent.mkdir(parents=True, exist_ok=True)
        _check_directory_path(item.target.parent)
        fd, name = tempfile.mkstemp(prefix=".media-sort-", suffix=".partial", dir=item.target.parent)
        stage = Path(name)
        with os.fdopen(fd, "w+b") as output:
            info = os.fstat(output.fileno())
            stage_identity = (info.st_dev, info.st_ino)
            source_fd = os.open(item.source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(source_fd, "rb") as source:
                if _fingerprint(os.fstat(source.fileno())) != item._fingerprint:
                    raise ValueError(f"Source changed since preview: {item.source}")
                digest = hashlib.sha256()
                while True:
                    _check_cancel(cancel)
                    chunk = source.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
                if _fingerprint(os.fstat(source.fileno())) != item._fingerprint:
                    raise ValueError(f"Source changed during copy: {item.source}")
            _check_source(item)
            shutil.copystat(item.source, stage, follow_symlinks=False)
            os.fsync(output.fileno())
        _check_cancel(cancel)
        _check_source(item)
        _check_directory_path(item.target.parent)
        # link() is atomic and fails if the target exists. Never fall back to
        # rename/replace: filesystems without hard links must fail closed.
        try:
            os.link(stage, item.target, follow_symlinks=False)
        except FileExistsError:
            status, message = "skipped", "Target appeared during copy; source kept."
        else:
            published = True
            if os.name == "posix" and dirs is not None:
                dirs.add(item.target.parent)
            _check_cancel(cancel)
            _check_source(item)
            target_info = item.target.lstat()
            if (target_info.st_dev, target_info.st_ino) != stage_identity:
                raise ValueError("Published target changed; source will not be removed")
            item.source.unlink()
            status, message = "moved", "Verified copy published and source removed."
    except _Cancelled:
        status, message = "cancelled", "Cancelled; source kept."
        if published:
            message += " Verified copy retained at destination."
    except (OSError, ValueError, shutil.Error) as exc:
        status, message = "error", f"{exc}; source kept."
        if published:
            message += " Verified copy retained at destination; source could not be safely removed."
    finally:
        if stage is not None and stage_identity is not None:
            try:
                info = stage.lstat()
                if (info.st_dev, info.st_ino) == stage_identity:
                    stage.unlink()
                else:
                    raise OSError(f"Temporary path changed; left untouched: {stage}")
            except OSError as exc:
                message += f" Temporary cleanup failed: {exc}"
                if status == "moved":
                    status = "error"
    return TransferResult(item, status, message)


def execute_plan(plan: Plan, *, cancel: Event | None = None) -> Iterator[TransferResult]:
    """Execute a reviewed snapshot, reporting each processed item.

    Cancellation emits one cancelled result for the current/next item and stops.
    A cancelled (incomplete) plan is never executed. Other errors do not stop
    subsequent items. Plans without source fingerprints fail closed.
    """
    import concurrent.futures

    # Cancelled plan: yield first item as cancelled and stop
    if plan.cancelled or (cancel is not None and cancel.is_set()):
        if plan.items:
            yield TransferResult(plan.items[0], "cancelled", "Cancelled; source kept.")
        return

    # Yield non-ready items first
    for item in plan.items:
        if item.status not in ("ready", "deduplicate"):
            yield TransferResult(item, "skipped" if item.status == "skipped" else "error", item.reason)

    # Collect ready items
    ready = [item for item in plan.items if item.status in ("ready", "deduplicate")]
    if not ready:
        return

    # Batch directory fsync
    dirs_to_fsync: set[Path] = set()

    # Parallel execution, yielding in plan order
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            (executor.submit(_transfer, item, cancel, dirs_to_fsync), item)
            for item in ready
        ]
        for future, item in futures:
            if cancel and cancel.is_set():
                future.cancel()
                continue
            try:
                yield future.result()
            except _Cancelled:
                yield TransferResult(item, "cancelled", "Cancelled; source kept.")
                return

    # Batch fsync all published directories (POSIX only)
    if os.name == "posix":
        for d in dirs_to_fsync:
            try:
                fd = os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
