#!/usr/bin/env python3
"""
LLM utilities: robust JSON parsing + DeepSeek API calling.
Ported from memory-pkg/src/claude_memory/json_utils.py (7-level cascade).
"""

import json
import logging
import os
import re
import time
from typing import Any

import requests

from config import load_config

logger = logging.getLogger("nailong.llm")

# 致命错误(不可重试,应立即中止整批):认证失败 / 余额不足 / 权限不足
FATAL_STATUS = {401, 402, 403}


class LLMFatalError(Exception):
    """LLM 调用遇到不可重试的致命错误(如 402 余额不足)。
    调用方应捕获它并立即中止整批,而不是逐个重试空转。"""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code} {message}")


# ── JSON parsing (ported from old json_utils.py) ──────────


def _escape_latex_in_json(content: str) -> str:
    """Escape LaTeX backslash-letter combos that are INVALID JSON escapes."""
    _INVALID_ESC = r'[ac-eg-ij-mo-qs-vx-zA-Z]'
    content = re.sub(rf'(?<!\\)\\(?={_INVALID_ESC})', r'\\\\', content)
    content = content.replace('\\\\\\\\', '\\\\')
    return content


def _escape_control_chars(content: str) -> str:
    """Replace raw control characters with \\uXXXX JSON escapes."""
    return re.sub(
        r'[\x00-\x08\x0b\x0c\x0e-\x1f]',
        lambda m: f'\\u{ord(m.group()):04x}',
        content
    )


def _remove_trailing_commas(content: str) -> str:
    """Remove trailing commas before ] or } in JSON."""
    return re.sub(r',(\s*[}\]])', r'\1', content)


def robust_json_parse(content: str) -> dict:
    """Parse JSON with cascading fallbacks for common LLM formatting errors.

    Tries in order:
      1. Strict parse
      2. Extract from markdown ```json fence, then strict
      3. Escape LaTeX backslashes, then strict
      4. Escape control characters, then strict
      5. Remove trailing commas, then strict
      6. Control chars + trailing commas, then strict
      7. Return {} (graceful degradation)
    """
    # Strategy 1: Strict parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown code block
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Escape LaTeX backslashes
    try:
        return json.loads(_escape_latex_in_json(content))
    except json.JSONDecodeError:
        pass

    # Strategy 4: Escape control characters
    try:
        return json.loads(_escape_control_chars(content))
    except json.JSONDecodeError:
        pass

    # Strategy 5: Remove trailing commas
    try:
        return json.loads(_remove_trailing_commas(content))
    except json.JSONDecodeError:
        pass

    # Strategy 6: Control chars + trailing commas
    try:
        return json.loads(
            _escape_control_chars(_remove_trailing_commas(content)))
    except json.JSONDecodeError:
        logger.warning(
            "JSON parse FAILED after all strategies (first 200 chars): %s",
            repr(content[:200])
        )
        return {}


# ── LLM Calling ───────────────────────────────────────────


def call_llm(prompt: str, system: str = "",
             model: str | None = None,
             temperature: float | None = None,
             max_tokens: int | None = None) -> dict | None:
    """Call DeepSeek API via Anthropic-compatible endpoint.

    Uses ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL from env vars
    (already configured in Claude Code settings.json).

    Returns parsed JSON dict, or None on failure.
    """
    cfg = load_config()
    api = cfg["api"]
    llm_cfg = cfg["llm"]

    if model is None:
        model = llm_cfg["model"]
    if temperature is None:
        temperature = llm_cfg["temperature"]
    if max_tokens is None:
        max_tokens = llm_cfg["max_tokens"]

    headers = {
        "Authorization": f"Bearer {api['token']}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [],
    }
    if system:
        body["system"] = system
    body["messages"].append({"role": "user", "content": prompt})

    # Build URL: Anthropic-compatible messages endpoint
    base = api["base_url"].rstrip("/")
    url = f"{base}/messages"

    max_retries = llm_cfg.get("max_retries", 3)
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url, headers=headers, json=body,
                timeout=llm_cfg.get("timeout", 30)
            )
            if resp.status_code == 200:
                data = resp.json()
                # Extract text from Anthropic-format response
                content_blocks = data.get("content", [])
                text = ""
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
                if text:
                    return robust_json_parse(text)
                return None
            if resp.status_code in FATAL_STATUS:
                # 致命(余额/认证/权限)——重试无意义,直接抛出让整批中止
                raise LLMFatalError(resp.status_code, resp.text[:200])
            # 其它(429/5xx/瞬时 4xx)——可重试
            logger.warning(
                "LLM API error (attempt %d/%d): HTTP %d %s",
                attempt + 1, max_retries,
                resp.status_code, resp.text[:200]
            )
        except LLMFatalError:
            raise                          # 不吞,往上抛给调用方中止整批
        except (requests.Timeout, requests.ConnectionError) as e:
            # 瞬时网络问题(断网/超时)——退避后重试
            logger.warning(
                "LLM API 网络错误 (attempt %d/%d): %s",
                attempt + 1, max_retries, e
            )
        except Exception as e:
            logger.warning(
                "LLM API error (attempt %d/%d): %s",
                attempt + 1, max_retries, e
            )

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    return None


def call_llm_raw(prompt: str, system: str = "",
                 model: str | None = None,
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> str | None:
    """Call LLM and return raw text (not parsed as JSON).
    For prompts where the output is markdown, not JSON.
    """
    cfg = load_config()
    api = cfg["api"]
    llm_cfg = cfg["llm"]

    if model is None:
        model = llm_cfg["model"]
    if temperature is None:
        temperature = llm_cfg["temperature"]
    if max_tokens is None:
        max_tokens = llm_cfg["max_tokens"]

    headers = {
        "Authorization": f"Bearer {api['token']}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    base = api["base_url"].rstrip("/")
    url = f"{base}/messages"

    max_retries = llm_cfg.get("max_retries", 3)
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url, headers=headers, json=body,
                timeout=llm_cfg.get("timeout", 30)
            )
            if resp.status_code == 200:
                data = resp.json()
                content_blocks = data.get("content", [])
                text = ""
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
                return text
            if resp.status_code in FATAL_STATUS:
                raise LLMFatalError(resp.status_code, resp.text[:200])
            logger.warning(
                "LLM API error (attempt %d/%d): HTTP %d",
                attempt + 1, max_retries, resp.status_code
            )
        except LLMFatalError:
            raise
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning(
                "LLM API 网络错误 (attempt %d/%d): %s",
                attempt + 1, max_retries, e
            )
        except Exception as e:
            logger.warning(
                "LLM API error (attempt %d/%d): %s",
                attempt + 1, max_retries, e
            )

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    return None
