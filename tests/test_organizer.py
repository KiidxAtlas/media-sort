"""Safety contracts exercised against real files, with injected I/O failures."""

import errno
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from media_sort import organizer
from media_sort.organizer import build_plan, execute_plan


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "destination"

    def media(self, name="clip.MP4", data=b"original media", parent=None):
        path = (parent or self.source) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self):
        return build_plan([self.source], [], self.destination, None)

    def assert_no_partials(self):
        self.assertEqual(list(self.root.rglob("*.partial")), [])

    def test_preview_is_read_only_and_verified_move_preserves_metadata(self):
        source = self.media()
        source.chmod(0o640)
        os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))
        original = source.stat()
        plan = self.plan()
        self.assertFalse(self.destination.exists())
        self.assertEqual(source.read_bytes(), b"original media")
        self.assertEqual(plan.items[0].target, self.destination / "mp4" / source.name)
        result, = execute_plan(plan)
        self.assertEqual(result.status, "moved")
        self.assertFalse(source.exists())
        self.assertEqual(result.item.target.stat().st_mtime_ns, original.st_mtime_ns)
        if os.name == "posix":
            self.assertEqual(result.item.target.stat().st_mode & 0o777, 0o640)
        self.assert_no_partials()

    def test_overlapping_roots_and_hardlinked_files_are_deduplicated(self):
        nested = self.source / "nested"
        source = self.media(parent=nested)
        os.link(source, self.source / "alias.mp4")
        plan = build_plan([nested, self.source, nested], [], self.destination, None)
        self.assertEqual([item.source for item in plan.items], [source])

    def test_both_destinations_pruned_from_all_categories(self):
        videos = self.source / "sorted-videos"
        images = self.source / "sorted-images"
        self.media("old.mp4", parent=videos)
        self.media("old.jpg", parent=videos)
        self.media("other.mp4", parent=images)
        self.media("other.jpg", parent=images)
        video = self.media("new.mp4")
        image = self.media("new.jpg")
        plan = build_plan([self.source], [self.source], videos, images)
        self.assertEqual({item.source for item in plan.items}, {video, image})

    def test_invalid_roots_and_destination_relationships_raise(self):
        regular = self.media()
        for sources, destination in (
            ([self.source], None),
            ([self.root / "missing"], self.destination),
            ([regular], self.destination),
            ([self.source], regular),
            ([self.source], self.source),
            ([self.source], self.root),
            ([self.source], regular / "nested"),
        ):
            with (
                self.subTest(sources=sources, destination=destination),
                self.assertRaises(ValueError),
            ):
                build_plan(sources, [], destination, None)

    def test_cross_category_source_inside_destination_rejected(self):
        with self.assertRaises(ValueError):
            build_plan([self.source], [], self.destination, self.root)

    def test_symlinks_are_not_followed_and_link_roots_rejected(self):
        real = self.media()
        (self.source / "alias.mp4").symlink_to(real)
        (self.source / "loop").symlink_to(self.source, target_is_directory=True)
        plan = self.plan()
        self.assertEqual([item.source for item in plan.items], [real])
        self.assertEqual(len(plan.warnings), 2)
        with self.assertRaises(ValueError):
            build_plan([self.source / "loop"], [], self.destination, None)
        linked_dest = self.root / "linked-destination"
        linked_dest.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            build_plan([self.source], [], linked_dest, None)

    def test_inaccessible_descendant_warns_and_other_files_still_plan(self):
        good = self.media()
        blocked = self.source / "blocked"
        self.media(parent=blocked)
        scan = os.scandir

        def inaccessible(path):
            if Path(path) == blocked:
                raise PermissionError("access denied")
            return scan(path)

        with patch.object(organizer.os, "scandir", side_effect=inaccessible):
            plan = self.plan()
        self.assertEqual([item.source for item in plan.items], [good])
        self.assertTrue(any(str(blocked) in warning for warning in plan.warnings))

    def test_collision_reservations_survive_first_transfer_failure(self):
        first = self.media("clip.mp4")
        second = self.media("clip.mp4", b"other", parent=self.source / "nested")
        plan = self.plan()
        self.assertEqual([item.status for item in plan.items], ["ready", "skipped"])
        first.write_bytes(b"changed after preview")
        results = list(execute_plan(plan))
        self.assertEqual([result.status for result in results], ["error", "skipped"])
        self.assertEqual(second.read_bytes(), b"other")
        self.assertFalse(plan.items[0].target.exists())

    def test_existing_and_dangling_targets_never_overwritten(self):
        source = self.media()
        target = self.destination / "mp4" / source.name
        target.parent.mkdir(parents=True)
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                if dangling:
                    target.symlink_to(self.root / "missing")
                else:
                    target.write_bytes(b"user target")
                result, = execute_plan(self.plan())
                self.assertEqual(result.status, "skipped")
                self.assertEqual(source.read_bytes(), b"original media")
                if dangling:
                    self.assertTrue(target.is_symlink())
                else:
                    self.assertEqual(target.read_bytes(), b"user target")
                target.unlink()

    def test_source_changed_after_preview_is_not_moved(self):
        source = self.media()
        plan = self.plan()
        source.write_bytes(b"edited")
        result, = execute_plan(plan)
        self.assertEqual(result.status, "error")
        self.assertEqual(source.read_bytes(), b"edited")
        self.assertFalse(result.item.target.exists())

    def test_replaced_source_with_same_size_and_mtime_is_not_moved(self):
        source = self.media()
        plan = self.plan()
        original = source.stat()
        replacement = self.source / "replacement"
        replacement.write_bytes(source.read_bytes())
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        replacement.replace(source)
        result, = execute_plan(plan)
        self.assertEqual(result.status, "error")
        self.assertTrue(source.exists())
        self.assertFalse(result.item.target.exists())

    def test_late_target_collision_preserves_both_files(self):
        source = self.media()
        plan = self.plan()
        link = os.link

        def publish(stage, target, **kwargs):
            target.write_bytes(b"concurrent user file")
            return link(stage, target, **kwargs)

        with patch.object(organizer.os, "link", side_effect=publish):
            result, = execute_plan(plan)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(source.read_bytes(), b"original media")
        self.assertEqual(result.item.target.read_bytes(), b"concurrent user file")
        self.assert_no_partials()

    def test_copy_verify_metadata_and_publish_failures_keep_source(self):
        source = self.media()
        for operation, injected in (
            ("os.fsync", OSError(errno.ENOSPC, "disk full")),
            ("_hash_file", b"wrong hash"),
            ("shutil.copystat", PermissionError("metadata denied")),
            ("os.link", OSError(errno.EOPNOTSUPP, "hard links unsupported")),
        ):
            with self.subTest(operation=operation):
                plan = self.plan()
                kwargs = {"side_effect": injected} if isinstance(injected, Exception) else {"return_value": injected}
                with patch(f"media_sort.organizer.{operation}", **kwargs):
                    result, = execute_plan(plan)
                self.assertEqual(result.status, "error")
                self.assertEqual(source.read_bytes(), b"original media")
                self.assertFalse(result.item.target.exists())
                self.assert_no_partials()

    def test_source_removal_failure_reports_error_and_keeps_verified_copy(self):
        source = self.media()
        plan = self.plan()
        unlink = Path.unlink

        def cannot_remove(path, *args, **kwargs):
            if path == source:
                raise PermissionError("source removal denied")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=cannot_remove):
            result, = execute_plan(plan)
        self.assertEqual(result.status, "error")
        self.assertEqual(source.read_bytes(), b"original media")
        self.assertEqual(result.item.target.read_bytes(), b"original media")
        self.assertIn("retained", result.message)
        self.assert_no_partials()

    def test_cancellation_before_scan_and_execution(self):
        source = self.media()
        plan = self.plan()
        cancel = Event()
        cancel.set()
        stopped = build_plan([self.source], [], self.destination, None, cancel=cancel)
        self.assertTrue(stopped.cancelled)
        self.assertEqual(stopped.items, ())
        result, = execute_plan(plan, cancel=cancel)
        self.assertEqual(result.status, "cancelled")
        self.assertTrue(source.exists())
        self.assertFalse(self.destination.exists())

    def test_cancellation_mid_scan_returns_incomplete_nonexecutable_plan(self):
        self.media("a.mp4")
        self.media("b.mp4")
        cancel = Event()
        scan = organizer._scan

        def stop_after_first(*args, **kwargs):
            for entry in scan(*args, **kwargs):
                yield entry
                cancel.set()

        with patch.object(organizer, "_scan", side_effect=stop_after_first):
            plan = build_plan([self.source], [], self.destination, None, cancel=cancel)
        self.assertTrue(plan.cancelled)
        self.assertEqual([result.status for result in execute_plan(plan)], ["cancelled"])
        self.assertFalse(self.destination.exists())

    def test_cancellation_during_copy_discards_only_own_temporary(self):
        source = self.media(data=b"x" * (organizer._CHUNK_SIZE * 2))
        plan = self.plan()
        cancel = Event()
        digest = organizer.hashlib.sha256

        class CancelAfterChunk:
            def __init__(self):
                self.inner = digest()

            def update(self, chunk):
                self.inner.update(chunk)
                cancel.set()

            def digest(self):
                return self.inner.digest()

        with patch.object(organizer.hashlib, "sha256", CancelAfterChunk):
            result, = execute_plan(plan, cancel=cancel)
        self.assertEqual(result.status, "cancelled", result.message)
        self.assertEqual(source.stat().st_size, organizer._CHUNK_SIZE * 2)
        self.assertFalse(result.item.target.exists())
        self.assert_no_partials()

    def test_cancellation_after_publish_retains_both_copies(self):
        source = self.media()
        plan = self.plan()
        cancel = Event()
        link = os.link

        def cancel_after_publish(*args, **kwargs):
            link(*args, **kwargs)
            cancel.set()

        with patch.object(organizer.os, "link", side_effect=cancel_after_publish):
            result, = execute_plan(plan, cancel=cancel)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(source.read_bytes(), b"original media")
        self.assertEqual(result.item.target.read_bytes(), b"original media")
        self.assert_no_partials()

    def test_one_file_error_does_not_abort_remaining_transfers(self):
        first = self.media("a.mp4")
        second = self.media("b.mp4")
        plan = self.plan()
        first.write_bytes(b"edited after preview")
        results = list(execute_plan(plan))
        self.assertEqual([result.status for result in results], ["error", "moved"])
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(results[1].item.target.read_bytes(), b"original media")

    def test_case_alias_collision_preview_matches_execution(self):
        first = self.media("clip.mp4")
        alias = first.with_name("CLIP.MP4")
        if not alias.exists():
            self.skipTest("Filesystem is case-sensitive")
        second = self.media("CLIP.MP4", b"second", parent=self.source / "nested")
        plan = self.plan()
        self.assertEqual([item.status for item in plan.items], ["ready", "skipped"])
        self.assertEqual([result.status for result in execute_plan(plan)], ["moved", "skipped"])
        self.assertEqual(second.read_bytes(), b"second")

    def test_source_changed_at_publish_retains_verified_copy(self):
        source = self.media()
        plan = self.plan()
        link = os.link

        def edit_after_publish(*args, **kwargs):
            link(*args, **kwargs)
            source.write_bytes(b"new user edit")

        with patch.object(organizer.os, "link", side_effect=edit_after_publish):
            result, = execute_plan(plan)
        self.assertEqual(result.status, "error")
        self.assertEqual(source.read_bytes(), b"new user edit")
        self.assertEqual(result.item.target.read_bytes(), b"original media")
        self.assert_no_partials()

    def test_case_aliased_destination_is_pruned_and_root_rejected(self):
        if not self.source.with_name("SOURCE").exists():
            self.skipTest("Filesystem is case-sensitive")
        destination = self.source / "Sorted"
        self.media("old.mp4", parent=destination)
        source = self.media("new.mp4")
        plan = build_plan([self.source], [], destination.with_name("SORTED"), None)
        self.assertEqual([item.source for item in plan.items], [source])
        with self.assertRaises(ValueError):
            build_plan([self.source], [], self.source.with_name("SOURCE"), None)

    def test_destination_link_inserted_after_preview_keeps_source(self):
        source = self.media()
        plan = self.plan()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.destination.symlink_to(elsewhere, target_is_directory=True)
        result, = execute_plan(plan)
        self.assertEqual(result.status, "error")
        self.assertEqual(source.read_bytes(), b"original media")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_user_partial_file_is_never_cleaned_up(self):
        self.media()
        self.destination.mkdir()
        user_file = self.destination / "important.partial"
        user_file.write_bytes(b"user data")
        result, = execute_plan(self.plan())
        self.assertEqual(result.status, "moved")
        self.assertEqual(user_file.read_bytes(), b"user data")


if __name__ == "__main__":
    unittest.main()
