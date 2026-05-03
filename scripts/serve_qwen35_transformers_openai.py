#!/usr/bin/env python3
"""Minimal OpenAI-compatible chat server for local Qwen3.5 checkpoints.

This is a fallback for architectures that local vLLM cannot serve yet. It
implements only the OpenAI endpoints used by the GAIA executor.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    chat_template_kwargs: dict[str, Any] | None = None
    enable_thinking: bool | None = None
    thinking_token_budget: int | None = None
    thinking_budget: int | None = None
    stream_options: dict[str, Any] | None = None


def now() -> int:
    return int(time.time())


def sse(obj: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def create_app(args: argparse.Namespace) -> FastAPI:
    app = FastAPI()
    tokenizer = AutoTokenizer.from_pretrained(args.model_path, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model_path,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    ).to(args.device)
    model.eval()
    generation_slots = threading.Semaphore(max(1, args.max_concurrent_generations))

    class CloseThinkOrBudgetCriteria(StoppingCriteria):
        def __init__(self, prompt_len: int, thinking_budget: int, close_ids: list[int]):
            self.prompt_len = prompt_len
            self.thinking_budget = thinking_budget
            self.close_ids = close_ids

        def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor, **kwargs: Any) -> torch.BoolTensor:
            generated_len = int(input_ids.shape[-1] - self.prompt_len)
            stop = generated_len >= self.thinking_budget
            if self.close_ids and input_ids.shape[-1] >= len(self.close_ids):
                close = torch.tensor(self.close_ids, device=input_ids.device, dtype=input_ids.dtype)
                stop = stop or bool(torch.all(input_ids[:, -len(self.close_ids) :] == close).item())
            return torch.full((input_ids.shape[0],), stop, device=input_ids.device, dtype=torch.bool)

    def resolve_thinking_budget(req: ChatRequest) -> int | None:
        raw = req.thinking_token_budget
        if raw is None:
            raw = req.thinking_budget
        if raw is None and req.chat_template_kwargs:
            raw = req.chat_template_kwargs.get("thinking_token_budget")
        if raw is None and req.chat_template_kwargs:
            raw = req.chat_template_kwargs.get("thinking_budget")
        if raw is None:
            return None
        budget = int(raw)
        return budget if budget > 0 else None

    @app.get("/v1/models")
    def models() -> JSONResponse:
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": args.served_model_name,
                        "object": "model",
                        "created": now(),
                        "owned_by": "local-transformers",
                        "root": args.model_path,
                        "max_model_len": args.max_model_len,
                    }
                ],
            }
        )

    def render_prompt(req: ChatRequest) -> str:
        template_kwargs = dict(req.chat_template_kwargs or {})
        if req.enable_thinking is not None and "enable_thinking" not in template_kwargs:
            template_kwargs["enable_thinking"] = req.enable_thinking
        return tokenizer.apply_chat_template(
            req.messages,
            tokenize=False,
            add_generation_prompt=True,
            **template_kwargs,
        )

    def split_final_text(text: str, thinking: bool) -> tuple[str, str]:
        if "</think>" in text:
            left, right = text.split("</think>", 1)
            return left.replace("<think>", "", 1).strip(), right.lstrip()
        if thinking:
            return text.replace("<think>", "", 1).strip(), ""
        return "", text

    def stream_chat(req: ChatRequest) -> Iterator[bytes]:
        request_id = "chatcmpl-" + uuid.uuid4().hex
        prompt = render_prompt(req)
        inputs = tokenizer(prompt, return_tensors="pt").to(args.device)
        input_token_count = int(inputs["input_ids"].shape[-1])
        max_new_tokens = req.max_tokens or args.default_max_tokens
        max_new_tokens = max(1, min(max_new_tokens, args.max_model_len - input_token_count))
        if max_new_tokens <= 0:
            raise HTTPException(status_code=400, detail="prompt exceeds max_model_len")

        temperature = args.temperature if req.temperature is None else req.temperature
        top_p = args.top_p if req.top_p is None else req.top_p

        def build_generation_kwargs(
            generation_inputs: dict[str, torch.Tensor],
            streamer: TextIteratorStreamer,
            token_limit: int,
            stopping_criteria: StoppingCriteriaList | None = None,
        ) -> dict[str, Any]:
            kwargs: dict[str, Any] = {
                **generation_inputs,
                "streamer": streamer,
                "max_new_tokens": token_limit,
                "eos_token_id": tokenizer.eos_token_id,
                "pad_token_id": tokenizer.eos_token_id,
            }
            if stopping_criteria is not None:
                kwargs["stopping_criteria"] = stopping_criteria
            if temperature and temperature > 0:
                kwargs.update({"do_sample": True, "temperature": temperature, "top_p": top_p})
            else:
                kwargs.update({"do_sample": False})
            return kwargs

        streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=False)
        kwargs = build_generation_kwargs(dict(inputs), streamer, max_new_tokens)

        thinking = True
        if req.enable_thinking is False:
            thinking = False
        if req.chat_template_kwargs and req.chat_template_kwargs.get("enable_thinking") is False:
            thinking = False

        yield sse(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": now(),
                "model": args.served_model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        )

        mode = "reasoning" if thinking else "content"
        pending = ""
        stripped_open_tag = False
        delimiter = "</think>"

        def emit_delta(field: str, text: str) -> bytes | None:
            if not text:
                return None
            return sse(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": now(),
                    "model": args.served_model_name,
                    "choices": [{"index": 0, "delta": {field: text}, "finish_reason": None}],
                }
            )

        def clean_reasoning_prefix(text: str) -> str:
            nonlocal stripped_open_tag
            if stripped_open_tag:
                return text
            stripped_open_tag = True
            if text.startswith("<think>"):
                text = text[len("<think>") :]
            return text.lstrip("\n")

        def consume_stream(active_streamer: TextIteratorStreamer) -> Iterator[bytes]:
            nonlocal mode, pending
            for piece in active_streamer:
                if mode == "content":
                    chunk = emit_delta("content", piece)
                    if chunk:
                        yield chunk
                    continue

                pending += piece
                split_at = pending.find(delimiter)
                if split_at >= 0:
                    before = clean_reasoning_prefix(pending[:split_at])
                    chunk = emit_delta("reasoning", before)
                    if chunk:
                        yield chunk
                    rest = pending[split_at + len(delimiter) :].lstrip("\n")
                    mode = "content"
                    pending = ""
                    chunk = emit_delta("content", rest)
                    if chunk:
                        yield chunk
                    continue

                safe_len = max(0, len(pending) - len(delimiter) + 1)
                if safe_len:
                    before = clean_reasoning_prefix(pending[:safe_len])
                    pending = pending[safe_len:]
                    chunk = emit_delta("reasoning", before)
                    if chunk:
                        yield chunk

        def start_generation(generation_kwargs: dict[str, Any]) -> tuple[threading.Thread, dict[str, torch.Tensor], dict[str, BaseException]]:
            output_holder: dict[str, torch.Tensor] = {}
            error_holder: dict[str, BaseException] = {}

            def run_generate() -> None:
                try:
                    with generation_slots, torch.inference_mode():
                        output_holder["output"] = model.generate(**generation_kwargs)
                except BaseException as exc:  # propagated through stream below
                    error_holder["error"] = exc

            thread = threading.Thread(target=run_generate, daemon=True)
            thread.start()
            return thread, output_holder, error_holder

        def finish_generation(
            thread: threading.Thread,
            output_holder: dict[str, torch.Tensor],
            error_holder: dict[str, BaseException],
        ) -> torch.Tensor:
            thread.join()
            if "error" in error_holder:
                raise RuntimeError(str(error_holder["error"])) from error_holder["error"]
            return output_holder["output"]

        thinking_budget = resolve_thinking_budget(req) if thinking else None
        if thinking_budget is not None:
            close_ids = tokenizer.encode(delimiter, add_special_tokens=False)
            kwargs["stopping_criteria"] = StoppingCriteriaList(
                [CloseThinkOrBudgetCriteria(input_token_count, thinking_budget, close_ids)]
            )

        thread, output_holder, error_holder = start_generation(kwargs)
        yield from consume_stream(streamer)
        output = finish_generation(thread, output_holder, error_holder)

        if thinking_budget is not None:
            generated_len = max(0, int(output.shape[-1] - input_token_count))
            remaining = max(0, max_new_tokens - generated_len)
            phase2_input_ids = output
            if mode == "reasoning":
                if pending:
                    chunk = emit_delta("reasoning", clean_reasoning_prefix(pending))
                    if chunk:
                        yield chunk
                    pending = ""
                close_inputs = tokenizer("\n</think>\n\n", return_tensors="pt", add_special_tokens=False).to(args.device)
                phase2_input_ids = torch.cat([phase2_input_ids, close_inputs["input_ids"]], dim=-1)
                mode = "content"
            phase2_allowed = min(remaining, max(0, args.max_model_len - int(phase2_input_ids.shape[-1])))
            if phase2_allowed > 0:
                phase2_streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=False)
                phase2_inputs = {
                    "input_ids": phase2_input_ids,
                    "attention_mask": torch.ones_like(phase2_input_ids),
                }
                phase2_kwargs = build_generation_kwargs(phase2_inputs, phase2_streamer, phase2_allowed)
                thread, output_holder, error_holder = start_generation(phase2_kwargs)
                yield from consume_stream(phase2_streamer)
                finish_generation(thread, output_holder, error_holder)

        if pending:
            field = "reasoning" if mode == "reasoning" else "content"
            text = clean_reasoning_prefix(pending) if field == "reasoning" else pending
            chunk = emit_delta(field, text)
            if chunk:
                yield chunk

        yield sse(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": now(),
                "model": args.served_model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        if req.stream_options and req.stream_options.get("include_usage"):
            yield sse(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": now(),
                    "model": args.served_model_name,
                    "choices": [],
                    "usage": {"prompt_tokens": input_token_count, "completion_tokens": None, "total_tokens": None},
                }
            )
        yield b"data: [DONE]\n\n"

    @app.post("/v1/chat/completions", response_model=None)
    def chat(req: ChatRequest):
        if req.model != args.served_model_name:
            raise HTTPException(status_code=404, detail=f"unknown model {req.model!r}")
        if req.stream:
            return StreamingResponse(stream_chat(req), media_type="text/event-stream")

        prompt = render_prompt(req)
        inputs = tokenizer(prompt, return_tensors="pt").to(args.device)
        max_new_tokens = req.max_tokens or args.default_max_tokens
        temperature = args.temperature if req.temperature is None else req.temperature
        generation_kwargs: dict[str, Any] = {
            **inputs,
            "max_new_tokens": max_new_tokens,
            "eos_token_id": tokenizer.eos_token_id,
            "pad_token_id": tokenizer.eos_token_id,
        }
        if temperature and temperature > 0:
            generation_kwargs.update({"do_sample": True, "temperature": temperature, "top_p": req.top_p or args.top_p})
        else:
            generation_kwargs.update({"do_sample": False})
        with generation_slots, torch.inference_mode():
            output = model.generate(**generation_kwargs)
        decoded = tokenizer.decode(output[0, inputs["input_ids"].shape[-1] :], skip_special_tokens=False)
        _, content = split_final_text(decoded, req.enable_thinking is not False)
        return JSONResponse(
            {
                "id": "chatcmpl-" + uuid.uuid4().hex,
                "object": "chat.completion",
                "created": now(),
                "model": args.served_model_name,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            }
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", default="/data/xsy/codes/checkpoints/Qwen3.5-9B")
    parser.add_argument("--served-model-name", default="qwen3.5-9b")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-model-len", type=int, default=262144)
    parser.add_argument("--default-max-tokens", type=int, default=int(os.environ.get("QWEN35_LOCAL_DEFAULT_MAX_TOKENS", "8192")))
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument(
        "--max-concurrent-generations",
        type=int,
        default=int(os.environ.get("QWEN35_LOCAL_MAX_CONCURRENT_GENERATIONS", "1")),
    )
    return parser.parse_args()


if __name__ == "__main__":
    parsed = parse_args()
    app = create_app(parsed)
    uvicorn.run(app, host=parsed.host, port=parsed.port, log_level="info")
