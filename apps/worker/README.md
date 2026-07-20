# Creative Asset Manager worker

The executable entrypoint is `main.py`. It shares the API application's typed
configuration, SQLAlchemy models/repositories, and provider contracts.

From the repository root:

```bash
apps/api/.venv/bin/python apps/worker/main.py
```

Processing is intentionally inert by default. Set
`PROCESSING_JOBS_ENABLED=true` only in a controlled worker deployment. Health
is served on `127.0.0.1:8081` by default.

See `docs/operations/WORKER_RUNTIME.md` for configuration, health endpoints,
lease behavior, graceful shutdown, and recovery.
