from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from services.form_config_service import FormConfigService
from validators.form_config_validator import FormConfigValidator


def validate_form(filename: str, skip_template_check: bool = False, template_root: str | Path | None = None) -> int:
    path = Path(filename)
    with path.open("r", encoding="utf-8") as handle:
        raw_config = json.load(handle)
    form_config = FormConfigService().normalize_form_config(raw_config)
    errors = FormConfigValidator(
        template_root=template_root or Path.cwd() / "templates",
        skip_template_check=skip_template_check,
    ).validate(form_config)
    if errors:
        print("Form config validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Form config validation passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate-form")
    validate_parser.add_argument("filename")
    validate_parser.add_argument("--skip-template-check", action="store_true")
    validate_parser.add_argument("--template-root")
    args = parser.parse_args()
    if args.command == "validate-form":
        return validate_form(
            args.filename,
            skip_template_check=args.skip_template_check,
            template_root=args.template_root,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
