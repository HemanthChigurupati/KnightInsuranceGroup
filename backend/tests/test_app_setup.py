from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import app


def _client_with(settings: Settings) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_health_reports_model_but_never_the_key():
    secret = "sk-test-should-not-leak"
    client = _client_with(
        Settings(_env_file=None, llm_model="claude-test", anthropic_api_key=SecretStr(secret))
    )
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["llm"] == {"provider": "anthropic", "model": "claude-test", "configured": True}
    assert secret not in resp.text


def test_health_shows_unconfigured_without_key():
    client = _client_with(Settings(_env_file=None, llm_model="claude-test"))
    assert client.get("/api/health").json()["llm"]["configured"] is False


def test_openai_compatible_needs_only_base_url():
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_model="llama3.1",
        llm_base_url="http://localhost:11434/v1",
    )
    assert settings.llm_configured is True


def test_index_page_is_served():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert "<title>Submission Review</title>" in resp.text
    assert TestClient(app).get("/app.js").status_code == 200


def test_sample_packets_are_present(samples_dir):
    for packet in ("SUB-A", "SUB-B", "SUB-C"):
        assert (samples_dir / packet / "broker_email.txt").is_file()
