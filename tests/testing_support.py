from __future__ import annotations

import csv
import io


class InMemoryStorage:
    """Shared in-memory storage adapter for unit, integration and browser tests."""

    def __init__(self, form_definition):
        self.form_definition = form_definition
        self.form_filename = "formularz_zgloszeniowy.json"
        self.output_dir = "output"
        self.csv_filename = "dane.csv"
        self.saved_pdfs = {}
        self.csv_rows = []
        self.direct_files = {}

    def ensure_base_structure(self):
        return None

    def ensure_outputs_for_all_forms(self):
        return None

    def ensure_form_output_structure(self, slug):
        return None

    def mkdir(self, path):
        return None

    def write_bytes(self, path, content, content_type=None):
        self.direct_files[path] = bytes(content)

    def read_bytes(self, path):
        if path not in self.direct_files:
            raise FileNotFoundError(path)
        return self.direct_files[path]

    def delete(self, path, missing_ok=False):
        if path in self.direct_files:
            del self.direct_files[path]
        elif not missing_ok:
            raise FileNotFoundError(path)

    def list_form_files(self):
        return [self.form_filename]

    def read_form_json(self, filename):
        if filename != self.form_filename:
            raise FileNotFoundError(filename)
        return self.form_definition

    def exists(self, path):
        if path.endswith(f"/{self.csv_filename}") and self.csv_rows:
            return True
        return path in self.direct_files or path in self.saved_pdfs

    def save_pdf(self, slug, filename, pdf_bytes, **kwargs):
        self.saved_pdfs[f"output/{slug}/pdf/{filename}"] = pdf_bytes

    def get_pdf_bytes(self, slug, filename):
        return self.saved_pdfs[f"output/{slug}/pdf/{filename}"]

    def get_file_bytes(self, path):
        if path.endswith(f"/{self.csv_filename}"):
            return self.read_text_or_empty(path).encode("utf-8")
        if path in self.direct_files:
            return self.direct_files[path]
        return self.saved_pdfs[path]

    def read_text_or_empty(self, path):
        if path.endswith(self.csv_filename):
            if not self.csv_rows:
                return ""
            fieldnames = []
            for row in self.csv_rows:
                for key in row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.csv_rows)
            return buffer.getvalue()
        return (
            self.direct_files.get(path, b"").decode("utf-8")
            if path in self.direct_files
            else ""
        )

    def append_csv_row(self, slug, row):
        self.csv_rows.append(dict(row))

    def read_csv_rows(self, slug):
        return list(self.csv_rows)

    def update_csv_row_by_submission_id(self, slug, submission_id, updates):
        for row in self.csv_rows:
            if row.get("submission_id") == submission_id:
                row.update(updates)
                return True
        return False
