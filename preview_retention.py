import argparse

from main import (
    Storage,
    ACCESS_KEY,
    SECRET_KEY,
    BUCKET_NAME,
    ENDPOINT_URL,
    INTERVALS,
)


def main():
    parser = argparse.ArgumentParser(
        description="Preview backup retention without deleting files."
    )
    parser.add_argument(
        "--env",
        choices=["prod", "dev"],
        default="prod",
        help="Environment to preview (default: prod)",
    )
    args = parser.parse_args()

    storage = Storage(
        ACCESS_KEY,
        SECRET_KEY,
        BUCKET_NAME,
        ENDPOINT_URL,
    )

    total_keep = 0
    total_delete = 0

    for interval in INTERVALS:
        directory = f"{args.env}/{interval.name}"

        # Read existing backups without modifying anything.
        objects = storage.list_directory(directory)

        # Match the backup job's retention ordering.
        objects = sorted(objects, reverse=True)

        keep = objects[:interval.max_backups]
        delete = objects[interval.max_backups:]

        print(f"\n{'=' * 60}")
        print(f"{interval.name.upper()} BACKUPS")
        print(f"Existing: {len(objects)}")
        print(f"Retention limit: {interval.max_backups}")

        print("\nKEEP:")
        for key in keep:
            print(f"  {key}")

        print("\nWOULD DELETE:")
        for key in delete:
            print(f"  {key}")

        total_keep += len(keep)
        total_delete += len(delete)

    print(f"\n{'=' * 60}")
    print("TOTALS")
    print(f"Keep: {total_keep}")
    print(f"Would delete: {total_delete}")


if __name__ == "__main__":
    main()