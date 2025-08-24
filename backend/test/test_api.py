from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200

def test_codes():
    payload = {"noteText": "Telehealth 18 mins, ECG performed."}
    r = client.post("/mbs-codes", json=payload)
    assert r.status_code == 200
    js = r.json()
    assert "suggestions" in js
    assert isinstance(js["suggestions"], list)