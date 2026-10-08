#!/usr/bin/env sh
set -eu

# Load environment variables from the mounted secret.
. /envs/env-local.sh

# Run the backup script.
exec python main.py
