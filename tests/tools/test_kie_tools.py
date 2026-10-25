"""Tests for Kie.ai media tools (KieImage, KieVideo, and kie_client)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tools import kie_client
from tools.base_tool import BaseTool, ExecutionMode, ToolRuntime, ToolStability, ToolStatus, ToolTier
from tools.graphics.kie_image import KieImage
from tools.video.kie_video import KieVideo

TOOLS = [KieImage, KieVideo]


class FakeResponse:
    def __init__(self, payload=None, content=b"fake-bytes", status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.content = content
        self.status_code = status_code
        self.text = text or str(self._payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def iter_content(self, chunk_size=65536):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _clear_kie_env(monkeypatch):
    for key in kie_client.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("cls", TOOLS, ids=lambda cls: cls.name)
class TestContract:
    def test_contract_identity(self, cls):
        tool = cls()
        assert issubclass(cls, BaseTool)
        assert tool.provider == "kie.ai"
        assert tool.tier == ToolTier.GENERATE
        assert tool.stability == ToolStability.BETA
        assert tool.runtime == ToolRuntime.API
        assert tool.execution_mode == ExecutionMode.SYNC
        assert "prompt" in tool.input_schema["required"]
        assert "env:KIE_AI_API_KEY" in tool.dependencies

    def test_status_and_key_aliases(self, cls, monkeypatch):
        assert cls().get_status() == ToolStatus.UNAVAILABLE
        for key in kie_client.ENV_KEYS:
            monkeypatch.setenv(key, "test-key-123")
            assert cls().get_status() == ToolStatus.AVAILABLE
            monkeypatch.delenv(key)

    def test_no_key_fails_before_network(self, cls):
        result = cls().execute({"prompt": "A cinematic shot of a neon cyberpunk city"})
        assert not result.success
        assert "KIE_AI_API_KEY" in result.error


def test_kie_image_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("KIE_AI_API_KEY", "test-key")
    monkeypatch.setattr(kie_client.time, "sleep", lambda _s: None)

    task_id = "task_img_123"
    img_url = "https://cdn.kie.ai/outputs/test_img.png"

    post_resp = FakeResponse({"code": 200, "data": {"taskId": task_id}})
    poll_resp = FakeResponse({
        "code": 200,
        "data": {
            "taskId": task_id,
            "state": "success",
            "resultJson": f'{{"resultUrls": ["{img_url}"]}}',
        },
    })
    dl_resp = FakeResponse(content=b"PNG_DATA", status_code=200)

    responses_post = [post_resp]
    responses_get = [poll_resp, dl_resp]

    def fake_post(url, **kwargs):
        assert url == kie_client.CREATE_TASK_ENDPOINT
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        return responses_post.pop(0)

    def fake_get(url, **kwargs):
        return responses_get.pop(0)

    fake_module = types.ModuleType("requests")
    fake_module.post = fake_post
    fake_module.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_module)

    out_file = tmp_path / "test.png"
    tool = KieImage()
    res = tool.execute({
        "prompt": "sunset over mountain",
        "output_path": str(out_file),
    })

    assert res.success
    assert res.data["provider"] == "kie.ai"
    assert res.data["task_id"] == task_id
    assert out_file.exists()
    assert out_file.read_bytes() == b"PNG_DATA"


def test_kie_video_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("KIE_AI_API_KEY", "test-key")
    monkeypatch.setattr(kie_client.time, "sleep", lambda _s: None)

    task_id = "task_vid_456"
    vid_url = "https://cdn.kie.ai/outputs/test_vid.mp4"

    post_resp = FakeResponse({"code": 200, "data": {"taskId": task_id}})
    poll_resp = FakeResponse({
        "code": 200,
        "data": {
            "taskId": task_id,
            "state": "success",
            "resultJson": f'{{"resultUrls": ["{vid_url}"]}}',
        },
    })
    dl_resp = FakeResponse(content=b"MP4_DATA", status_code=200)

    responses_post = [post_resp]
    responses_get = [poll_resp, dl_resp]

    def fake_post(url, **kwargs):
        assert url == kie_client.CREATE_TASK_ENDPOINT
        return responses_post.pop(0)

    def fake_get(url, **kwargs):
        return responses_get.pop(0)

    fake_module = types.ModuleType("requests")
    fake_module.post = fake_post
    fake_module.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_module)

    out_file = tmp_path / "test.mp4"
    tool = KieVideo()
    res = tool.execute({
        "prompt": "drone flight over waterfall",
        "duration": 5,
        "output_path": str(out_file),
    })

    assert res.success
    assert res.data["provider"] == "kie.ai"
    assert res.data["task_id"] == task_id
    assert out_file.exists()
    assert out_file.read_bytes() == b"MP4_DATA"


def test_kie_poll_failure(monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY", "test-key")
    monkeypatch.setattr(kie_client.time, "sleep", lambda _s: None)

    task_id = "task_fail_789"
    post_resp = FakeResponse({"code": 200, "data": {"taskId": task_id}})
    poll_resp = FakeResponse({
        "code": 200,
        "data": {
            "taskId": task_id,
            "state": "fail",
            "failMsg": "Safety filter triggered",
        },
    })

    responses_post = [post_resp]
    responses_get = [poll_resp]

    fake_module = types.ModuleType("requests")
    fake_module.post = lambda url, **kwargs: responses_post.pop(0)
    fake_module.get = lambda url, **kwargs: responses_get.pop(0)
    monkeypatch.setitem(sys.modules, "requests", fake_module)

    tool = KieVideo()
    res = tool.execute({"prompt": "hazardous content"})
    assert not res.success
    assert "Safety filter triggered" in res.error
