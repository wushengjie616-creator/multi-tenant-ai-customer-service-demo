from loadtests.identities import identity_for


def test_loadtest_identities_are_deterministic_and_isolated():
    first = identity_for(0)
    assert first == identity_for(0)
    assert first.tenant_id != first.user_id != first.conversation_id
    assert identity_for(1).tenant_id != first.tenant_id
    assert identity_for(1).conversation_id != first.conversation_id
