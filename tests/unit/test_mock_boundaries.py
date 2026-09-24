from mocks.mock_im import main as mock_im
from mocks.mock_platform import main as mock_platform


class _RecordingClient:
    def __init__(self):
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.request = (url, kwargs)


async def test_mock_im_forwards_the_customer_bearer_token():
    client = _RecordingClient()

    await mock_im.forward_inbound(
        {"message_id": "m-1"},
        "customer-token",
        client_factory=lambda **_: client,
    )

    assert client.request == (
        mock_im.API_WEBHOOK_URL,
        {
            "json": {"message_id": "m-1"},
            "headers": {"Authorization": "Bearer customer-token"},
        },
    )


async def test_mock_platform_auto_renew_state_is_scoped_by_tenant_and_user():
    mock_platform._auto_renew_enabled.clear()
    mock_platform._results.clear()
    await mock_platform.execute_tool(
        "close_auto_renew",
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "resource_id": "membership-2026",
        },
        "tenant-a-close-1",
    )

    tenant_a = await mock_platform.query("subscription_status", "tenant-a", "user-a")
    tenant_b = await mock_platform.query("subscription_status", "tenant-b", "user-b")

    assert tenant_a["data"]["auto_renew"] is False
    assert tenant_b["data"]["auto_renew"] is True
