from capabledeputy.policy.labels import Label


def test_label_taxonomy_matches_design() -> None:
    expected = {
        "confidential.health",
        "confidential.financial",
        "confidential.personal",
        "untrusted.external",
        "untrusted.user_input",
        "trusted.user_direct",
        "egress.email",
        "egress.purchase",
    }
    actual = {label.value for label in Label}
    assert actual == expected


def test_label_namespaces_are_dotted() -> None:
    valid_namespaces = {"confidential", "untrusted", "trusted", "egress"}
    for label in Label:
        head, sep, _ = label.value.partition(".")
        assert sep == "."
        assert head in valid_namespaces


def test_label_string_value_is_full_dotted_form() -> None:
    assert str(Label.CONFIDENTIAL_HEALTH) == "confidential.health"
    assert Label("confidential.health") is Label.CONFIDENTIAL_HEALTH
