from smart_router.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, extract_safe_features


def test_feature_schema_is_stable_and_privacy_safe():
    body = {
        "messages": [
            {"role": "system", "content": "private system prompt"},
            {"role": "user", "content": "Debug this? ```python\nprint(1)\n``` https://example.com/a"},
        ],
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
        "max_tokens": 1200,
    }
    features = extract_safe_features(body)
    values = features.as_dict()
    assert FEATURE_SCHEMA_VERSION == 1
    assert tuple(values) == FEATURE_NAMES
    assert values["has_tools"] == 1
    assert values["has_code"] == 1
    assert values["url_count"] == 1
    rendered = str(values)
    assert "private system prompt" not in rendered
    assert "read_file" not in rendered
