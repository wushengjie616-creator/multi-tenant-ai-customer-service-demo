from loadtests import identities

identity_for = identities.identity_for
endpoint_for = getattr(identities, "endpoint_for", lambda *_args: None)


def test_loadtest_identities_are_deterministic_and_isolated():
    first = identity_for(0)
    assert first == identity_for(0)
    assert first.tenant_id != first.user_id != first.conversation_id
    assert identity_for(1).tenant_id != first.tenant_id
    assert identity_for(1).conversation_id != first.conversation_id


def test_loadtest_endpoints_are_assigned_round_robin():
    endpoints = ("http://api:8000", "http://api-replica:8000", "http://api-replica-2:8000")

    assert [endpoint_for(index, endpoints) for index in range(5)] == [
        "http://api:8000",
        "http://api-replica:8000",
        "http://api-replica-2:8000",
        "http://api:8000",
        "http://api-replica:8000",
    ]
    assert endpoint_for(3, ()) is None
