"""Azure OpenAI helper for the chat gateway.

Reads configuration from environment variables (recommended via python-dotenv):
  - AZURE_OPENAI_ENDPOINT (base resource endpoint, or full deployments URL)
  - AZURE_OPENAI_API_KEY
  - AZURE_OPENAI_API_VERSION
  - AZURE_OPENAI_DEPLOYMENT_NAME
"""

from __future__ import annotations

import os
import re
from typing import Optional


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
    return bool(os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"))


async def chat_completion(user_message: str, system_message: str) -> str:
    """Call Azure OpenAI Chat Completions via the OpenAI Python SDK."""
    endpoint_raw = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    deployment_env = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")

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

    # OpenAI SDK is sync; run in thread via asyncio.to_thread to keep gateway async.
    import asyncio

    def _do_call() -> str:
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()

    return await asyncio.to_thread(_do_call)

