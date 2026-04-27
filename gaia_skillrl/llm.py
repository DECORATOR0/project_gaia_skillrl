from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

import httpx

from .config import LLMConfig
from .schemas import LLMMessage
from .utils import ensure_dir, extract_json_object, utc_timestamp, write_json

_llm_logger = logging.getLogger("nlrl_skills.llm")


@dataclass
class LLMCallResult:
    text: str
    raw_response: dict[str, Any]
    request_payload: dict[str, Any]


_SEMAPHORE_LOCK = Lock()
_REQUEST_SEMAPHORES: dict[int, BoundedSemaphore] = {}


def _shared_request_semaphore(limit: int) -> BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        semaphore = _REQUEST_SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = BoundedSemaphore(limit)
            _REQUEST_SEMAPHORES[limit] = semaphore
        return semaphore


class OpenAICompatibleLLM:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.disable_keepalive = os.environ.get("NLRL_LLM_DISABLE_KEEPALIVE", "").strip().lower() in {"1", "true", "yes", "on"}
        self.client = None if self._uses_codex_cli() else self._build_client()
        self.max_retries = int(os.environ.get("NLRL_LLM_MAX_RETRIES", "5"))
        self.retry_delay_seconds = float(os.environ.get("NLRL_LLM_RETRY_DELAY_SECONDS", "6"))
        self.retry_backoff_multiplier = float(os.environ.get("NLRL_LLM_RETRY_BACKOFF_MULTIPLIER", "1.6"))
        self.retry_jitter_seconds = float(os.environ.get("NLRL_LLM_RETRY_JITTER_SECONDS", "3"))
        self.retry_max_delay_seconds = float(os.environ.get("NLRL_LLM_RETRY_MAX_DELAY_SECONDS", "60"))
        self.json_repair_attempts = int(os.environ.get("NLRL_LLM_JSON_REPAIR_ATTEMPTS", "2"))
        concurrency_raw = os.environ.get("NLRL_LLM_MAX_CONCURRENT_REQUESTS", "").strip()
        self.request_semaphore = _shared_request_semaphore(int(concurrency_raw)) if concurrency_raw else None

    def _uses_responses_sse(self) -> bool:
        return self.config.api_mode.strip().lower() == "responses_sse"

    def _uses_codex_cli(self) -> bool:
        return self.config.api_mode.strip().lower() in {"codex_cli", "codex-exec", "codex_exec"}

    def _build_client(self) -> httpx.Client:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.disable_keepalive:
            headers["Connection"] = "close"
        limits = None
        if self.disable_keepalive:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=0)
        client_kwargs = {
            "base_url": self.config.base_url.rstrip("/") + "/",
            "timeout": self.config.timeout_seconds,
            "trust_env": False,
            "headers": headers,
        }
        if limits is not None:
            client_kwargs["limits"] = limits
        return httpx.Client(**client_kwargs)

    def _rebuild_client(self) -> None:
        if self._uses_codex_cli():
            return
        try:
            if self.client is not None:
                self.client.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass
        self.client = self._build_client()

    def _retry_after_seconds(self, exc: Exception) -> float | None:
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        retry_after = exc.response.headers.get("retry-after")
        if not retry_after:
            return None
        retry_after = retry_after.strip()
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        return max(0.0, min(delay, self.retry_max_delay_seconds))

    def _retry_delay(self, attempt: int, exc: Exception) -> float:
        retry_after = self._retry_after_seconds(exc)
        if retry_after is not None:
            base_delay = retry_after
        else:
            base_delay = self.retry_delay_seconds * (self.retry_backoff_multiplier ** attempt)
        base_delay = min(base_delay, self.retry_max_delay_seconds)
        jitter = random.uniform(0.0, self.retry_jitter_seconds) if self.retry_jitter_seconds > 0 else 0.0
        return min(base_delay + jitter, self.retry_max_delay_seconds)

    def _retry_sleep(self, delay_seconds: float) -> None:
        time.sleep(delay_seconds)

    @contextmanager
    def _request_slot(self):
        if self.request_semaphore is None:
            yield
            return
        self.request_semaphore.acquire()
        try:
            yield
        finally:
            self.request_semaphore.release()

    def _should_retry(self, exc: Exception) -> bool:
        return True

    def _format_last_error(self, exc: Exception) -> str:
        detail = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                body = exc.response.text[:2000]
                detail += f"\nHTTP status: {exc.response.status_code}\nResponse body:\n{body}"
            except Exception:
                pass
        return detail

    def _raise_exhausted(self, mode: str, exc: Exception) -> None:
        detail = self._format_last_error(exc)
        _llm_logger.error(
            "[%s] %s LLM call FAILED after %d attempts.\nLast error detail:\n%s",
            self.config.name, mode, self.max_retries + 1, detail,
            exc_info=True,
        )
        raise RuntimeError(
            f"[{self.config.name}] {mode} LLM call failed after {self.max_retries + 1} attempts. "
            f"Last error: {detail}"
        ) from exc

    def _supports_enable_thinking_flag(self) -> bool:
        return "qwen" in self.config.model.lower()

    def _env_bool(self, *names: str) -> bool | None:
        for name in names:
            raw = os.environ.get(name, "").strip().lower()
            if not raw:
                continue
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
        return None

    def _uses_vllm_chat_template_thinking(self) -> bool:
        role_prefix = f"NLRL_{self.config.name.upper()}"
        forced = self._env_bool(
            f"{role_prefix}_USE_CHAT_TEMPLATE_KWARGS",
            f"{role_prefix}_VLLM_CHAT_TEMPLATE_KWARGS",
            "NLRL_LLM_USE_CHAT_TEMPLATE_KWARGS",
            "NLRL_LLM_VLLM_CHAT_TEMPLATE_KWARGS",
        )
        if forced is not None:
            return forced
        if self.config.api_mode.strip().lower() != "chat_completions":
            return False
        if "qwen3" not in self.config.model.lower():
            return False
        base_url = self.config.base_url.lower()
        local_endpoint = any(host in base_url for host in ("127.0.0.1", "localhost", "0.0.0.0"))
        return local_endpoint or self.config.api_key.strip().upper() == "EMPTY"

    def _include_stream_usage(self) -> bool:
        role_prefix = f"NLRL_{self.config.name.upper()}"
        forced = self._env_bool(
            f"{role_prefix}_STREAM_INCLUDE_USAGE",
            "NLRL_LLM_STREAM_INCLUDE_USAGE",
        )
        if forced is not None:
            return forced
        return self._uses_vllm_chat_template_thinking()

    def _responses_endpoint(self) -> str:
        endpoint = self.config.base_url.rstrip("/")
        if endpoint.endswith("/responses"):
            return endpoint
        if endpoint.endswith("/v1"):
            return endpoint + "/responses"
        return endpoint

    def _build_payload(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if self._supports_enable_thinking_flag() and self.config.enable_thinking is not None:
            if self._uses_vllm_chat_template_thinking():
                payload["chat_template_kwargs"] = {"enable_thinking": self.config.enable_thinking}
            else:
                payload["enable_thinking"] = self.config.enable_thinking
        resolved_max_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        return payload

    def _build_responses_payload(self, messages: list[LLMMessage]) -> dict[str, Any]:
        instructions_parts = [m.content for m in messages if m.role == "system" and m.content.strip()]
        conversation_input = []
        for message in messages:
            if message.role == "system":
                continue
            role = message.role.strip().lower()
            if role == "assistant":
                conversation_input.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                    }
                )
                continue
            conversation_input.append(
                {
                    "role": role or "user",
                    "content": [{"type": "input_text", "text": message.content}],
                }
            )
        if not conversation_input:
            conversation_input = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Follow the provided instructions."}],
                }
            ]
        payload: dict[str, Any] = {
            "model": self.config.model,
            "stream": True,
            "input": conversation_input,
        }
        if instructions_parts:
            payload["instructions"] = "\n\n".join(instructions_parts)
        return payload

    def _extract_responses_output_text(self, payload: dict[str, Any]) -> str:
        response = payload.get("response", {})
        output_text = response.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = str(content.get("text", "")).strip()
                    if text:
                        return text
        raise RuntimeError("No output_text found in responses SSE response.")

    def _extract_responses_event_text(self, payload: dict[str, Any]) -> str:
        payload_type = str(payload.get("type", ""))
        if payload_type == "response.output_text.done":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        if payload_type == "response.content_part.done":
            part = payload.get("part", {})
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        if payload_type == "response.output_item.done":
            item = payload.get("item", {})
            if isinstance(item, dict):
                for content in item.get("content", []):
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") != "output_text":
                        continue
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        return ""

    def _extract_responses_error(self, payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        response = payload.get("response", {})
        response_error = response.get("error")
        if isinstance(response_error, dict):
            message = response_error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        return json.dumps(payload, ensure_ascii=False)

    def _parse_responses_sse_text(self, raw_text: str, request_payload: dict[str, Any]) -> LLMCallResult:
        last_payload: dict[str, Any] | None = None
        completed_payload: dict[str, Any] | None = None
        streamed_text_parts: list[str] = []
        event_text_candidates: list[str] = []
        for block in raw_text.split("\n\n"):
            lines = block.strip().splitlines()
            if not lines:
                continue
            data_lines = [line[6:] for line in lines if line.startswith("data: ")]
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            last_payload = payload
            payload_type = str(payload.get("type", ""))
            if payload_type == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    streamed_text_parts.append(delta)
            else:
                candidate_text = self._extract_responses_event_text(payload)
                if candidate_text:
                    event_text_candidates.append(candidate_text)
            if payload_type == "response.completed":
                completed_payload = payload
                break
            if payload_type in {"error", "response.failed"}:
                raise RuntimeError(self._extract_responses_error(payload))
        if completed_payload is None:
            error_message = "No response.completed event found in responses SSE output."
            if last_payload is not None:
                error_message += f" Last payload: {self._extract_responses_error(last_payload)}"
            raise RuntimeError(error_message)
        try:
            text = self._extract_responses_output_text(completed_payload)
        except RuntimeError:
            text = ""
            for candidate in reversed(event_text_candidates):
                if candidate:
                    text = candidate
                    break
            if not text:
                text = "".join(streamed_text_parts).strip()
            if not text:
                raise
        text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text or "").strip()
        return LLMCallResult(text=text, raw_response=completed_payload, request_payload=request_payload)

    def _responses_chat(self, payload: dict[str, Any]) -> LLMCallResult:
        with self._request_slot():
            for attempt in range(self.max_retries + 1):
                try:
                    headers = {
                        "Accept": "text/event-stream",
                        "Content-Type": "application/json",
                    }
                    if self.config.api_key:
                        headers["Authorization"] = f"Bearer {self.config.api_key}"
                    with httpx.Client(trust_env=False, timeout=float(self.config.timeout_seconds)) as client:
                        response = client.post(
                            self._responses_endpoint(),
                            headers=headers,
                            json=payload,
                        )
                        response.raise_for_status()
                    return self._parse_responses_sse_text(response.text, payload)
                except Exception as exc:
                    if attempt >= self.max_retries:
                        self._raise_exhausted("Responses-SSE", exc)
                    retry_delay = self._retry_delay(attempt, exc)
                    _llm_logger.warning(
                        "[%s] Responses-SSE attempt %d/%d failed: %s — retrying in %.1fs",
                        self.config.name, attempt + 1, self.max_retries + 1, exc, retry_delay,
                    )
                    self._rebuild_client()
                    self._retry_sleep(retry_delay)
        raise RuntimeError("Responses SSE LLM call exhausted retries without returning a response.")

    def _extract_delta_text(self, chunk: dict[str, Any]) -> str:
        choices = chunk.get("choices", [])
        if not choices:
            return ""
        choice = choices[0]
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            delta = {}
        fragments: list[str] = []
        content = delta.get("content")
        if isinstance(content, str):
            fragments.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
        if not fragments:
            message = choice.get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    fragments.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text")
                            if isinstance(text, str):
                                fragments.append(text)
        return "".join(fragments)

    def _chat_stream(self, payload: dict[str, Any]) -> LLMCallResult:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        if self._include_stream_usage():
            stream_options = dict(stream_payload.get("stream_options") or {})
            stream_options["include_usage"] = True
            stream_payload["stream_options"] = stream_options
        wall_timeout = float(
            os.environ.get("NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS", "").strip()
            or self.config.timeout_seconds
        )
        with self._request_slot():
            for attempt in range(self.max_retries + 1):
                response = None
                chunks: list[dict[str, Any]] = []
                text_parts: list[str] = []
                finish_reason = ""
                reasoning_chunk_count = 0
                content_chunk_count = 0
                first_content_chunk_index: int | None = None
                usage: dict[str, Any] | None = None
                try:
                    started_at = time.monotonic()
                    if self.client is None:
                        raise RuntimeError("HTTP client is not initialized.")
                    with self.client.stream("POST", "chat/completions", json=stream_payload) as response:
                        response.raise_for_status()
                        for raw_line in response.iter_lines():
                            elapsed = time.monotonic() - started_at
                            if elapsed > wall_timeout:
                                raise TimeoutError(
                                    f"Streaming response exceeded wall timeout of {wall_timeout:.1f}s "
                                    f"after {len(chunks)} chunks and {len(text_parts)} text fragments."
                                )
                            if raw_line is None:
                                continue
                            line = raw_line.strip()
                            if not line:
                                continue
                            if isinstance(line, bytes):
                                line = line.decode("utf-8", errors="replace")
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data:
                                continue
                            if data == "[DONE]":
                                break
                            chunk = json.loads(data)
                            chunks.append(chunk)
                            usage_payload = chunk.get("usage")
                            if isinstance(usage_payload, dict):
                                usage = usage_payload
                            text = self._extract_delta_text(chunk)
                            if text:
                                text_parts.append(text)
                                content_chunk_count += 1
                                if first_content_chunk_index is None:
                                    first_content_chunk_index = len(chunks)
                            choices = chunk.get("choices", [])
                            if choices and isinstance(choices[0], dict):
                                delta = choices[0].get("delta")
                                if isinstance(delta, dict):
                                    reasoning = delta.get("reasoning_content")
                                    if isinstance(reasoning, str) and reasoning:
                                        reasoning_chunk_count += 1
                                reason = choices[0].get("finish_reason")
                                if isinstance(reason, str) and reason:
                                    finish_reason = reason
                    assembled_text = "".join(text_parts).strip()
                    if not assembled_text:
                        raise ValueError("Streaming response produced empty content.")
                    return LLMCallResult(
                        text=assembled_text,
                        raw_response={
                            "stream": True,
                            "chunk_count": len(chunks),
                            "content_chunk_count": content_chunk_count,
                            "reasoning_chunk_count": reasoning_chunk_count,
                            "first_content_chunk_index": first_content_chunk_index,
                            "finish_reason": finish_reason,
                            "last_chunk": chunks[-1] if chunks else {},
                            "usage": usage,
                        },
                        request_payload=stream_payload,
                    )
                except Exception as exc:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass
                    if attempt >= self.max_retries:
                        self._raise_exhausted("Streaming", exc)
                    retry_delay = self._retry_delay(attempt, exc)
                    _llm_logger.warning(
                        "[%s] Streaming attempt %d/%d failed: %s — retrying in %.1fs",
                        self.config.name, attempt + 1, self.max_retries + 1, exc, retry_delay,
                    )
                    self._rebuild_client()
                    self._retry_sleep(retry_delay)
        raise RuntimeError("Streaming LLM call exhausted retries without returning a response.")

    def chat(self, messages: list[LLMMessage], *, temperature: float | None = None, max_tokens: int | None = None) -> LLMCallResult:
        if self._uses_codex_cli():
            return self._codex_cli_chat(messages, temperature=temperature, max_tokens=max_tokens)
        if self._uses_responses_sse():
            payload = self._build_responses_payload(messages)
            return self._responses_chat(payload)
        payload = self._build_payload(messages, temperature=temperature, max_tokens=max_tokens)
        if self.config.stream:
            return self._chat_stream(payload)
        last_error: Exception | None = None
        response: httpx.Response | None = None
        with self._request_slot():
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.client.post("chat/completions", json=payload)
                    response.raise_for_status()
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        self._raise_exhausted("Non-streaming", exc)
                    retry_delay = self._retry_delay(attempt, exc)
                    _llm_logger.warning(
                        "[%s] Non-streaming attempt %d/%d failed: %s — retrying in %.1fs",
                        self.config.name, attempt + 1, self.max_retries + 1, exc, retry_delay,
                    )
                    self._rebuild_client()
                    self._retry_sleep(retry_delay)
        if response is None:  # pragma: no cover - defensive
            raise RuntimeError(f"LLM call failed: {last_error}")
        raw_response = response.json()
        text = ""
        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(
                item.get("text", "")
                for item in text
                if isinstance(item, dict)
            )
        text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text or "").strip()
        return LLMCallResult(text=text, raw_response=raw_response, request_payload=payload)

    def _codex_cli_prompt(self, messages: list[LLMMessage]) -> str:
        blocks: list[str] = []
        for message in messages:
            role = message.role.strip().lower()
            if role == "system":
                blocks.append(message.content)
            else:
                blocks.append(f"{role.upper() or 'USER'}:\n{message.content}")
        return "\n".join(blocks).strip()

    def _codex_cli_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCallResult:
        prompt = self._codex_cli_prompt(messages)
        codex_bin = os.environ.get("NLRL_CODEX_CLI_PATH", "codex")
        workdir = os.environ.get("NLRL_CODEX_CLI_WORKDIR", "/tmp")
        timeout_seconds = int(os.environ.get("NLRL_CODEX_CLI_TIMEOUT_SECONDS", str(self.config.timeout_seconds)))
        extra_args = shlex_split(os.environ.get("NLRL_CODEX_CLI_EXTRA_ARGS", ""))
        request_payload = {
            "api_mode": self.config.api_mode,
            "model": self.config.model,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
            "prompt": prompt,
            "timeout_seconds": timeout_seconds,
        }
        with tempfile.NamedTemporaryFile(prefix="nlrl_codex_last_", suffix=".txt", delete=False) as handle:
            output_path = Path(handle.name)
        cmd = [
            codex_bin,
            "exec",
            "-m",
            self.config.model,
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-rules",
            "-c",
            'approval_policy="never"',
            "--output-last-message",
            str(output_path),
            "-C",
            workdir,
            *extra_args,
            "-",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
            elapsed = time.monotonic() - started
            text = output_path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                text = completed.stdout.strip()
            if completed.returncode != 0:
                raise RuntimeError(
                    f"codex exec failed with returncode={completed.returncode}\n"
                    f"stdout:\n{completed.stdout[-4000:]}\n"
                    f"stderr:\n{completed.stderr[-4000:]}"
                )
            if not text:
                raise RuntimeError("codex exec returned empty final message.")
            return LLMCallResult(
                text=text,
                raw_response={
                    "provider": "codex_cli",
                    "command": cmd,
                    "returncode": completed.returncode,
                    "stdout_tail": completed.stdout[-4000:],
                    "stderr_tail": completed.stderr[-4000:],
                    "elapsed_seconds": elapsed,
                    "output_last_message": str(output_path),
                },
                request_payload=request_payload | {"command": cmd},
            )
        finally:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass

    def _json_repair_prompt(self, error: Exception | str) -> str:
        return (
            "Your previous reply could not be parsed as a single valid JSON object.\n"
            f"Parser error: {error}\n"
            "Reply again with exactly one valid JSON object and nothing else.\n"
            "Requirements:\n"
            "- Keep the same intended decision/content unless the parser issue itself forces a minimal correction.\n"
            "- Do not output markdown fences or any prose before/after the JSON.\n"
            "- Do not use placeholders, Python expressions, string concatenation, or pseudo-code inside JSON.\n"
            "- Materialize every list/object/value as concrete JSON.\n"
        )

    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], LLMCallResult]:
        conversation = list(messages)
        last_error: Exception | None = None

        for repair_attempt in range(self.json_repair_attempts + 1):
            result = self.chat(conversation, temperature=temperature, max_tokens=max_tokens)
            try:
                payload = extract_json_object(result.text)
                if repair_attempt and isinstance(result.raw_response, dict):
                    result.raw_response["json_repair_attempts"] = repair_attempt
                    result.raw_response["json_repair_last_error"] = "" if last_error is None else str(last_error)
                return payload, result
            except Exception as exc:
                last_error = exc
                if repair_attempt >= self.json_repair_attempts:
                    raise
                conversation = [
                    *conversation,
                    LLMMessage(role="assistant", content=result.text),
                    LLMMessage(role="user", content=self._json_repair_prompt(exc)),
                ]

        raise RuntimeError("JSON repair loop exited unexpectedly.")


def shlex_split(value: str) -> list[str]:
    if not value.strip():
        return []
    import shlex

    return shlex.split(value)


def log_llm_call(log_dir: Path, role_name: str, result: LLMCallResult) -> None:
    ensure_dir(log_dir)
    stamp = utc_timestamp()
    write_json(
        log_dir / f"{stamp}_{role_name}_request.json",
        result.request_payload,
    )
    write_json(
        log_dir / f"{stamp}_{role_name}_response.json",
        {
            "text": result.text,
            "raw_response": result.raw_response,
        },
    )
