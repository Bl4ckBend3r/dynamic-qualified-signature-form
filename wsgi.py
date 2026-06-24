from app import app, logger, storage


@app.get("/health")
def healthcheck():
    checks = {
        "app": "ok",
        "nextcloud": "unknown",
    }

    try:
        storage.ensure_base_structure()
        checks["nextcloud"] = "ok"
    except Exception as exc:
        logger.warning("Healthcheck Nextcloud failed: %s", exc)
        checks["nextcloud"] = "error"
        return checks, 503

    return checks, 200


application = app
