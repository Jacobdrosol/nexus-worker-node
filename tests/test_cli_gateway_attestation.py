from unittest.mock import MagicMock, patch

from nexus_worker.manager.cli_tools import _claude_gateway_authentication_state


def test_claude_gateway_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway:8081")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "private-token")

    assert _claude_gateway_authentication_state("claude") is None


def test_claude_gateway_attests_only_a_ready_health_endpoint(monkeypatch):
    monkeypatch.setenv("NEXUS_CLAUDE_GATEWAY_ATTESTATION", "true")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway:8081")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "private-token")
    response = MagicMock()
    response.read.return_value = b'{"status":"ok","ready":true}'
    response.__enter__.return_value = response

    with patch("urllib.request.urlopen", return_value=response) as open_url:
        assert _claude_gateway_authentication_state("claude") == "authenticated"

    request = open_url.call_args.args[0]
    assert request.full_url == "http://gateway:8081/health"
    assert request.get_header("X-api-key") == "private-token"


def test_claude_gateway_fails_closed_when_not_ready(monkeypatch):
    monkeypatch.setenv("NEXUS_CLAUDE_GATEWAY_ATTESTATION", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway:8081")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "private-token")
    response = MagicMock()
    response.read.return_value = b'{"status":"not_ready","ready":false}'
    response.__enter__.return_value = response

    with patch("urllib.request.urlopen", return_value=response):
        assert _claude_gateway_authentication_state("claude") == "not_authenticated"
