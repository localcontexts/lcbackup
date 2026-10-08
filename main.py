
import os
import subprocess
import sys
import time

from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import boto3


TIME_FORMAT = '%Y:%m:%d:%H:%M:%S'

Interval = namedtuple('Interval', ['name', 'max_backups'])

INTERVALS = [
    Interval('day', 7),
    Interval('week', 5),
    Interval('month', 12),
    Interval('year', 4),
]

CURRENT_FOLDER = Path(__file__).parent.absolute()
DUMPED_FILES_FOLDER = CURRENT_FOLDER / 'dumped_files'
DUMP_FILE = DUMPED_FILES_FOLDER / 'dumped.psql'

DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_PORT = os.environ.get('DB_PORT')
DB_USER = os.environ.get('DB_USER')
DB_PASS = os.environ.get('DB_PASS')

DEBUG = os.environ.get('DEBUG_VALUE')

ACCESS_KEY = os.environ.get('DBBACKUP_ACCESS_KEY')
SECRET_KEY = os.environ.get('DBBACKUP_SECRET_KEY')
BUCKET_NAME = os.environ.get('DBBACKUP_BUCKET_NAME')
ENDPOINT_URL = os.environ.get('DBBACKUP_ENDPOINT_URL')


class Storage:
    def __init__(self, access_key, secret_key, bucket_name, endpoint_url):
        if not all([access_key, secret_key, bucket_name, endpoint_url]):
            raise RuntimeError('S3 bucket is not configured')

        self.bucket_name = bucket_name

        self.client = boto3.session.Session().client(
            's3',
            region_name='nyc3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def write_file(self, local_file_path: str, object_key: str):
        self.client.upload_file(
            local_file_path,
            self.bucket_name,
            object_key,
        )

        # Confirm the uploaded object exists and has content.
        response = self.client.head_object(
            Bucket=self.bucket_name,
            Key=object_key,
        )

        if response['ContentLength'] <= 0:
            raise RuntimeError(
                f'Uploaded backup is empty: {object_key}'
            )

    def delete_file(self, object_key: str):
        self.client.delete_object(
            Bucket=self.bucket_name,
            Key=object_key,
        )

    def list_directory(self, directory_path: str) -> List[str]:
        # S3 returns full object keys, not just filenames.
        prefix = directory_path.rstrip('/') + '/'

        paginator = self.client.get_paginator('list_objects_v2')

        files = []

        for page in paginator.paginate(
            Bucket=self.bucket_name,
            Prefix=prefix,
        ):
            for item in page.get('Contents', []):
                key = item['Key']

                if key.endswith('.psql'):
                    files.append(key)

        return sorted(files)


class BaseCommand:
    @staticmethod
    def print_error(message: str):
        print(message, file=sys.stderr, flush=True)

    @staticmethod
    def print_info(message: str):
        print(message, file=sys.stdout, flush=True)


class DB_CONNECTOR:
    def __init__(self, host, name, port, user, password):
        self.settings = {
            'HOST': host,
            'NAME': name,
            'PORT': port,
            'USER': user,
            'PASSWORD': password,
        }

        if not all(self.settings.values()):
            raise RuntimeError('Database connection is not configured')

    def dump(self, output_file_path):
        env = os.environ.copy()
        env['PGPASSWORD'] = self.settings['PASSWORD']

        cmd = [
            'pg_dump',
            '-U', self.settings['USER'],
            '-h', self.settings['HOST'],
            '-p', str(self.settings['PORT']),
            '-d', self.settings['NAME'],
            '-f', str(output_file_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
                check=True,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                'Database dump exceeded the 300-second timeout'
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f'Database dump failed: {e.stderr}'
            ) from e

        if not output_file_path.is_file():
            raise RuntimeError('Database dump file was not created')

        if output_file_path.stat().st_size == 0:
            raise RuntimeError('Database dump file is empty')


class Command(BaseCommand):
    def __init__(self):
        self.storage = Storage(
            ACCESS_KEY,
            SECRET_KEY,
            BUCKET_NAME,
            ENDPOINT_URL,
        )

        self.db = DB_CONNECTOR(
            DB_HOST,
            DB_NAME,
            DB_PORT,
            DB_USER,
            DB_PASS,
        )

        self.env = 'prod' if DEBUG == 'False' else 'dev'

    @staticmethod
    def truncate_datetime(dt: datetime, interval_name: str) -> datetime:
        if interval_name == 'year':
            return datetime(dt.year, 1, 1)

        if interval_name == 'month':
            return datetime(dt.year, dt.month, 1)

        if interval_name == 'week':
            return datetime(dt.year, dt.month, dt.day) - timedelta(
                days=dt.weekday()
            )

        if interval_name == 'day':
            return datetime(dt.year, dt.month, dt.day)

        raise ValueError(f'Invalid interval: {interval_name}')

    @staticmethod
    def remove_dumped_file():
        if DUMP_FILE.exists():
            DUMP_FILE.unlink()

    def create_backup(self, object_key: str):
        if not DUMP_FILE.is_file():
            self.print_info('Creating database dump')
            self.db.dump(DUMP_FILE)

        if DUMP_FILE.stat().st_size == 0:
            raise RuntimeError('Database dump file is empty')

        self.print_info(f'Uploading backup: {object_key}')
        self.storage.write_file(str(DUMP_FILE), object_key)

        self.print_info(f'Backup uploaded successfully: {object_key}')

    def should_save_new_file(
        self,
        interval: Interval,
        files: List[str],
    ) -> bool:
        if not files:
            return True

        most_recent_file = Path(files[-1]).stem
        most_recent_datetime = datetime.strptime(
            most_recent_file,
            TIME_FORMAT,
        )

        last_backup_period = self.truncate_datetime(
            most_recent_datetime,
            interval.name,
        )

        current_period = self.truncate_datetime(
            datetime.now(),
            interval.name,
        )

        return last_backup_period != current_period

    def remove_oldest_files(
        self,
        interval: Interval,
        files: List[str],
    ):
        # Never delete backups before a successful upload.
        # Only remove files exceeding the retention limit.
        files = sorted(files)

        while len(files) > interval.max_backups:
            oldest_file = files[0]

            self.print_info(
                f'Deleting old {interval.name} backup: {oldest_file}'
            )

            self.storage.delete_file(oldest_file)
            files.pop(0)

    def job(self):
        DUMPED_FILES_FOLDER.mkdir(parents=True, exist_ok=True)

        # Start each execution with a fresh dump.
        self.remove_dumped_file()

        try:
            for interval in INTERVALS:
                directory = f'{self.env}/{interval.name}'

                files = self.storage.list_directory(directory)

                if not self.should_save_new_file(interval, files):
                    self.print_info(
                        f'Backup already exists for {interval.name}'
                    )
                    continue

                filename = f'{time.strftime(TIME_FORMAT)}.psql'
                object_key = f'{directory}/{filename}'

                self.create_backup(object_key)

                files.append(object_key)

                self.remove_oldest_files(interval, files)

            self.print_info('Backup Script Complete')

        finally:
            self.remove_dumped_file()

    def run(self):
        self.print_info('Running Backup Script')
        self.job()


if __name__ == '__main__':
    try:
        command = Command()
        command.run()
    except Exception as e:
        print(
            f'Backup job failed: {e}',
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
