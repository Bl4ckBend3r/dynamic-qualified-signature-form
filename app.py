from __future__ import annotations

import sys

import legacy_app as _legacy_app


def create_app(config_object=None):
    """Application factory for the transitional architecture.

    The current route implementation is preserved in legacy_app while services,
    repositories, validators and blueprints are introduced around it.
    """

    if config_object is not None:
        _legacy_app.app.config.from_object(config_object)
    return _legacy_app.app


_legacy_app.create_app = create_app
sys.modules[__name__] = _legacy_app


if __name__ == "__main__":
    _legacy_app.app.run(
        debug=_legacy_app.app.config["DEBUG"],
        host="127.0.0.1",
        port=5000,
    )
