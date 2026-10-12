import httpx

from app.main import app


async def test_liveness_and_request_id():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["x-request-id"]


async def test_validation_has_no_raw_stack_or_input():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/chat/stream", json={"message": "", "provider": "invalid"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert "traceback" not in response.text.lower()
