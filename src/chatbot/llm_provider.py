"""LLM provider for the MCPDischarge chatbot (Azure OpenAI via OpenAI Python SDK).

This module intentionally contains only the minimal wiring needed to:
- call the LLM
- optionally use tool calling (function calling)

Security (RBAC/PHI) is enforced in higher layers; never delegate it to the LLM.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional


_DEPLOYMENT_URL_RE = re.compile(r"^(https://[^/]+)(/openai/deployments/([^/]+).*)?$", re.IGNORECASE)


def _normalize_endpoint(endpoint: str) -> tuple[str, Optional[str]]:
    """Return (azure_endpoint_base, deployment_name_from_url_if_present)."""
    endpoint = endpoint.strip().strip('"').strip("'")
    match = _DEPLOYMENT_URL_RE.match(endpoint)
    if not match:
        return endpoint.rstrip("/") + "/", None
    base = match.group(1).rstrip("/") + "/"
    deployment = match.group(3)
    return base, deployment


def is_configured() -> bool:
    return bool(
        os.getenv("AZURE_OPENAI_ENDPOINT")
        and os.getenv("AZURE_OPENAI_API_KEY")
        and os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    )


async def chat_with_tools(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call Azure OpenAI Chat Completions with tools enabled.

    Returns the raw `choice.message` object converted to a plain dict.
    """
    endpoint_raw = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    deployment_env = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")
    timeout_s = float(os.getenv("AZURE_OPENAI_TIMEOUT_S", "30"))

    if not endpoint_raw or not api_key:
        raise RuntimeError("Azure OpenAI is not configured (missing AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY).")

    azure_endpoint, deployment_from_url = _normalize_endpoint(endpoint_raw)
    deployment = deployment_env or (deployment_from_url or "")
    if not deployment:
        raise RuntimeError("Missing AZURE_OPENAI_DEPLOYMENT_NAME (or provide a deployments URL in AZURE_OPENAI_ENDPOINT).")

    try:
        from openai import AzureOpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency: openai. Install with: pip install -r requirements.txt") from exc

    def _do_call() -> dict[str, Any]:
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_s,
            max_retries=1,
        )
        # Some environments support parallel tool calls; fall back gracefully if unsupported.
        try:
            resp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                parallel_tool_calls=True,
            )
        except TypeError:
            resp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
            )
        choices = getattr(resp, "choices", None)
        if not choices:
            raise RuntimeError(
                "Azure OpenAI returned no choices. Check AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_DEPLOYMENT_NAME, and AZURE_OPENAI_API_VERSION."
            )
        msg = choices[0].message
        if hasattr(msg, "model_dump"):
            return msg.model_dump()
        try:
            return json.loads(msg.json())  # type: ignore[attr-defined]
        except Exception:
            # best effort
            return {"content": getattr(msg, "content", None), "tool_calls": getattr(msg, "tool_calls", None)}

    return await asyncio.to_thread(_do_call)


async def chat_text(
    *,
    messages: list[dict[str, Any]],
    temperature: float = 0.0,
    deployment_env_var: str = "AZURE_OPENAI_PREPROCESS_DEPLOYMENT_NAME",
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    api_version: Optional[str] = None,
    deployment: Optional[str] = None,
) -> dict[str, Any]:
    """Call Azure OpenAI Chat Completions without tools.

    This is used for safe preprocessing/rewriting. If `deployment_env_var` is set,
    that deployment name is used; otherwise the default AZURE_OPENAI_DEPLOYMENT_NAME.
    """
    endpoint_raw = (endpoint or os.getenv("AZURE_OPENAI_ENDPOINT", "")).strip()
    api_key = (api_key or os.getenv("AZURE_OPENAI_API_KEY", "")).strip()
    api_version = (api_version or os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")).strip()
    deployment_env = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "").strip()
    preprocess_deployment = os.getenv(deployment_env_var, "").strip()
    timeout_s = float(os.getenv("AZURE_OPENAI_TIMEOUT_S", "30"))

    if not endpoint_raw or not api_key:
        raise RuntimeError("Azure OpenAI is not configured (missing AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY).")

    azure_endpoint, deployment_from_url = _normalize_endpoint(endpoint_raw)
    chosen_deployment = (deployment or "").strip() or preprocess_deployment or deployment_env or (deployment_from_url or "")
    if not chosen_deployment:
        raise RuntimeError("Missing AZURE_OPENAI_DEPLOYMENT_NAME (or provide a deployments URL in AZURE_OPENAI_ENDPOINT).")

    try:
        from openai import AzureOpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency: openai. Install with: pip install -r requirements.txt") from exc

    def _do_call() -> dict[str, Any]:
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_s,
            max_retries=1,
        )
        resp = client.chat.completions.create(
            model=chosen_deployment,
            messages=messages,
            temperature=temperature,
        )
        choices = getattr(resp, "choices", None)
        if not choices:
            raise RuntimeError("Azure OpenAI returned no choices.")
        msg = choices[0].message
        if hasattr(msg, "model_dump"):
            return msg.model_dump()
        try:
            return json.loads(msg.json())  # type: ignore[attr-defined]
        except Exception:
            return {"content": getattr(msg, "content", None)}

    return await asyncio.to_thread(_do_call)
