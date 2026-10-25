"""DeepSeek LLM tool for script writing, prompt refinement, translation, and reasoning.

Supports DeepSeek-V3 (`deepseek-chat`) and DeepSeek-R1 (`deepseek-reasoner`).
Uses OpenAI-compatible endpoint at https://api.deepseek.com/chat/completions.
"""

from __future__ import annotations

import os
import time
from typing import Any

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

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekLLM(BaseTool):
    name = "deepseek_llm"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "deepseek"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:DEEPSEEK_API_KEY"]
    install_instructions = (
        "Set DEEPSEEK_API_KEY to your DeepSeek API key.\n"
        "  Get one at https://platform.deepseek.com/api_keys"
    )
    agent_skills = []

    capabilities = [
        "generate_script",
        "refine_prompt",
        "translate",
        "reason",
        "chat",
    ]
    supports = {
        "reasoning": True,
        "system_prompt": True,
        "temperature": True,
    }
    best_for = [
        "generating video scripts, voiceover narration, and scene outlines",
        "deep reasoning on complex technical concepts (DeepSeek-R1)",
        "optimizing prompts for video/image generation models",
        "high quality multilingual translation of scripts and captions",
    ]
    not_good_for = ["direct video/image rendering (use media tools for that)"]
    quality_score = 0.95

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "User prompt or instructions (e.g. write script, refine prompt, translate)",
            },
            "operation": {
                "type": "string",
                "default": "chat",
                "enum": ["chat", "generate_script", "refine_prompt", "translate", "reason"],
                "description": "Operation type to guide default behavior",
            },
            "system_prompt": {
                "type": "string",
                "description": "Optional system prompt setting the AI persona or instructions",
            },
            "model": {
                "type": "string",
                "default": DEFAULT_MODEL,
                "enum": ["deepseek-chat", "deepseek-reasoner"],
                "description": "deepseek-chat (V3) for fast chat/scripts, deepseek-reasoner (R1) for chain-of-thought",
            },
            "temperature": {
                "type": "number",
                "default": 0.7,
                "description": "Sampling temperature (ignored for deepseek-reasoner)",
            },
            "max_tokens": {
                "type": "integer",
                "default": 4096,
                "description": "Maximum tokens in generated response",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "model", "operation", "temperature"]
    side_effects = ["calls DeepSeek chat completions API"]
    user_visible_verification = ["Inspect generated text output and reasoning"]

    def _get_api_key(self) -> str | None:
        key = os.environ.get("DEEPSEEK_API_KEY")
        return key.strip() if key else None

    def _get_base_url(self) -> str:
        url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        return url

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", DEFAULT_MODEL)
        if model == "deepseek-reasoner":
            return 0.005
        return 0.001

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error=f"No DeepSeek API key found. {self.install_instructions}",
            )

        import requests

        start = time.time()
        prompt = inputs["prompt"]
        model = inputs.get("model", DEFAULT_MODEL)
        operation = inputs.get("operation", "chat")
        system_prompt = inputs.get("system_prompt")
        temperature = float(inputs.get("temperature", 0.7))
        max_tokens = int(inputs.get("max_tokens", 4096))

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        elif operation == "generate_script":
            messages.append({
                "role": "system",
                "content": (
                    "You are a professional video director and scriptwriter for OpenMontage. "
                    "Write engaging, punchy, well-paced scripts with scene breakdowns and narration."
                ),
            })
        elif operation == "refine_prompt":
            messages.append({
                "role": "system",
                "content": (
                    "You are an expert AI media prompter. Expand and refine user descriptions into "
                    "detailed, cinematic prompts optimized for video and image generation models."
                ),
            })

        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if model != "deepseek-reasoner":
            payload["temperature"] = temperature

        base_url = self._get_base_url()
        endpoint = f"{base_url}/chat/completions"

        try:
            resp = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if resp.status_code >= 400:
                return ToolResult(
                    success=False,
                    error=f"DeepSeek API error HTTP {resp.status_code}: {resp.text[:500]}",
                    duration_seconds=round(time.time() - start, 2),
                )

            data = resp.json()
            choice = data["choices"][0]["message"]
            content = choice.get("content", "")
            reasoning_content = choice.get("reasoning_content")
            usage = data.get("usage", {})

        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"DeepSeek execution failed: {exc}",
                duration_seconds=round(time.time() - start, 2),
            )

        return ToolResult(
            success=True,
            data={
                "provider": "deepseek",
                "model": model,
                "content": content,
                "reasoning_content": reasoning_content,
                "usage": usage,
            },
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
