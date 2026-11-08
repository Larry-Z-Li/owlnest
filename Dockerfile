# How Render builds the server: the one place her records live, which every installed app talks to.
# See README, "The server". (Her computers don't use this file.)

FROM python:3.12-slim-trixie

# Fonts for report PDFs (Liberation Sans looks like Arial; Droid Sans Fallback has Chinese
# characters), and time zone data, so "today" on the server is her day.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation fonts-droid-fallback tzdata \
    && rm -rf /var/lib/apt/lists/*

# Her time zone: rent due dates, alerts and the monthly draft invoices follow it.
ENV TZ=America/Los_Angeles
# The data folder, on Render's disk (mounted at /var/data), which survives restarts and updates.
ENV PROPERTY_MANAGER_DATA=/var/data/PropertyManager/data
# Show messages in Render's log straight away.
ENV PYTHONUNBUFFERED=1

# Render's SSH needs this folder, for copying files to and from the disk (see the README).
RUN mkdir -p /root/.ssh && chmod 0700 /root/.ssh

WORKDIR /app
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt
COPY property_manager ./property_manager

# One worker (see property_manager/server.py). Render chooses the port, in $PORT.
CMD gunicorn --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:${PORT:-10000} "property_manager.server:build_server_app()"
