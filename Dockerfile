
FROM python:3.12-slim-bookworm

# Install PostgreSQL client tools, including pg_dump.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        postgresql-client-15 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory.
WORKDIR /home

# Install Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files.
COPY main.py .
COPY run.sh .

# Create directory for temporary database dumps.
RUN mkdir -p /home/dumped_files && \
    chmod +x /home/run.sh

# Placeholder for environment configuration mounted by Cloud Run.
RUN mkdir -p /envs && \
    touch /envs/env-local.sh

ENTRYPOINT ["./run.sh"]
