import uuid


def sid():
    return "s-" + uuid.uuid4().hex[:8]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_chat_live_then_cache(client):
    msg = f"unique question {uuid.uuid4()}"
    r1 = client.post("/chat", json={"session_id": sid(), "message": msg})
    assert r1.status_code == 200
    b1 = r1.json()
    m1 = b1["metadata"]
    assert b1["reply"]
    assert m1["resolution_source"] == "LIVE_INFERENCE"
    assert m1["key_id"] == "key_1" and m1["fallback_attempts"] == 0
    assert isinstance(m1["latency_ms"], int)
    assert m1["input_tokens"] > 0 and m1["output_tokens"] > 0

    r2 = client.post("/chat", json={"session_id": sid(), "message": msg})
    b2 = r2.json()
    assert b2["metadata"]["resolution_source"] == "LLM_CACHE"
    assert b2["reply"] == b1["reply"]
    assert b2["metadata"]["key_id"] is None and b2["metadata"]["fallback_attempts"] == 0
    assert b2["metadata"]["input_tokens"] == m1["input_tokens"]

    r3 = client.post("/chat", json={"session_id": sid(), "message": msg + " different"})
    assert r3.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_multi_turn_history_and_isolation(client):
    s1, s2 = sid(), sid()
    client.post("/chat", json={"session_id": s1, "message": "My name is Ada"})
    client.post("/chat", json={"session_id": s1, "message": "What is my name?"})
    client.post("/chat", json={"session_id": s2, "message": "hello other"})
    h = client.get(f"/sessions/{s1}/messages").json()
    assert h["session_id"] == s1
    assert [m["role"] for m in h["messages"]] == ["user", "assistant", "user", "assistant"]
    assert h["messages"][0]["content"] == "My name is Ada"
    assert "metadata" in h["messages"][1] and "metadata" not in h["messages"][0]
    assert h["messages"][1]["metadata"]["resolution_source"] in ("LIVE_INFERENCE", "LLM_CACHE")
    assert len(client.get(f"/sessions/{s2}/messages").json()["messages"]) == 2


def test_unknown_session_404(client):
    r = client.get("/sessions/nope-123/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


def test_validation_422(client):
    assert client.post("/chat", json={"session_id": sid(), "message": "   "}).status_code == 422
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "x" * 129, "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": sid(), "message": "a" * 4001}).status_code == 422
    r = client.post("/chat", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()
    # 4000 chars after trimming is valid
    assert client.post("/chat", json={"session_id": sid(), "message": "  " + "b" * 4000 + "  "}).status_code == 200


def test_failover_429_and_500(client):
    for status in ("429", "500", "502", "503"):
        s = sid()
        r = client.post("/chat", json={"session_id": s, "message": f"failover {status} {uuid.uuid4()}"},
                        headers={"X-Simulate-Failure": f"key_1={status}"})
        assert r.status_code == 200, r.text
        m = r.json()["metadata"]
        assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1
        assert m["resolution_source"] == "LIVE_INFERENCE"


def test_all_keys_fail_503_not_stored(client):
    s = sid()
    r = client.post("/chat", json={"session_id": s, "message": "hi"}, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{s}/messages").status_code == 404
    r = client.post("/chat", json={"session_id": s, "message": "hi"},
                    headers={"X-Simulate-Failure": "key_1=429,key_2=500"})
    assert r.status_code == 503


def test_simulation_disabled(client, monkeypatch):
    client.app.state.settings.enable_failure_simulation = False
    r = client.post("/chat", json={"session_id": sid(), "message": f"x {uuid.uuid4()}"},
                    headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200


def test_custom_system_prompt_changes_cache_key(client):
    msg = f"same text {uuid.uuid4()}"
    a = client.post("/chat", json={"session_id": sid(), "message": msg}).json()
    b = client.post("/chat", json={"session_id": sid(), "message": msg, "system_prompt": "Be a pirate"}).json()
    assert a["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert b["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_parse_header():
    from app.llm import parse_simulation_header
    ids = ["key_1", "key_2"]
    assert parse_simulation_header("key_1=429", ids) == {"key_1": 429}
    assert parse_simulation_header("key_1=429, key_2=500", ids) == {"key_1": 429, "key_2": 500}
    assert parse_simulation_header("*=503", ids) == {"key_1": 503, "key_2": 503}
    assert parse_simulation_header("key_1=418,garbage", ids) == {}
