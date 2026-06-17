# gunicorn_config.py
# Gunicorn is the production WSGI server that replaces Flask's built-in dev server.
# Flask's dev server (app.run) is single-threaded and not safe for concurrent requests.
# Gunicorn handles multiple simultaneous webhook POSTs without queuing or crashing.

import os

# --- Workers ---
# Each worker is an independent OS process that handles requests independently.
# Formula: (2 x CPU cores) + 1. Render free tier has 1 vCPU → 3 workers.
# Each worker can handle one request at a time; Gunicorn load-balances across them.
workers = int(os.environ.get("GUNICORN_WORKERS", 3))

# --- Threads per worker ---
# Using threads within each worker lets a single worker handle multiple concurrent
# requests. This is what prevents the "queuing" delay when webhooks arrive in bursts.
# The threading.Lock in webhook_server.py ensures file writes stay safe across threads.
threads = int(os.environ.get("GUNICORN_THREADS", 4))

# --- Binding ---
# Render injects the PORT env var; default to 10000 to match webhook_server.py
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# --- Timeouts ---
# How long a worker can take to handle a request before Gunicorn kills and restarts it.
# 30s is enough for webhook receipt; keeps the server from hanging on slow clients.
timeout = 30

# --- Keep-alive ---
# Seconds to wait for the next request on a persistent connection.
# Language Cloud may reuse connections for bursts; 5s is a safe default.
keepalive = 5

# --- Logging ---
accesslog = "-"   # stdout — Render captures this in its log dashboard
errorlog = "-"    # stderr
loglevel = "info"

# --- Worker class ---
# "gthread" = threaded sync workers. Correct choice here since our work is
# I/O-bound (file writes) with no async framework. Don't use "gevent" or
# "eventlet" unless you've audited the file-lock code for greenlet safety.
worker_class = "gthread"
