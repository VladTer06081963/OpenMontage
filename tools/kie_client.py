"""Shared Kie.ai API client for multi-model video and image generation.

Kie.ai (https://kie.ai) is an aggregated AI API platform providing unified access
to leading image and video models (Seedance, Kling, Veo, Wan, Runway, Flux, Ideogram, etc.).

API Contract:
    POST /api/v1/jobs/createTask -> {"code": 200, "data": {"taskId": "..."}}
    GET  /api/v1/jobs/recordInfo?taskId=... -> {"code": 200, "data": {"state": "...", "resultJson": ...}}
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

BASE_URL = "https://api.kie.ai"
CREATE_TASK_ENDPOINT = f"{BASE_URL}/api/v1/jobs/createTask"
RECORD_INFO_ENDPOINT = f"{BASE_URL}/api/v1/jobs/recordInfo"

TERMINAL_SUCCESS = {"success", "completed", "succeeded"}
TERMINAL_FAILURE = {"fail", "failed", "canceled", "cancelled", "error"}
IN_FLIGHT = {"waiting", "queuing", "generating", "processing", "pending"}

ENV_KEYS = ("KIE_AI_API_KEY", "KIE_API_KEY")

INSTALL_INSTRUCTIONS = (
    "Set KIE_AI_API_KEY to your Kie.ai API key.\n"
    "  Get one at https://kie.ai / https://docs.kie.ai"
)

# Supported image models via Kie.ai
IMAGE_MODELS: dict[str, dict[str, Any]] = {
    "flux1-kontext": {
        "family": "flux",
        "description": "FLUX Kontext text-to-image with balanced aesthetics and speed",
        "cost_per_image": 0.03,
        "default_aspect_ratio": "16:9",
    },
    "ideogram/v3-text-to-image": {
        "family": "ideogram",
        "description": "Ideogram v3 with excellent typography and text rendering",
        "cost_per_image": 0.04,
        "default_aspect_ratio": "16:9",
    },
    "gpt-image-2-5-sunburst-text-to-image": {
        "family": "openai",
        "description": "GPT Image 2.5 with high fidelity and prompt adherence",
        "cost_per_image": 0.05,
        "default_aspect_ratio": "16:9",
    },
    "qwen3/pro-text-to-image": {
        "family": "qwen",
        "description": "Qwen 3 Pro high-resolution text-to-image",
        "cost_per_image": 0.03,
        "default_aspect_ratio": "16:9",
    },
    "grok-imagine-image-2-0/text-to-image": {
        "family": "grok",
        "description": "xAI Grok Imagine 2.0 image generation",
        "cost_per_image": 0.04,
        "default_aspect_ratio": "16:9",
    },
}

# Supported video models via Kie.ai
VIDEO_MODELS: dict[str, dict[str, Any]] = {
    "seedance-2-0": {
        "family": "seedance",
        "description": "ByteDance Seedance 2.0 cinematic video with native synchronized audio",
        "cost_base": 0.25,
        "supports_audio": True,
    },
    "runway": {
        "family": "runway",
        "description": "Runway Gen video generation with realistic motion",
        "cost_base": 0.20,
        "supports_audio": False,
    },
    "veo-3-1": {
        "family": "veo",
        "description": "Google Veo high-definition cinematic video clips",
        "cost_base": 0.30,
        "supports_audio": False,
    },
    "kling-3-0": {
        "family": "kling",
        "description": "Kling 3.0 realistic motion video generation",
        "cost_base": 0.20,
        "supports_audio": False,
    },
    "wan-2-6-image-to-video": {
        "family": "wan",
        "description": "Alibaba Wan 2.6 image-to-video and text-to-video",
        "cost_base": 0.18,
        "supports_audio": False,
    },
    "grok-imagine/image-to-video": {
        "family": "grok",
        "description": "xAI Grok Imagine image-to-video generation",
        "cost_base": 0.22,
        "supports_audio": False,
    },
}


class KieError(RuntimeError):
    """Raised for any Kie.ai API, polling, or transport failure."""


def get_api_key() -> str | None:
    """Return the first configured Kie.ai API key from environment."""
    for key in ENV_KEYS:
        val = os.environ.get(key)
        if val:
            return val.strip()
    return None


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _parse_payload(response: Any, context: str = "Kie.ai") -> dict[str, Any]:
    try:
        body = response.json()
    except Exception as exc:
        text = getattr(response, "text", "")[:500]
        raise KieError(f"{context} returned non-JSON response: {text}") from exc

    if not isinstance(body, dict):
        raise KieError(f"{context} returned unexpected body structure: {str(body)[:500]}")

    code = body.get("code")
    if code is not None and int(code) != 200:
        msg = body.get("msg") or body.get("message") or body.get("error") or str(body)
        raise KieError(f"{context} API error (code {code}): {msg}")

    return body


def submit(
    model: str,
    input_payload: dict[str, Any],
    api_key: str,
    call_back_url: str | None = None,
    timeout: int = 60,
) -> str:
    """Submit a task to Kie.ai createTask endpoint and return the taskId."""
    import requests

    payload: dict[str, Any] = {
        "model": model,
        "input": input_payload,
    }
    if call_back_url:
        payload["callBackUrl"] = call_back_url

    try:
        resp = requests.post(
            CREATE_TASK_ENDPOINT,
            headers=_headers(api_key),
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        raise KieError(f"Could not reach Kie.ai at {CREATE_TASK_ENDPOINT}: {exc}") from exc

    if resp.status_code >= 400:
        raise KieError(f"Kie.ai createTask failed with HTTP {resp.status_code}: {resp.text[:500]}")

    body = _parse_payload(resp, context="Kie.ai createTask")
    data = body.get("data")
    if not isinstance(data, dict):
        raise KieError(f"Kie.ai returned no data container: {body}")

    task_id = data.get("taskId") or data.get("id")
    if not task_id:
        raise KieError(f"Kie.ai returned no taskId in response: {body}")

    return str(task_id)


def _extract_output_urls(result_json: Any) -> list[str]:
    """Extract output URLs from recordInfo resultJson field."""
    parsed: Any = result_json
    if isinstance(result_json, str):
        try:
            parsed = json.loads(result_json)
        except Exception:
            if result_json.strip().startswith(("http://", "https://")):
                return [result_json.strip()]
            return []

    if isinstance(parsed, list):
        urls = []
        for item in parsed:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                urls.append(item)
            elif isinstance(item, dict):
                for k in ("url", "resultUrl", "video_url", "image_url"):
                    if item.get(k):
                        urls.append(str(item[k]))
        return urls

    if isinstance(parsed, dict):
        urls = []
        for key in ("resultUrls", "urls", "video_urls", "image_urls", "outputs"):
            arr = parsed.get(key)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, str):
                        urls.append(item)
                    elif isinstance(item, dict) and item.get("url"):
                        urls.append(str(item["url"]))
        for key in ("url", "resultUrl", "video_url", "image_url", "output"):
            val = parsed.get(key)
            if val and isinstance(val, str) and val not in urls:
                urls.append(val)
        return urls

    return []


def poll(
    task_id: str,
    api_key: str,
    interval: float = 3.0,
    timeout: float = 600.0,
    request_timeout: int = 30,
) -> dict[str, Any]:
    """Poll Kie.ai task until terminal state is reached."""
    import requests

    url = f"{RECORD_INFO_ENDPOINT}?taskId={task_id}"
    elapsed = 0.0
    consecutive_errors = 0

    while elapsed < timeout:
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=request_timeout,
            )
        except Exception as exc:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                raise KieError(
                    f"Polling Kie.ai task {task_id} failed after {consecutive_errors} transport errors: {exc}"
                ) from exc
            time.sleep(interval)
            elapsed += interval
            continue

        if resp.status_code >= 400:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                raise KieError(f"Kie.ai poll returned HTTP {resp.status_code}: {resp.text[:500]}")
            time.sleep(interval)
            elapsed += interval
            continue

        consecutive_errors = 0
        body = _parse_payload(resp, context=f"Kie.ai poll for {task_id}")
        data = body.get("data")
        if not isinstance(data, dict):
            time.sleep(interval)
            elapsed += interval
            continue

        state = str(data.get("state", "unknown")).lower()

        if state in TERMINAL_SUCCESS:
            result_json = data.get("resultJson") or data.get("result") or data.get("outputs")
            urls = _extract_output_urls(result_json)
            if not urls:
                # Some models might put direct url in data
                for k in ("url", "videoUrl", "imageUrl", "downloadUrl"):
                    if data.get(k):
                        urls.append(str(data[k]))
            if not urls:
                raise KieError(
                    f"Kie.ai task {task_id} completed with success but returned no output media URLs: {data}"
                )
            return {
                "state": state,
                "outputs": urls,
                "data": data,
            }

        if state in TERMINAL_FAILURE:
            fail_msg = data.get("failMsg") or data.get("failCode") or "Unknown generation failure"
            raise KieError(f"Kie.ai task {task_id} failed ({state}): {fail_msg}")

        time.sleep(interval)
        elapsed += interval

    raise KieError(
        f"Kie.ai task {task_id} did not finish within {timeout:.0f}s. "
        f"Last state: {state if 'state' in locals() else 'unknown'}."
    )


def download(url: str, output_path: str | Path, timeout: int = 120) -> Path:
    """Download output file from URL to destination."""
    import requests

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)

    return dest
