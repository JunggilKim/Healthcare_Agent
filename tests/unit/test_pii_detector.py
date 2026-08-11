from backend.app.security.pii_detector import detect_identifier_ranges


def test_identifier_detector_returns_categories_and_ranges_not_values() -> None:
    text = "name: synthetic, email demo@example.org, SSN 123-45-6789, 010-1234-5678"
    matches = detect_identifier_ranges(text)
    categories = {item.category for item in matches}
    assert {"EMAIL", "PHONE", "US_SSN", "EXPLICIT_IDENTIFIER_LABEL"} <= categories
    assert all(text[item.start : item.end] for item in matches)


def test_identifier_detector_catches_korean_resident_number_and_label() -> None:
    matches = detect_identifier_ranges("주민등록번호: 900101-1234567")
    assert {
        "EXPLICIT_IDENTIFIER_LABEL",
        "KOREAN_RRN",
    } <= {item.category for item in matches}
