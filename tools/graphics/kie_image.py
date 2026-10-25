"""Kie.ai multi-model image generation tool (FLUX, Ideogram, GPT Image, Qwen)."""

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

_DEFAULT_MODEL = "flux1-kontext"


class KieImage(BaseTool):
    name = "kie_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "kie.ai"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_AI_API_KEY"]
    install_instructions = kie_client.INSTALL_INSTRUCTIONS
    agent_skills = ["flux-best-practices"]

    capabilities = ["generate_image", "text_to_image", "image_edit"]
    supports = {
        "aspect_ratio": True,
        "multi_model_gateway": True,
        "custom_size": True,
    }
    best_for = [
        "FLUX Kontext, Ideogram v3, and GPT Image via Kie.ai multi-model gateway",
        "high-aesthetic photorealism and clean typography",
        "unified billing across top image generation providers",
    ]
    not_good_for = ["offline generation without network"]
    fallback_tools = ["flux_image", "google_imagen", "openai_image", "recraft_image"]
    quality_score = 0.90

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Text description of image to generate"},
            "model": {
                "type": "string",
                "default": _DEFAULT_MODEL,
                "enum": list(kie_client.IMAGE_MODELS.keys()),
                "description": "Model identifier on Kie.ai",
            },
            "aspect_ratio": {
                "type": "string",
                "default": "16:9",
                "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                "description": "Aspect ratio for output image",
            },
            "negative_prompt": {"type": "string", "description": "Negative prompt if supported"},
            "image_url": {"type": "string", "description": "Input image URL for image-to-image"},
            "image_path": {"type": "string", "description": "Local input image path"},
            "poll_interval": {"type": "number", "default": 2.5},
            "poll_timeout": {"type": "number", "default": 300.0},
            "output_path": {"type": "string", "description": "Local file path to save generated image"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "model", "aspect_ratio"]
    side_effects = ["writes image file to output_path", "calls Kie.ai API"]
    user_visible_verification = ["Inspect generated image for quality and prompt fidelity"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if kie_client.get_api_key() else ToolStatus.UNAVAILABLE

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["model_catalog"] = kie_client.IMAGE_MODELS
        return info

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", _DEFAULT_MODEL)
        spec = kie_client.IMAGE_MODELS.get(model, {})
        return float(spec.get("cost_per_image", 0.03))

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
        aspect_ratio = inputs.get("aspect_ratio", "16:9")
        output_path = Path(inputs.get("output_path", "kie_image.png"))
        poll_interval = float(inputs.get("poll_interval", 2.5))
        poll_timeout = float(inputs.get("poll_timeout", 300.0))

        input_payload: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
        }
        if inputs.get("negative_prompt"):
            input_payload["negative_prompt"] = inputs["negative_prompt"]
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
                error=f"Kie.ai image generation failed: {exc}",
                duration_seconds=round(time.time() - start, 2),
            )

        return ToolResult(
            success=True,
            data={
                "provider": "kie.ai",
                "model": model,
                "prompt": prompt,
                "output": str(saved_path),
                "task_id": task_id,
                "aspect_ratio": aspect_ratio,
                "source_url": outputs[0],
            },
            artifacts=[str(saved_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
