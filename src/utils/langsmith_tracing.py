"""LangSmith tracing helpers (safe-by-default).

This project may handle sensitive healthcare data. LangSmith tracing is useful
for debugging/evaluation, but we must not emit PHI into traces.

These helpers:
- enable tracing only when LANGCHAIN_TRACING_V2 is truthy and LANGCHAIN_API_KEY is set
- provide `traceable_safe(...)` decorator that sanitizes inputs/outputs
"""

from __future__ import annotations

import asyncio
import functools
import os
from typing import Any, Callable, Optional

from src.chatbot.phi_guard import contains_phi_keys


def _is_truthy_env(name: str) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _get_langsmith_api_key() -> str:
    # Accept both for compatibility across LangSmith/LangChain versions.
    return (os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY") or "").strip()


def langsmith_enabled() -> bool:
    # LangSmith uses the LANGCHAIN_* env vars for compatibility.
    if not (_is_truthy_env("LANGCHAIN_TRACING_V2") or _is_truthy_env("LANGSMITH_TRACING")):
        return False
    if not _get_langsmith_api_key():
        return False
    return True


def langsmith_status() -> dict[str, Any]:
    """Return a non-sensitive diagnostic view of LangSmith env configuration."""
    tracing_enabled = _is_truthy_env("LANGCHAIN_TRACING_V2") or _is_truthy_env("LANGSMITH_TRACING")
    api_key_present = bool(_get_langsmith_api_key())
    enabled = bool(tracing_enabled and api_key_present)
    return {
        "enabled": enabled,
        "tracing_env": {
            "LANGCHAIN_TRACING_V2": _is_truthy_env("LANGCHAIN_TRACING_V2"),
            "LANGSMITH_TRACING": _is_truthy_env("LANGSMITH_TRACING"),
        },
        "api_key_present": api_key_present,
        "project": (os.getenv("LANGCHAIN_PROJECT") or os.getenv("LANGSMITH_PROJECT") or "").strip() or None,
        "endpoint": (os.getenv("LANGCHAIN_ENDPOINT") or os.getenv("LANGSMITH_ENDPOINT") or "").strip() or None,
    }


def _summary(value: Any) -> Any:
    """Return a non-sensitive summary of a value (no raw content)."""
    try:
        if value is None:
            return None
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return {"_type": "str", "len": len(value)}
        if isinstance(value, bytes):
            return {"_type": "bytes", "len": len(value)}
        if isinstance(value, list):
            return {"_type": "list", "len": len(value)}
        if isinstance(value, tuple):
            return {"_type": "tuple", "len": len(value)}
        if isinstance(value, set):
            return {"_type": "set", "len": len(value)}
        if isinstance(value, dict):
            keys = []
            try:
                keys = [str(k)[:80] for k in list(value.keys())[:40]]
            except Exception:
                keys = []
            return {"_type": "dict", "keys": keys, "len": len(value)}
        # Fallback: type only (avoid __repr__ leaking data)
        return {"_type": type(value).__name__}
    except Exception:
        return {"_type": "unknown"}


def _sanitize_inputs(
    inputs: dict[str, Any],
    *,
    drop_keys: set[str],
    include_keys: Optional[set[str]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (inputs or {}).items():
        ks = str(k)
        if ks in drop_keys:
            continue
        if include_keys is not None and ks not in include_keys:
            continue
        out[ks] = _summary(v)
    # Mark if the raw input (if any) appears to contain PHI keys.
    try:
        out["_contains_phi_keys"] = bool(contains_phi_keys(inputs))
    except Exception:
        out["_contains_phi_keys"] = None
    return out


def _sanitize_output(output: Any) -> Any:
    try:
        return {
            "summary": _summary(output),
            "contains_phi_keys": bool(contains_phi_keys(output)),
        }
    except Exception:
        return {"summary": _summary(output), "contains_phi_keys": None}


def traceable_safe(
    *,
    name: str,
    run_type: str,
    process_inputs: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    process_outputs: Optional[Callable[[Any], Any]] = None,
):
    """LangSmith `traceable` wrapper — lazy, cached, and safe-by-default.

    - No-op when LANGCHAIN_TRACING_V2 is not set or LANGCHAIN_API_KEY is missing.
    - Evaluated lazily on first call so it works even if .env is loaded after import.
    - Falls back to the unwrapped function if LangSmith is unavailable or errors.
    - Never propagates LangSmith errors to the caller.

    NOTE: Raw strings (e.g. user_text) and tool payloads are never logged directly.
    Use `process_inputs` / `process_outputs` to emit only sanitised summaries.
    """

    def _decorator(fn: Callable):
        # Per-function cache: None = not yet resolved, False = disabled, callable = traced fn.
        _cache: list = [None]

        def _resolve() -> Any:
            if _cache[0] is not None:
                return _cache[0]
            if not langsmith_enabled():
                _cache[0] = False
                return False
            try:
                from langsmith import traceable  # type: ignore
                traced = traceable(
                    name=name,
                    run_type=run_type,
                    process_inputs=process_inputs,
                    process_outputs=process_outputs,
                )(fn)
                _cache[0] = traced
                return traced
            except Exception:
                _cache[0] = False
                return False

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                traced = _resolve()
                if traced is False:
                    return await fn(*args, **kwargs)
                try:
                    return await traced(*args, **kwargs)
                except Exception:
                    # If the traced version fails (e.g. LangSmith network error or
                    # anyio cancel-scope mismatch), fall back silently.
                    _cache[0] = False
                    return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            traced = _resolve()
            if traced is False:
                return fn(*args, **kwargs)
            try:
                return traced(*args, **kwargs)
            except Exception:
                _cache[0] = False
                return fn(*args, **kwargs)

        return sync_wrapper

    return _decorator


# ---- Common processors (safe defaults) ------------------------------------


def process_inputs_controller(inputs: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_inputs(
        inputs,
        drop_keys={"user_text", "messages", "chat_history"},
        include_keys=None,
    )


def process_outputs_controller(output: Any) -> Any:
    return _sanitize_output(output)


def process_inputs_llm_provider(inputs: dict[str, Any]) -> dict[str, Any]:
    msgs = inputs.get("messages")
    tools = inputs.get("tools")
    out = {
        "messages_len": len(msgs) if isinstance(msgs, list) else None,
        "tools_len": len(tools) if isinstance(tools, list) else None,
        "temperature": inputs.get("temperature"),
    }
    try:
        out["_contains_phi_keys"] = bool(contains_phi_keys(inputs))
    except Exception:
        out["_contains_phi_keys"] = None
    return out


def process_outputs_llm_provider(output: Any) -> Any:
    # The raw message content may be user-visible and could include sensitive
    # info; only log shape.
    try:
        content = None
        tool_calls = None
        if isinstance(output, dict):
            content = output.get("content")
            tool_calls = output.get("tool_calls")
        return {
            "has_content": bool((content or "").strip()) if isinstance(content, str) else bool(content),
            "content_len": len(content) if isinstance(content, str) else None,
            "tool_calls_len": len(tool_calls) if isinstance(tool_calls, list) else None,
            "contains_phi_keys": bool(contains_phi_keys(output)),
        }
    except Exception:
        return _sanitize_output(output)


def process_inputs_mcp_retry(inputs: dict[str, Any]) -> dict[str, Any]:
    # inputs: fn, tool, server, patient_id
    return _sanitize_inputs(
        inputs,
        drop_keys={"fn"},
        include_keys={"tool", "server", "patient_id", "max_retries"},
    )


def process_outputs_mcp_retry(output: Any) -> Any:
    # Never log tool payloads; only shape.
    return _sanitize_output(output)


def process_inputs_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_inputs(
        inputs,
        drop_keys={"client", "meds", "medications"},
        include_keys=None,
    )


def process_outputs_workflow(output: Any) -> Any:
    return _sanitize_output(output)


def process_inputs_mcp_tool(inputs: dict[str, Any]) -> dict[str, Any]:
    # FastMCP tool calls may include PHI depending on the server. Only log shapes.
    return _sanitize_inputs(
        inputs,
        drop_keys=set(),
        include_keys=None,
    )


def process_outputs_mcp_tool(output: Any) -> Any:
    return _sanitize_output(output)


def instrument_fastmcp_tools(mcp: Any, *, server: str) -> None:
    """Wrap `mcp.tool(...)` so all subsequently-registered tools are LangSmith-traced.

    Safe-by-default:
    - no-op unless LangSmith tracing is enabled (see `langsmith_enabled`)
    - inputs/outputs are summarized (no raw payloads)
    """

    if getattr(mcp, "_langsmith_tools_instrumented", False):
        return

    original_tool = getattr(mcp, "tool", None)
    if original_tool is None:
        return

    def _tool(*args: Any, **kwargs: Any):
        decorator = original_tool(*args, **kwargs)

        def _decorate(fn: Callable):
            tool_name = getattr(fn, "__name__", "tool")
            wrapped = traceable_safe(
                name=f"mcp.{server}.{tool_name}",
                run_type="tool",
                process_inputs=process_inputs_mcp_tool,
                process_outputs=process_outputs_mcp_tool,
            )(fn)
            return decorator(wrapped)

        return _decorate

    setattr(mcp, "tool", _tool)
    setattr(mcp, "_langsmith_tools_instrumented", True)
