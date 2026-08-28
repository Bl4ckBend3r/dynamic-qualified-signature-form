"""Isolated WSGI application used only by the synthetic performance workflow."""

from app import create_app


class SyntheticPerformanceStorage:
    form_filename = "performance-synthetic.json"

    @staticmethod
    def ensure_base_structure() -> None:
        return None

    @staticmethod
    def ensure_outputs_for_all_forms() -> None:
        return None

    @staticmethod
    def ensure_form_output_structure(_slug: str) -> None:
        return None

    def list_form_files(self) -> list[str]:
        return [self.form_filename]

    def read_form_json(self, filename: str) -> dict:
        if filename != self.form_filename:
            raise FileNotFoundError(filename)
        return {
            "title": "Synthetic performance form",
            "description": "Generated test data only",
            "fields": [],
            "documents": [],
            "workflow": {},
        }


application = create_app(storage_override=SyntheticPerformanceStorage())
