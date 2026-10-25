"""Tests for DeepSeekLLM tool."""

from __future__ import annotations

import sys
import types

import pytest

from tools.analysis.deepseek_llm import DeepSeekLLM
from tools.base_tool import BaseTool, ExecutionMode, ToolRuntime, ToolStability, ToolStatus, ToolTier


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text or str(self._payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def _clear_deepseek_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)


def test_deepseek_contract():
    tool = DeepSeekLLM()
    assert issubclass(DeepSeekLLM, BaseTool)
    assert tool.name == "deepseek_llm"
    assert tool.provider == "deepseek"
    assert tool.capability == "analysis"
    assert tool.tier == ToolTier.CORE
    assert tool.runtime == ToolRuntime.API
    assert tool.execution_mode == ExecutionMode.SYNC
    assert "prompt" in tool.input_schema["required"]
    assert "env:DEEPSEEK_API_KEY" in tool.dependencies


def test_deepseek_status(monkeypatch):
    tool = DeepSeekLLM()
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    assert tool.get_status() == ToolStatus.AVAILABLE


def test_deepseek_no_key_fails():
    tool = DeepSeekLLM()
    res = tool.execute({"prompt": "write a video script"})
    assert not res.success
    assert "DEEPSEEK_API_KEY" in res.error


def test_deepseek_execution_success(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-12345")

    expected_content = "Scene 1: Introduction to Quantum Physics..."
    expected_payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": expected_content,
                    "reasoning_content": "Thought process: explain superposition clearly.",
                }
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 120,
            "total_tokens": 170,
        },
    }

    recorded_calls = []

    def fake_post(url, **kwargs):
        recorded_calls.append({"url": url, **kwargs})
        return FakeResponse(expected_payload)

    fake_module = types.ModuleType("requests")
    fake_module.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake_module)

    tool = DeepSeekLLM()
    res = tool.execute({
        "prompt": "Explain quantum computing in 3 scenes",
        "operation": "generate_script",
        "model": "deepseek-reasoner",
    })

    assert res.success
    assert res.data["provider"] == "deepseek"
    assert res.data["model"] == "deepseek-reasoner"
    assert res.data["content"] == expected_content
    assert res.data["reasoning_content"] == "Thought process: explain superposition clearly."
    assert res.data["usage"]["total_tokens"] == 170

    assert len(recorded_calls) == 1
    call = recorded_calls[0]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-deepseek-12345"
    assert call["json"]["model"] == "deepseek-reasoner"
    # Temperature should be omitted for deepseek-reasoner
    assert "temperature" not in call["json"]


def test_deepseek_http_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-invalid")

    def fake_post(url, **kwargs):
        return FakeResponse(status_code=401, text="Unauthorized: invalid api key")

    fake_module = types.ModuleType("requests")
    fake_module.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake_module)

    tool = DeepSeekLLM()
    res = tool.execute({"prompt": "Hello"})
    assert not res.success
    assert "HTTP 401" in res.error
