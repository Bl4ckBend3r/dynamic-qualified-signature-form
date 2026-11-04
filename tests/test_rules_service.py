from services.rules_service import RulesService


def test_rules_block_agreement_with_configured_conditions():
    config = {
        "rules": [
            {
                "id": "block_agreement_if_not_eligible",
                "when": {
                    "any": [
                        {"field": "deklaracja_18_lat", "equals": "Nie"},
                        {"field": "deklaracja_lubuskie", "equals": "Nie"},
                    ]
                },
                "then": [
                    {"action": "set_field", "field": "agreement_blocked", "value": "Tak"},
                    {
                        "action": "set_field",
                        "field": "agreement_block_reason",
                        "value": "Warunki nie zostały spełnione.",
                    },
                    {"action": "set_status", "value": "AGREEMENT_BLOCKED"},
                ],
            }
        ]
    }

    updates = RulesService().apply_rules({}, config, {"deklaracja_lubuskie": "Nie"})

    assert updates["agreement_blocked"] == "Tak"
    assert updates["agreement_block_reason"] == "Warunki nie zostały spełnione."
    assert updates["process_status"] == "AGREEMENT_BLOCKED"


def test_rules_legacy_fallback_blocks_current_declaration_fields():
    updates = RulesService().apply_rules({}, {"rules": []}, {"deklaracja_18_lat": "Nie"})

    assert updates["agreement_blocked"] == "Tak"
    assert updates["process_status"] == "AGREEMENT_BLOCKED"


def test_rules_clear_stale_agreement_block_when_block_condition_no_longer_matches():
    config = {
        "rules": [
            {
                "id": "block_agreement_if_not_eligible",
                "when": {
                    "any": [
                        {"field": "deklaracja_18_lat", "equals": "Nie"},
                        {"field": "deklaracja_lubuskie", "equals": "Nie"},
                    ]
                },
                "then": [
                    {"action": "set_field", "field": "agreement_blocked", "value": "Tak"},
                    {"action": "set_field", "field": "agreement_block_reason", "value": "Warunki nie zostały spełnione."},
                    {"action": "set_status", "value": "AGREEMENT_BLOCKED"},
                ],
            }
        ]
    }
    submission = {
        "agreement_blocked": "Tak",
        "agreement_block_reason": "Warunki nie zostały spełnione.",
        "process_status": "AGREEMENT_BLOCKED",
    }

    updates = RulesService().apply_rules(
        submission,
        config,
        {"deklaracja_18_lat": "Tak", "deklaracja_lubuskie": "Tak"},
    )

    assert updates["agreement_blocked"] == ""
    assert updates["agreement_block_reason"] == ""
    assert "process_status" not in updates
