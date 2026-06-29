from app import create_app, logger


application = create_app()


@application.get("/health")
def healthcheck():
    checks = {
        "app": "ok",
        "nextcloud": "unknown",
    }

    try:
        storage = application.extensions["services"].storage
        storage.ensure_base_structure()
        checks["nextcloud"] = "ok"
    except Exception as exc:
        logger.warning("Healthcheck Nextcloud failed: %s", exc)
        checks["nextcloud"] = "error"
        return checks, 503

    return checks, 200
