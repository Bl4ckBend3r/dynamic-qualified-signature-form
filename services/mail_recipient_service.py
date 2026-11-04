"""Resolve configured record recipients without granting participant access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from form_loader import evaluate_scoped_condition


PARTICIPANT_FIELDS = ("record_uuid", "first_name", "last_name", "full_name", "email", "phone")


@dataclass(frozen=True)
class RecordMailRecipient:
    group_key: str
    record_uuid: str
    email: str
    participant: dict[str, str]
    can_access_submission: bool = False


class MailRecipientResolver:
    @staticmethod
    def configured_sources(definition, event_type, template, *, step_id=""):
        """None keeps legacy single-recipient delivery; [] is configured but disabled."""
        sources, configured = [], False
        for rule in (definition.get("workflow") or {}).get("email_notifications", []):
            if "recipient_source" not in rule:
                continue
            event = str(rule.get("event") or rule.get("trigger") or "")
            template_type = str(rule.get("template_type") or "")
            if event and event != event_type:
                continue
            if template_type and template_type != getattr(template, "template_type", ""):
                continue
            if not event and not template_type and rule.get("id") != event_type:
                continue
            if rule.get("step_id") and rule["step_id"] != step_id:
                continue
            configured = True
            if rule.get("enabled", True):
                sources.append(rule["recipient_source"])
        return sources if configured else None

    @staticmethod
    def validate_source(source: Any, definition: dict) -> list[str]:
        if not isinstance(source, dict) or source.get("type") != "repeatable_group":
            return ["Źródło odbiorców musi mieć typ repeatable_group."]
        group = next((f for f in definition.get("fields", []) if f.get("type") == "repeatable_group" and f.get("name") == source.get("group")), None)
        if not group:
            return ["Źródło odbiorców wskazuje nieistniejącą grupę powtarzalną."]
        email_field = source.get("email_field") or group.get("decision_contact_email_field")
        if not email_field:
            emails = [f.get("name") for f in group.get("fields", []) if f.get("type") == "email"]
            email_field = emails[0] if len(emails) == 1 else None
        if not any(f.get("name") == email_field and f.get("type") == "email" for f in group.get("fields", [])):
            return ["Wybierz pole e-mail należące do wskazanej grupy odbiorców."]
        return []

    def resolve(self, submission, definition, sources, *, people_service, record_uuid="", group_key="", participant_snapshot=None):
        groups = {group["key"]: group for group in people_service.repeatable_groups(submission, definition=definition)}
        data = dict(submission.data_json or {})
        recipients, seen = [], set()
        for source in sources:
            errors = self.validate_source(source, definition)
            if errors:
                raise ValueError(" ".join(errors))
            group = groups.get(source["group"])
            if not group or (record_uuid and group_key != group["key"]):
                continue
            config = group["config"]
            if config.get("enabled", True) is False or config.get("active", True) is False or not evaluate_scoped_condition(config.get("visible_if"), root_data=data, current_data=data):
                continue
            email_key = source.get("email_field") or group["contact_field"]
            email_schema = next(f for f in config["fields"] if f.get("name") == email_key)
            for item in group["items"]:
                if record_uuid and item["id"] != record_uuid:
                    continue
                if email_schema.get("enabled", True) is False or not evaluate_scoped_condition(email_schema.get("visible_if"), root_data=data, current_data=item["values"]):
                    continue
                live_email = str(item["values"].get(email_key) or "").strip()
                person = people_service.participant_context(group, item)
                authorized_email = str(person.get('email') or '').strip()
                email = live_email
                if participant_snapshot is not None:
                    if str(participant_snapshot.get("record_uuid") or "") != item["id"]:
                        raise ValueError("Snapshot odbiorcy nie odpowiada osobie wskazanej przez zdarzenie.")
                    person = participant_snapshot
                    snapshot_values = person.get('values') or {}
                    if email_key in snapshot_values:
                        email = str(snapshot_values[email_key] or '').strip()
                    elif email_key in {group['contact_field'], (config.get('item_context_fields') or {}).get('email')}:
                        email = str(person.get('email') or '').strip()
                    else:
                        raise ValueError('Snapshot decyzji nie zawiera skonfigurowanego adresu odbiorcy.')
                participant = {key: str(person.get(key) or "") for key in PARTICIPANT_FIELDS}
                if participant_snapshot is not None:
                    participant.update({key: str(person.get(key) or "") for key in ('decision', 'decision_label', 'decision_reason')})
                participant["record_uuid"] = item["id"]
                participant["email"] = email
                identity = (group["key"], item["id"], email)
                if identity in seen:
                    continue
                seen.add(identity)
                applicant_id = (data.get("_applicant_record_ids") or {}).get(group["key"])
                can_access = bool(config.get("applicant_record") and applicant_id == item["id"] and email == authorized_email)
                recipients.append(RecordMailRecipient(group["key"], item["id"], email, participant, can_access))
        return recipients
