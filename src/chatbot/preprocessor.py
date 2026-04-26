"""User input preprocessing for the MCPDischarge chatbot.

Goal:
- normalize weird punctuation / symbols across OS encodings
- fix a few common typos seen in demos
- optionally (and safely) use an LLM to rewrite the user query into a clean form

Security notes:
- RBAC/PHI enforcement must still run on the original input.
- Preprocessing must not introduce PHI or expand the scope of a request.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

from src.chatbot.llm_provider import chat_text, is_configured as llm_configured


_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\uFEFF]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def sanitize_user_text(text: str) -> str:
    if not text:
        return ""

    t = unicodedata.normalize("NFKC", text)
    t = _ZERO_WIDTH_RE.sub("", t)
    t = _CONTROL_RE.sub(" ", t)

    # Standardize common “smart punctuation” to ASCII to avoid mojibake across Windows.
    t = (
        t.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201C", '"')
        .replace("\u201D", '"')
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2026", "...")
        .replace("\u00A0", " ")
    )

    # Collapse weird repeated punctuation and whitespace
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()

    # Normalize common patient-id variants: "pat001", "pat 1", "PAT-1" -> "PAT-001"
    def _norm_pat(m: re.Match[str]) -> str:
        num = int(m.group(1))
        return f"PAT-{num:03d}"

    t = re.sub(r"\bPAT\s*[-#]?\s*0*(\d{1,3})\b", _norm_pat, t, flags=re.IGNORECASE)

    # A few common typos seen in the UI demos
    t = re.sub(r"\bdishcharge\b", "discharge", t, flags=re.IGNORECASE)
    t = re.sub(r"\bmedecine\b", "medicine", t, flags=re.IGNORECASE)

    return t


def _should_llm_rewrite(original: str, sanitized: str) -> bool:
    if not llm_configured():
        return False
    if not os.getenv("CHATBOT_USE_LLM_PREPROCESSOR", "").strip():
        return False
    if not original or not sanitized:
        return False

    # Heuristics: lots of non-ascii, or the user message got materially changed by sanitization.
    non_ascii = sum(1 for ch in original if ord(ch) > 127)
    if non_ascii >= 6:
        return True
    if original != sanitized and len(original) > 40:
        return True

    # Unbalanced brackets/quotes often break intent extraction.
    for a, b in [("(", ")"), ("[", "]"), ("{", "}")]:
        if original.count(a) != original.count(b):
            return True
    if original.count('"') % 2 == 1 or original.count("'") % 2 == 1:
        return True

    return False


async def preprocess_user_text(user_text: str, *, conversation_hint: Optional[str] = None) -> str:
    """Return a cleaned user text safe for downstream intent/tool routing.

    The returned string should preserve meaning but be more robust to parsing.
    """
    sanitized = sanitize_user_text(user_text)
    if not _should_llm_rewrite(user_text, sanitized):
        return sanitized

    # LLM rewrite: keep it narrow and deterministic.
    sys = (
        "You are a query normalizer for a hospital discharge chatbot.\n"
        "Rewrite the user's message into a clean, grammatical, single-line English query.\n"
        "Rules:\n"
        "- Preserve meaning exactly; do not add new requests.\n"
        "- Preserve patient IDs (PAT-XXX) and drug names; do not invent IDs.\n"
        "- Remove unbalanced symbols and weird special characters.\n"
        "- Output ONLY the rewritten query text (no quotes, no explanations)."
    )
    if conversation_hint:
        sys += f"\nContext hint (non-PHI): {conversation_hint}"

    # Allow using alternate Azure deployment creds for preprocessing if provided.
    endpoint_one = os.getenv("AZURE_OPENAI_ENDPOINT_ONE", "").strip()
    key_one = os.getenv("AZURE_OPENAI_API_KEY_ONE", "").strip()
    deployment_one = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME_ONE", "").strip().strip('"').strip("'")
    # Some configs may have a typo; support both.
    version_one = (os.getenv("AZURE_OPENAI_API_VERSION_ONE", "") or os.getenv("ZURE_OPENAI_API_VERSION_ONE", "")).strip()

    use_one = bool(endpoint_one and key_one and deployment_one)

    try:
        msg = await chat_text(
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": sanitized[:1000]}],
            temperature=0.0,
            endpoint=endpoint_one if use_one else None,
            api_key=key_one if use_one else None,
            api_version=version_one if (use_one and version_one) else None,
            deployment=deployment_one if use_one else None,
        )
        out = (msg.get("content") or "").strip()
        out = out.replace("\n", " ").strip()
        out = sanitize_user_text(out)
        return out or sanitized
    except Exception:
        return sanitized
