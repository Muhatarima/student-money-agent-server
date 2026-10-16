import httpx

import mock_server


async def test_mock_whatsapp_keeps_messages_with_masked_phone():
    mock_server.sent.clear()
    transport = httpx.ASGITransport(app=mock_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wa") as client:
        body = {"messaging_product": "whatsapp", "to": "8801700001234", "type": "text"}
        response = await client.post("/fake-whatsapp/send", json=body)
        assert response.json() == {"messages": [{"id": "wamid.fake1"}]}
        [message] = (await client.get("/fake-whatsapp/sent")).json()
    assert message["to"] == "****1234"
