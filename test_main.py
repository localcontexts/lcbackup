
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main


class TestBackupJob(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.dump_file = Path(self.temp_dir.name) / "dumped.psql"

        self.dump_path_patch = patch.object(
            main, "DUMP_FILE", self.dump_file
        )
        self.dump_folder_patch = patch.object(
            main, "DUMPED_FILES_FOLDER", Path(self.temp_dir.name)
        )

        self.dump_path_patch.start()
        self.dump_folder_patch.start()

        self.addCleanup(self.dump_path_patch.stop)
        self.addCleanup(self.dump_folder_patch.stop)

        # Bypass real S3 and database initialization.
        with patch.object(main, "Storage"), patch.object(
            main, "DB_CONNECTOR"
        ):
            self.command = main.Command()

        self.command.env = "prod"

        self.objects = {
            "prod/day/2026:10:01:00:00:00.psql",
            "prod/day/2026:10:02:00:00:00.psql",
            "prod/day/2026:10:03:00:00:00.psql",
            "prod/day/2026:10:04:00:00:00.psql",
            "prod/day/2026:10:05:00:00:00.psql",
            "prod/day/2026:10:06:00:00:00.psql",
            "prod/day/2026:10:07:00:00:00.psql",
        }

        def list_directory(prefix):
            return sorted(
                key for key in self.objects
                if key.startswith(prefix + "/")
            )

        def dump(path):
            Path(path).write_bytes(b"mock database contents")

        def upload(local_path, key):
            if not Path(local_path).is_file():
                raise RuntimeError("Dump file missing")
            self.objects.add(key)

        def delete(key):
            if key not in self.objects:
                raise RuntimeError(f"Object does not exist: {key}")
            self.objects.remove(key)

        self.command.storage.list_directory.side_effect = list_directory
        self.command.storage.write_file.side_effect = upload
        self.command.storage.delete_file.side_effect = delete
        self.command.db.dump.side_effect = dump

    @patch.object(main.time, "strftime", return_value="2026:10:08:00:00:00")
    def test_successful_backup_and_retention(self, mock_time):
        with patch.object(
            self.command,
            "should_save_new_file",
            return_value=True,
        ):
            self.command.job()

    def test_upload_failure_does_not_delete_backups(self):
        self.command.storage.write_file.side_effect = RuntimeError(
            "Simulated S3 upload failure"
        )

        with self.assertRaisesRegex(RuntimeError, "Simulated S3"):
            self.command.job()

        self.command.storage.delete_file.assert_not_called()
        self.assertEqual(len(self.objects), 7)
        self.assertFalse(self.dump_file.exists())

    def test_empty_dump_is_rejected(self):
        self.command.db.dump.side_effect = (
            lambda path: Path(path).write_bytes(b"")
        )

        with self.assertRaisesRegex(RuntimeError, "empty"):
            self.command.job()

        self.command.storage.write_file.assert_not_called()
        self.command.storage.delete_file.assert_not_called()
        self.assertFalse(self.dump_file.exists())

    def test_listing_failure_stops_job(self):
        self.command.storage.list_directory.side_effect = RuntimeError(
            "Simulated S3 listing failure"
        )

        with self.assertRaisesRegex(RuntimeError, "Simulated S3"):
            self.command.job()

        self.command.storage.write_file.assert_not_called()
        self.command.storage.delete_file.assert_not_called()

    @patch.object(main.time, "strftime", return_value="2026:10:08:00:00:00")
    def test_deletion_failure_stops_job(self, mock_time):
        self.command.storage.delete_file.side_effect = RuntimeError(
            "Simulated S3 deletion failure"
        )

        with patch.object(
            self.command,
            "should_save_new_file",
            return_value=True,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Simulated S3 deletion failure"
            ):
                self.command.job()

        # New backup was uploaded before retention was attempted.
        self.assertIn(
            "prod/day/2026:10:08:00:00:00.psql",
            self.objects,
        )

        # Existing backup was not deleted.
        self.assertIn(
            "prod/day/2026:10:01:00:00:00.psql",
            self.objects,
        )

        # The job stopped before processing weekly backups.
        self.assertNotIn(
            "prod/week/2026:10:08:00:00:00.psql",
            self.objects,
        )

        self.assertFalse(self.dump_file.exists())

    @patch.object(main.time, "strftime", return_value="2026:10:08:00:00:00")
    def test_existing_backup_period_is_skipped(self, mock_time):
        from datetime import datetime as real_datetime

        # Add backups from the current day, week, month, and year.
        self.objects.update({
            "prod/day/2026:10:08:00:00:00.psql",
            "prod/week/2026:10:06:00:00:00.psql",
            "prod/month/2026:10:01:00:00:00.psql",
            "prod/year/2026:01:01:00:00:00.psql",
        })

        # Freeze the current date without replacing datetime.strptime().
        class MockDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 8, 12, 0, 0)

        with patch.object(main, "datetime", MockDatetime):
            self.command.job()

        # No new database dump or upload should occur.
        self.command.db.dump.assert_not_called()
        self.command.storage.write_file.assert_not_called()
        self.command.storage.delete_file.assert_not_called()

        self.assertFalse(self.dump_file.exists())

if __name__ == "__main__":
    unittest.main()
