from smart_router.database import RouteStore


def test_sticky_promotion_and_conservative_demotion(settings):
    store = RouteStore(settings)
    key = "abc"
    assert store.resolve(key, "auto", "fast", "start", now=100).tier == "fast"
    promoted = store.resolve(key, "auto", "strong", "complex", now=101)
    assert promoted.tier == "strong" and promoted.action == "promoted"
    for i in range(settings.demotion_turns - 1):
        result = store.resolve(key, "auto", "fast", "simple", now=102 + i)
        assert result.tier == "strong"
    result = store.resolve(key, "auto", "fast", "simple", now=200)
    assert result.tier == "fast" and result.action == "demoted"
