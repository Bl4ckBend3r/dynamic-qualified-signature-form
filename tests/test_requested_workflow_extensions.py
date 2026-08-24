from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, DecisionTypeDefinition, Form, FormSubmission, FormVersion, RepeatableGroupItemDecision
from services.admin_submission_service import format_business_datetime
from services.decision_definition_service import DecisionDefinitionError, DecisionDefinitionService
from services.form_submission_mapper import build_submission_from_form
from services.form_version_service import FormVersionService
from signature_verifier import verify_signed_pdf


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_warsaw_datetime_uses_dst_in_winter_and_summer():
    assert format_business_datetime(datetime(2026, 1, 15, 12, tzinfo=timezone.utc)) == "2026-01-15 13:00"
    assert format_business_datetime(datetime(2026, 7, 15, 12, tzinfo=timezone.utc)) == "2026-07-15 14:00"


def test_submission_mapper_ignores_client_clock_and_creates_aware_utc_timestamp():
    mapped = build_submission_from_form({"client_created_at": "1999-01-01T00:00:00"})
    assert mapped["created_at"].tzinfo is timezone.utc
    assert mapped["created_at"].year != 1999


def test_decision_catalog_assignment_is_snapshotted_in_form_version():
    db = _session()
    service = DecisionDefinitionService()
    form = Form(slug="stable", name="Stable", title="Stable")
    db.add(form)
    db.flush()
    draft = FormVersion(
        form_id=form.id, version_major=1, version_minor=0, version_label="1.0", status="draft",
        definition_json={"workflow": {"initial_step": "review", "steps": [
            {"id": "review", "type": "manual_decision", "requires_officer_action": True},
            {"id": "done", "final": True},
        ]}},
    )
    decision = service.save_definition(db, code="lot_darmowy", label="Lot darmowy", category="positive")
    service.assign_to_draft(draft, decision, step_id="review", target_step="done")
    db.add(draft)
    db.flush()
    submission = FormSubmission(submission_id=str(uuid4()), form_slug=form.slug, form_version_id=draft.id, workflow_step="review", workflow_stage="review")
    db.add(submission)
    db.flush()
    decision.label = "Zmieniona nazwa"
    decision.is_active = False
    resolved = service.resolve(submission, "lot_darmowy")
    assert resolved["label"] == "Lot darmowy"
    assert resolved["target_step"] == "done"


def test_repeatable_items_receive_independent_decisions_by_stable_uuid():
    db = _session()
    service = DecisionDefinitionService()
    first_id, second_id, third_id = (str(uuid4()) for _ in range(3))
    form = Form(slug="travel", name="Travel", title="Travel")
    db.add(form)
    db.flush()
    version = FormVersion(
        form_id=form.id, version_major=1, version_minor=0, version_label="1.0", status="published",
        definition_json={
            "fields": [{"name": "podrozni", "label": "Podróżni", "type": "repeatable_group", "decision_contact_email_field": "email", "fields": [
                {"name": "name", "type": "text"}, {"name": "email", "type": "email"},
            ]}],
            "workflow": {"initial_step": "review", "steps": [{"id": "review"}, {"id": "done"}], "decision_types": [
                {"code": "free", "label": "Lot darmowy", "semantic_category": "positive", "step_id": "review", "target_step": "done", "active": True},
                {"code": "subsidized", "label": "Lot dofinansowany", "semantic_category": "neutral", "step_id": "review", "target_step": "done", "active": True},
            ]},
        },
    )
    db.add(version)
    db.flush()
    submission = FormSubmission(
        submission_id=str(uuid4()), form_slug=form.slug, form_version_id=version.id,
        workflow_step="review", workflow_stage="review",
        data_json={"podrozni": [
            {"id": first_id, "name": "A", "email": "a@example.test"},
            {"id": second_id, "name": "B", "email": "b@example.test"},
            {"id": third_id, "name": "C", "email": ""},
        ]},
    )
    db.add(submission)
    db.flush()
    actor = SimpleNamespace(id=None)
    service.decide_item(db, submission, group_key="podrozni", item_id=first_id, decision_code="free", comment="", actor=actor)
    service.decide_item(db, submission, group_key="podrozni", item_id=second_id, decision_code="subsidized", comment="", actor=actor)
    try:
        service.decide_item(db, submission, group_key="podrozni", item_id=str(uuid4()), decision_code="free", comment="", actor=actor)
    except DecisionDefinitionError:
        pass
    else:
        raise AssertionError("Obcy UUID elementu powinien zostać odrzucony")
    db.flush()
    rows = db.query(RepeatableGroupItemDecision).order_by(RepeatableGroupItemDecision.id).all()
    assert [(row.item_id, row.decision_code) for row in rows] == [(first_id, "free"), (second_id, "subsidized")]
    assert all(row.item_id != third_id for row in rows)
    submission.data_json = {"podrozni": list(reversed(submission.data_json["podrozni"]))}
    assert service.item_history(db, submission.id)[-1].item_id == first_id
    assert service.contact_email(submission, "podrozni", first_id) == "a@example.test"
    try:
        service.contact_email(submission, "podrozni", third_id)
    except DecisionDefinitionError:
        pass
    else:
        raise AssertionError("Brak poprawnego e-maila powinien blokować wysyłkę")


def test_copy_link_action_is_client_only_and_uses_stable_slug():
    template = open("templates/admin/forms/list.html", encoding="utf-8").read()
    assert 'data-copy-public-link="{{ url_for(\'public_forms.form_page\', slug=form.slug) }}"' in template
    assert "navigator.clipboard.writeText" in template
    assert "Link skopiowany" in template


def test_project_logo_is_frozen_in_each_form_version_snapshot():
    form = Form(slug="brand", name="Brand", title="Brand", project_logo_id=11)
    first = FormVersionService().build_snapshot(form, fields=[])
    form.project_logo_id = 22
    second = FormVersionService().build_snapshot(form, fields=[])
    assert first["_form_metadata"]["project_logo_id"] == 11
    assert second["_form_metadata"]["project_logo_id"] == 22


def _signed_pdf(tmp_path: Path) -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pypdf import PdfWriter

    unsigned = tmp_path / "unsigned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_metadata({"/Title": "Office signature regression"})
    with unsigned.open("wb") as stream:
        writer.write(stream)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test Office Signer")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pfx = tmp_path / "signer.p12"
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(b"signer", key, cert, None, serialization.BestAvailableEncryption(b"secret")))
    signer = signers.SimpleSigner.load_pkcs12(str(pfx), passphrase=b"secret")
    signed = tmp_path / "signed.pdf"
    with unsigned.open("rb") as source, signed.open("wb") as output:
        signers.sign_pdf(
            IncrementalPdfFileWriter(source),
            signature_meta=signers.PdfSignatureMetadata(field_name="Signature1"),
            signer=signer,
            output=output,
        )
    return signed


def test_real_cryptographic_pdf_signature_is_not_promoted_to_trusted_offline(tmp_path):
    result = verify_signed_pdf(_signed_pdf(tmp_path))
    assert result["signature_count"] == 1
    assert result["integrity_ok"] is True
    assert result["cryptographically_valid"] is True
    assert result["validation_status"] == "INDETERMINATE"


def test_tampered_signed_pdf_is_invalid(tmp_path):
    signed = _signed_pdf(tmp_path)
    content = bytearray(signed.read_bytes())
    marker = content.find(b"Office signature regression")
    assert marker >= 0
    content[marker] = ord("X")
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(content)
    result = verify_signed_pdf(tampered)
    assert result["validation_status"] == "INVALID"
    assert result["integrity_ok"] is False or result["cryptographically_valid"] is False
