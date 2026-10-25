"""Kie.ai multi-model video generation tool (Seedance, Kling, Runway, Veo, Wan)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools import kie_client
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

_DEFAULT_MODEL = "seedance-2-0"


class KieVideo(BaseTool):
    name = "kie_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "kie.ai"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_AI_API_KEY"]
    install_instructions = kie_client.INSTALL_INSTRUCTIONS
    agent_skills = ["ai-video-gen", "seedance-2-0"]

    capabilities = ["text_to_video", "image_to_video", "video_generation"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "native_audio": True,
        "aspect_ratio": True,
        "multi_model_gateway": True,
    }
    best_for = [
        "ByteDance Seedance 2.0, Runway, Google Veo, Kling 3.0, and Wan 2.6 via Kie.ai",
        "cinematic video generation with unified multi-model access",
        "pay-as-you-go credits without maintaining multiple individual provider accounts",
    ]
    not_good_for = ["offline video rendering without internet access"]
    fallback_tools = ["seedance_video", "kling_video", "veo_video", "minimax_video"]
    quality_score = 0.92

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Text description of video to generate"},
            "model": {
                "type": "string",
                "default": _DEFAULT_MODEL,
                "enum": list(kie_client.VIDEO_MODELS.keys()),
                "description": "Video model identifier on Kie.ai",
            },
            "duration": {
                "type": "integer",
                "default": 5,
                "enum": [5, 10],
                "description": "Video duration in seconds",
            },
            "aspect_ratio": {
                "type": "string",
                "default": "16:9",
                "enum": ["16:9", "9:16", "1:1"],
                "description": "Aspect ratio for output video",
            },
            "quality": {
                "type": "string",
                "default": "720p",
                "enum": ["720p", "1080p"],
                "description": "Output resolution quality",
            },
            "image_url": {"type": "string", "description": "Reference or starting image URL for image-to-video"},
            "image_path": {"type": "string", "description": "Local starting image path"},
            "poll_interval": {"type": "number", "default": 5.0},
            "poll_timeout": {"type": "number", "default": 600.0},
            "output_path": {"type": "string", "description": "Local file path to save rendered video"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "model", "duration", "aspect_ratio"]
    side_effects = ["writes video file to output_path", "calls Kie.ai API"]
    user_visible_verification = ["Inspect generated video clip for motion stability and quality"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if kie_client.get_api_key() else ToolStatus.UNAVAILABLE

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["model_catalog"] = kie_client.VIDEO_MODELS
        return info

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", _DEFAULT_MODEL)
        spec = kie_client.VIDEO_MODELS.get(model, {})
        base_cost = float(spec.get("cost_base", 0.25))
        duration = int(inputs.get("duration", 5))
        return round(base_cost * (duration / 5.0), 3)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = kie_client.get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error=f"No Kie.ai API key found. {self.install_instructions}",
            )

        start = time.time()
        prompt = inputs["prompt"]
        model = inputs.get("model", _DEFAULT_MODEL)
        duration = int(inputs.get("duration", 5))
        aspect_ratio = inputs.get("aspect_ratio", "16:9")
        quality = inputs.get("quality", "720p")
        output_path = Path(inputs.get("output_path", "kie_video.mp4"))
        poll_interval = float(inputs.get("poll_interval", 5.0))
        poll_timeout = float(inputs.get("poll_timeout", 600.0))

        input_payload: dict[str, Any] = {
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "quality": quality,
        }
        if inputs.get("image_url"):
            input_payload["image_url"] = inputs["image_url"]

        try:
            task_id = kie_client.submit(model, input_payload, api_key=api_key)
            result = kie_client.poll(
                task_id,
                api_key=api_key,
                interval=poll_interval,
                timeout=poll_timeout,
            )
            outputs = result.get("outputs") or []
            if not outputs:
                return ToolResult(
                    success=False,
                    error=f"Kie.ai generation finished with no output URLs for task {task_id}",
                )

            saved_path = kie_client.download(outputs[0], output_path)

        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Kie.ai video generation failed: {exc}",
                duration_seconds=round(time.time() - start, 2),
            )

        return ToolResult(
            success=True,
            data={
                "provider": "kie.ai",
                "model": model,
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
                "output": str(saved_path),
                "task_id": task_id,
                "source_url": outputs[0],
            },
            artifacts=[str(saved_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
