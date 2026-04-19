from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from xml.etree import ElementTree as ET

import pandas as pd
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from PIL import Image
from pypdf import PdfReader

from .utils import ensure_dir, ensure_preferred_proxy_env, read_text

_DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}

_PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

_HTTP_RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_WHISPER_LANGUAGE_ALIASES = {
    "english": "en",
    "en": "en",
    "chinese": "zh",
    "zh": "zh",
    "spanish": "es",
    "es": "es",
    "french": "fr",
    "fr": "fr",
    "german": "de",
    "de": "de",
    "japanese": "ja",
    "ja": "ja",
    "korean": "ko",
    "ko": "ko",
    "portuguese": "pt",
    "pt": "pt",
    "russian": "ru",
    "ru": "ru",
}


def _join_non_empty(parts: list[str], *, separator: str = "\n") -> str:
    return separator.join(part for part in parts if part.strip())


def _xml_attr_value(element: ET.Element, local_name: str) -> str:
    for key, value in element.attrib.items():
        if key.rsplit("}", maxsplit=1)[-1] == local_name:
            return value
    return ""


def _word_paragraph_text(paragraph: ET.Element) -> str:
    chunks = []
    for node in paragraph.findall(".//w:t", _DOCX_NS):
        if node.text:
            chunks.append(node.text)
    return "".join(chunks).strip()


def _word_table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("./w:tr", _DOCX_NS):
        cells: list[str] = []
        for cell in row.findall("./w:tc", _DOCX_NS):
            paragraphs = [_word_paragraph_text(node) for node in cell.findall(".//w:p", _DOCX_NS)]
            cells.append(" ".join(part for part in paragraphs if part.strip()).strip())
        if any(cell.strip() for cell in cells):
            rows.append(cells)
    return rows


def _ppt_text(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    chunks = [node.text or "" for node in root.findall(".//a:t", _PPT_NS)]
    return _join_non_empty([chunk.strip() for chunk in chunks], separator="\n")


def _safe_child_path(root: Path, relative_name: str) -> Path:
    destination = (root / relative_name).resolve()
    root_resolved = root.resolve()
    if os.path.commonpath([str(root_resolved), str(destination)]) != str(root_resolved):
        raise ValueError(f"Archive member escapes target directory: {relative_name}")
    return destination


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    for member in archive.infolist():
        target = _safe_child_path(destination, member.filename)
        if member.is_dir():
            ensure_dir(target)
            continue
        ensure_dir(target.parent)
        with archive.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    for member in archive.getmembers():
        target = _safe_child_path(destination, member.name)
        if member.isdir():
            ensure_dir(target)
            continue
        if not member.isfile():
            continue
        ensure_dir(target.parent)
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        with extracted, target.open("wb") as dst:
            shutil.copyfileobj(extracted, dst)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    callable: Callable[..., Any]
    source: str = "built_in"

    def prompt_entry(self) -> str:
        payload = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "source": self.source,
        }
        return json.dumps(payload, ensure_ascii=False)


@dataclass
class ToolContext:
    workspace_root: Path
    skill_library_root: Path
    temp_root: Path
    python_executable: str
    shell_program: str
    search_results_limit: int = 5
    web_fetch_char_limit: int = 6000
    tool_api_base_url: str = ""
    tool_api_key: str = ""
    tool_api_timeout_seconds: int = 180
    tool_vision_model: str = "gpt-4o-mini"
    tool_audio_model: str = "gpt-4o-mini-transcribe"
    tool_api_max_retries: int = 2


class Toolbox:
    def __init__(self, context: ToolContext):
        ensure_preferred_proxy_env()
        self.context = context
        self._registry: dict[str, ToolSpec] = {}
        self.active_skill_dir: Path | None = None
        self.active_task_data_dir: Path | None = None
        self._whisper_model: Any | None = None
        ensure_dir(context.temp_root)
        self._register_builtin_tools()

    def set_active_skill_dir(self, path: str | None) -> None:
        self.active_skill_dir = Path(path).resolve() if path else None

    def set_active_task_data_dir(self, path: str | None) -> None:
        self.active_task_data_dir = Path(path).resolve() if path else None

    def _register(self, spec: ToolSpec) -> None:
        self._registry[spec.name] = spec

    def _resolve_workspace_path(self, path: str, *, prefer_existing: bool = True) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        roots: list[Path] = []
        if self.active_task_data_dir is not None:
            roots.append(self.active_task_data_dir)
        if self.active_skill_dir is not None:
            roots.append(self.active_skill_dir)
        roots.append(self.context.workspace_root)
        for root in roots:
            resolved = (root / candidate).resolve()
            if not prefer_existing or resolved.exists():
                return resolved
        return (roots[0] / candidate).resolve()

    def _register_builtin_tools(self) -> None:
        self._register(
            ToolSpec(
                name="list_dir",
                description="List files under the active task directory or an explicit relative path.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
                callable=self.list_dir,
            )
        )
        self._register(
            ToolSpec(
                name="read_file",
                description="Read a UTF-8 text file such as txt, md, json, html, csv, or task manifests.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                callable=self.read_file,
            )
        )
        self._register(
            ToolSpec(
                name="read_json_file",
                description="Read a JSON file and return the parsed object.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                callable=self.read_json_file,
            )
        )
        self._register(
            ToolSpec(
                name="extract_pdf_text",
                description="Extract text from a PDF attachment.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "max_pages": {"type": "integer"}},
                    "required": ["path"],
                },
                callable=self.extract_pdf_text,
            )
        )
        self._register(
            ToolSpec(
                name="read_table",
                description="Read CSV or XLSX files and return a compact preview.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "max_rows": {"type": "integer"}},
                    "required": ["path"],
                },
                callable=self.read_table,
            )
        )
        self._register(
            ToolSpec(
                name="image_metadata",
                description="Return size and mode for a local image.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                callable=self.image_metadata,
            )
        )
        self._register(
            ToolSpec(
                name="audio_transcribe",
                description="Transcribe a local audio attachment. Uses local whisper when installed and falls back to the configured OpenAI-compatible API.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                        "return_segments": {"type": "boolean"},
                    },
                    "required": ["path"],
                },
                callable=self.audio_transcribe,
            )
        )
        self._register(
            ToolSpec(
                name="ocr_image",
                description="Extract text from a local image. Uses local OCR when available and falls back to a vision model through the configured OpenAI-compatible API.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                        "return_bboxes": {"type": "boolean"},
                    },
                    "required": ["path"],
                },
                callable=self.ocr_image,
            )
        )
        self._register(
            ToolSpec(
                name="image_qa",
                description="Ask a question about a local image through the configured OpenAI-compatible multimodal model.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "question": {"type": "string"},
                    },
                    "required": ["path", "question"],
                },
                callable=self.image_qa,
            )
        )
        self._register(
            ToolSpec(
                name="parse_docx",
                description="Parse a DOCX document and return extracted text, headings, and table previews.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                callable=self.parse_docx,
            )
        )
        self._register(
            ToolSpec(
                name="parse_pptx",
                description="Parse a PPTX file and return slide text, notes, and image counts.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                callable=self.parse_pptx,
            )
        )
        self._register(
            ToolSpec(
                name="extract_archive",
                description="Extract a local archive such as zip or tar and return the output directory plus a compact file list.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "list_limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
                callable=self.extract_archive,
            )
        )
        self._register(
            ToolSpec(
                name="web_search",
                description="Run a lightweight web search and return top results.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    "required": ["query"],
                },
                callable=self.web_search,
            )
        )
        self._register(
            ToolSpec(
                name="fetch_url",
                description="Fetch a web page and return title plus cleaned text excerpt.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer"}},
                    "required": ["url"],
                },
                callable=self.fetch_url,
            )
        )
        self._register(
            ToolSpec(
                name="html_extract",
                description="Extract readable text and links from a URL or a local HTML file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer"},
                    },
                    "required": [],
                },
                callable=self.html_extract,
            )
        )
        self._register(
            ToolSpec(
                name="run_python",
                description="Run a short Python snippet for arithmetic or structured parsing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "input_json": {},
                    },
                    "required": ["code"],
                },
                callable=self.run_python,
            )
        )

    def specs(self, allowed_tools: list[str] | None = None) -> list[ToolSpec]:
        if not allowed_tools:
            return [self._registry[name] for name in sorted(self._registry)]
        allowed = set(allowed_tools)
        return [spec for name, spec in sorted(self._registry.items()) if name in allowed]

    def tool_prompt(self, allowed_tools: list[str] | None = None) -> str:
        return "\n".join(spec.prompt_entry() for spec in self.specs(allowed_tools))

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._registry:
            raise KeyError(f"Unknown tool: {tool_name}")
        return self._registry[tool_name].callable(**arguments)

    def list_dir(self, path: str = ".") -> list[str]:
        target = self._resolve_workspace_path(path)
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")
        if target.is_file():
            return [target.name]
        return sorted(entry.name for entry in target.iterdir())

    def read_file(self, path: str) -> str:
        return read_text(self._resolve_workspace_path(path))

    def read_json_file(self, path: str) -> Any:
        return json.loads(read_text(self._resolve_workspace_path(path)))

    def extract_pdf_text(self, path: str, max_pages: int = 10) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        reader = PdfReader(str(target))
        texts: list[str] = []
        page_count = min(len(reader.pages), max_pages)
        for idx in range(page_count):
            texts.append(reader.pages[idx].extract_text() or "")
        return {
            "path": str(target),
            "page_count": len(reader.pages),
            "pages_read": page_count,
            "text": "\n\n".join(texts).strip(),
        }

    def read_table(self, path: str, max_rows: int = 20) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        suffix = target.suffix.lower()
        if suffix == ".csv":
            frame = pd.read_csv(target)
        elif suffix in {".xlsx", ".xlsm", ".xls"}:
            frame = pd.read_excel(target)
        else:
            raise ValueError(f"Unsupported table format: {suffix}")
        preview = frame.head(max_rows).to_dict(orient="records")
        return {
            "path": str(target),
            "columns": list(frame.columns),
            "row_count": int(frame.shape[0]),
            "preview": preview,
        }

    def image_metadata(self, path: str) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        with Image.open(target) as image:
            return {
                "path": str(target),
                "format": image.format,
                "mode": image.mode,
                "width": image.width,
                "height": image.height,
            }

    def _tool_api_url(self, endpoint: str) -> str:
        base_url = self.context.tool_api_base_url.strip().rstrip("/")
        if not base_url:
            raise RuntimeError("No OpenAI-compatible tool API base URL is configured.")
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return base_url + "/" + endpoint.lstrip("/")

    def _tool_api_headers(self, *, include_json_content_type: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_json_content_type:
            headers["Content-Type"] = "application/json"
        if self.context.tool_api_key.strip():
            headers["Authorization"] = f"Bearer {self.context.tool_api_key.strip()}"
        return headers

    def _tool_api_request(
        self,
        method: str,
        endpoint: str,
        *,
        json_payload: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> requests.Response:
        url = self._tool_api_url(endpoint)
        headers = self._tool_api_headers(include_json_content_type=json_payload is not None)
        max_retries = max(self.context.tool_api_max_retries, 0)
        request_timeout = timeout or self.context.tool_api_timeout_seconds
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_payload,
                    data=data,
                    files=files,
                    timeout=request_timeout,
                )
                if response.status_code in _HTTP_RETRY_STATUS_CODES and attempt < max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))
        if isinstance(last_error, requests.HTTPError) and last_error.response is not None:
            body = last_error.response.text[:2000]
            raise RuntimeError(f"Tool API request failed with status {last_error.response.status_code}: {body}") from last_error
        raise RuntimeError(f"Tool API request failed: {last_error}") from last_error

    def _tool_model_name(self, kind: str) -> str:
        if kind == "audio":
            return (os.environ.get("NLRL_TOOL_AUDIO_MODEL", "").strip()
                or os.environ.get("NLRL_TOOL_MODEL", "").strip()
                or self.context.tool_audio_model)
        return (os.environ.get("NLRL_TOOL_VISION_MODEL", "").strip()
            or os.environ.get("NLRL_TOOL_MODEL", "").strip()
            or self.context.tool_vision_model)

    def _image_data_url(self, target: Path) -> str:
        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        payload = base64.b64encode(target.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{payload}"

    def _normalize_whisper_language(self, language: str | None) -> str | None:
        if not language:
            return None
        cleaned = language.strip().lower()
        if not cleaned:
            return None
        return _WHISPER_LANGUAGE_ALIASES.get(cleaned, cleaned if len(cleaned) <= 3 else None)

    def _extract_chat_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices", [])
        if not choices:
            raise RuntimeError(f"Empty chat completion response: {json.dumps(payload, ensure_ascii=False)[:1200]}")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            cleaned = content.strip()
            return re.sub(r"(?is)^```(?:\w+)?\s*|\s*```$", "", cleaned).strip()
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
            cleaned = "".join(fragments).strip()
            return re.sub(r"(?is)^```(?:\w+)?\s*|\s*```$", "", cleaned).strip()
        cleaned = str(content).strip()
        return re.sub(r"(?is)^```(?:\w+)?\s*|\s*```$", "", cleaned).strip()

    def _vision_chat(self, prompt: str, image_path: Path, *, max_tokens: int = 400) -> tuple[str, str]:
        payload = {
            "model": self._tool_model_name("vision"),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": self._image_data_url(image_path)}},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        response = self._tool_api_request("POST", "/chat/completions", json_payload=payload)
        raw_payload = response.json()
        return self._extract_chat_text(raw_payload), payload["model"]

    def _ocr_image_local(self, target: Path, language: str | None) -> tuple[str | None, str]:
        try:
            import pytesseract
        except Exception as exc:
            return None, f"Local OCR unavailable: {type(exc).__name__}: {exc}"
        try:
            with Image.open(target) as image:
                kwargs: dict[str, Any] = {}
                if language:
                    kwargs["lang"] = language
                text = pytesseract.image_to_string(image, **kwargs)
        except Exception as exc:
            return None, f"Local OCR failed: {type(exc).__name__}: {exc}"
        cleaned = text.strip()
        if not cleaned:
            return "", "Local OCR produced no readable text."
        return cleaned, ""

    def ocr_image(self, path: str, language: str | None = None, return_bboxes: bool = False) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        warnings: list[str] = []
        local_text, local_warning = self._ocr_image_local(target, language)
        if local_text is not None and local_text:
            if return_bboxes:
                warnings.append("Bounding boxes are unavailable for the local OCR path in this build.")
            return {
                "ok": True,
                "path": str(target),
                "content": local_text,
                "metadata": {"method": "pytesseract", "language": language or ""},
                "warnings": warnings,
            }
        if local_warning:
            warnings.append(local_warning)
        prompt = (
            "Extract every readable text fragment from this image. Preserve reading order and line breaks. "
            "Return only the extracted text with no commentary. If there is no readable text, return an empty string."
        )
        if language:
            prompt += f" The expected language is {language}."
        text, model_name = self._vision_chat(prompt, target, max_tokens=500)
        if return_bboxes:
            warnings.append("Bounding boxes are unavailable for the OpenAI-compatible OCR fallback.")
        return {
            "ok": True,
            "path": str(target),
            "content": text.strip(),
            "metadata": {"method": "openai_compatible_api", "model": model_name, "language": language or ""},
            "warnings": warnings,
        }

    def image_qa(self, path: str, question: str) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        prompt = (
            "Answer the question using only evidence visible in the image. "
            "Keep the answer short and do not add explanations.\n"
            f"Question: {question}"
        )
        text, model_name = self._vision_chat(prompt, target, max_tokens=300)
        return {
            "ok": True,
            "path": str(target),
            "question": question,
            "content": text.strip(),
            "metadata": {"method": "openai_compatible_api", "model": model_name},
            "warnings": [],
        }

    def _whisper_model_instance(self) -> Any:
        if self._whisper_model is not None:
            return self._whisper_model
        import faster_whisper

        model_name = os.environ.get("NLRL_TOOL_WHISPER_MODEL", "").strip() or "base"
        device = os.environ.get("NLRL_TOOL_WHISPER_DEVICE", "").strip() or "auto"
        compute_type = os.environ.get("NLRL_TOOL_WHISPER_COMPUTE_TYPE", "").strip() or "auto"
        self._whisper_model = faster_whisper.WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
        return self._whisper_model

    def _audio_transcribe_local(
        self,
        target: Path,
        language: str | None,
        return_segments: bool,
    ) -> tuple[dict[str, Any] | None, str]:
        normalized_language = self._normalize_whisper_language(language)
        try:
            model = self._whisper_model_instance()
        except Exception as exc:
            return None, f"Local transcription unavailable: {type(exc).__name__}: {exc}"
        try:
            segments, info = model.transcribe(str(target), language=normalized_language, vad_filter=True)
            segment_items = []
            collected_text: list[str] = []
            for segment in segments:
                piece = segment.text.strip()
                if not piece:
                    continue
                collected_text.append(piece)
                if return_segments:
                    segment_items.append(
                        {
                            "start": round(float(segment.start), 3),
                            "end": round(float(segment.end), 3),
                            "text": piece,
                        }
                    )
            transcript = " ".join(collected_text).strip()
        except Exception as exc:
            return None, f"Local transcription failed: {type(exc).__name__}: {exc}"
        return {
            "ok": True,
            "path": str(target),
            "content": transcript,
            "metadata": {
                "method": "faster_whisper",
                "language": getattr(info, "language", "") or "",
                "duration_seconds": getattr(info, "duration", None),
                "segments": segment_items if return_segments else [],
            },
            "warnings": [],
        }, ""

    def _audio_transcribe_api(self, target: Path, language: str | None, return_segments: bool) -> dict[str, Any]:
        model_name = self._tool_model_name("audio")
        normalized_language = self._normalize_whisper_language(language)
        with target.open("rb") as handle:
            response = self._tool_api_request(
                "POST",
                "/audio/transcriptions",
                data={
                    "model": model_name,
                    "response_format": "verbose_json" if return_segments else "json",
                    **({"language": normalized_language} if normalized_language else {}),
                },
                files={
                    "file": (
                        target.name,
                        handle,
                        mimetypes.guess_type(str(target))[0] or "application/octet-stream",
                    )
                },
                timeout=max(self.context.tool_api_timeout_seconds, 300),
            )
        payload = response.json()
        transcript = str(payload.get("text", "")).strip()
        metadata = {
            "method": "openai_compatible_api",
            "model": model_name,
            "language": str(payload.get("language", "")).strip(),
            "duration_seconds": payload.get("duration"),
            "segments": payload.get("segments", []) if return_segments else [],
        }
        return {
            "ok": True,
            "path": str(target),
            "content": transcript,
            "metadata": metadata,
            "warnings": [],
        }

    def audio_transcribe(self, path: str, language: str | None = None, return_segments: bool = False) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        warnings: list[str] = []
        local_result, local_warning = self._audio_transcribe_local(target, language, return_segments)
        if local_result is not None and local_result.get("content"):
            return local_result
        if local_warning:
            warnings.append(local_warning)
        api_result = self._audio_transcribe_api(target, language, return_segments)
        api_result["warnings"] = [*warnings, *api_result.get("warnings", [])]
        return api_result

    def parse_docx(self, path: str) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        warnings: list[str] = []
        with zipfile.ZipFile(target) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ValueError(f"DOCX package is missing word/document.xml: {target}")
            root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find("./w:body", _DOCX_NS)
        if body is None:
            raise ValueError(f"DOCX document body is empty: {target}")
        paragraph_texts: list[str] = []
        headings: list[str] = []
        table_text_blocks: list[str] = []
        table_summaries: list[dict[str, Any]] = []
        paragraph_tag = f"{{{_DOCX_NS['w']}}}p"
        table_tag = f"{{{_DOCX_NS['w']}}}tbl"
        for child in body:
            if child.tag == paragraph_tag:
                text = _word_paragraph_text(child)
                if not text:
                    continue
                paragraph_texts.append(text)
                style_node = child.find("./w:pPr/w:pStyle", _DOCX_NS)
                style_name = _xml_attr_value(style_node, "val").lower() if style_node is not None else ""
                if style_name.startswith("heading"):
                    headings.append(text)
                continue
            if child.tag == table_tag:
                rows = _word_table_rows(child)
                if rows:
                    table_text_blocks.append("\n".join(" | ".join(cell for cell in row if cell) for row in rows))
                    table_summaries.append(
                        {
                            "row_count": len(rows),
                            "column_count": max((len(row) for row in rows), default=0),
                            "preview": rows[:5],
                        }
                    )
        return {
            "ok": True,
            "path": str(target),
            "content": _join_non_empty([*paragraph_texts, *table_text_blocks]),
            "metadata": {
                "headings": headings[:20],
                "paragraph_count": len(paragraph_texts),
                "table_count": len(table_summaries),
                "tables": table_summaries[:10],
            },
            "warnings": warnings,
        }

    def parse_pptx(self, path: str) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        slides: list[dict[str, Any]] = []
        with zipfile.ZipFile(target) as archive:
            slide_paths = sorted(
                (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
                key=lambda item: int("".join(ch for ch in Path(item).stem if ch.isdigit()) or "0"),
            )
            for index, slide_path in enumerate(slide_paths, start=1):
                slide_text = _ppt_text(archive.read(slide_path))
                notes_text = ""
                image_count = 0
                rel_path = f"ppt/slides/_rels/{Path(slide_path).name}.rels"
                if rel_path in archive.namelist():
                    rel_root = ET.fromstring(archive.read(rel_path))
                    for relationship in rel_root.findall("./rel:Relationship", _PPT_NS):
                        rel_type = relationship.attrib.get("Type", "")
                        rel_target = relationship.attrib.get("Target", "")
                        if rel_type.endswith("/image"):
                            image_count += 1
                            continue
                        if rel_type.endswith("/notesSlide") and rel_target:
                            notes_path = posixpath.normpath(posixpath.join(posixpath.dirname(rel_path), rel_target))
                            if notes_path in archive.namelist():
                                notes_text = _ppt_text(archive.read(notes_path))
                slides.append(
                    {
                        "slide_number": index,
                        "text": slide_text,
                        "notes": notes_text,
                        "image_count": image_count,
                    }
                )
        content_blocks = []
        for slide in slides:
            block = [f"Slide {slide['slide_number']}"]
            if slide["text"]:
                block.append(slide["text"])
            if slide["notes"]:
                block.append(f"Notes:\n{slide['notes']}")
            content_blocks.append("\n".join(block))
        return {
            "ok": True,
            "path": str(target),
            "content": "\n\n".join(content_blocks).strip(),
            "metadata": {"slide_count": len(slides), "slides": slides},
            "warnings": [],
        }

    def extract_archive(self, path: str, output_dir: str | None = None, list_limit: int = 100) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        destination = (
            self._resolve_workspace_path(output_dir, prefer_existing=False)
            if output_dir
            else ensure_dir(self.context.temp_root / "archives" / target.stem)
        )
        if destination.exists() and not output_dir:
            shutil.rmtree(destination)
        ensure_dir(destination)
        suffixes = [suffix.lower() for suffix in target.suffixes]
        if target.suffix.lower() == ".zip":
            with zipfile.ZipFile(target) as archive:
                _safe_extract_zip(archive, destination)
        elif suffixes[-2:] == [".tar", ".gz"] or suffixes[-2:] == [".tar", ".bz2"] or suffixes[-2:] == [".tar", ".xz"] or target.suffix.lower() == ".tar":
            with tarfile.open(target) as archive:
                _safe_extract_tar(archive, destination)
        else:
            try:
                shutil.unpack_archive(str(target), str(destination))
            except shutil.ReadError as exc:
                raise ValueError(f"Unsupported archive format: {target.suffix}") from exc
        file_paths = [
            str(item.relative_to(destination))
            for item in sorted(destination.rglob("*"))
            if item.is_file()
        ]
        return {
            "ok": True,
            "path": str(target),
            "content": "\n".join(file_paths[:list_limit]),
            "metadata": {
                "output_dir": str(destination),
                "file_count": len(file_paths),
                "files": file_paths[:list_limit],
            },
            "warnings": [],
        }

    def web_search(self, query: str, max_results: int | None = None) -> list[dict[str, Any]]:
        limit = max_results or self.context.search_results_limit
        results: list[dict[str, Any]] = []
        try:
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=limit):
                    results.append(
                        {
                            "title": str(item.get("title", "")),
                            "href": self._normalize_search_href(str(item.get("href", ""))),
                            "body": str(item.get("body", "")),
                        }
                    )
        except Exception:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            for anchor in soup.select("a.result__a"):
                snippet_node = anchor.find_parent("div", class_="result")
                snippet = ""
                if snippet_node is not None:
                    body_node = snippet_node.select_one(".result__snippet")
                    if body_node is not None:
                        snippet = body_node.get_text(" ", strip=True)
                results.append(
                    {
                        "title": anchor.get_text(" ", strip=True),
                        "href": self._normalize_search_href(anchor.get("href", "")),
                        "body": snippet,
                    }
                )
                if len(results) >= limit:
                    break
        return results

    @staticmethod
    def _normalize_search_href(href: str) -> str:
        if not href:
            return href
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        uddg = query.get("uddg")
        if uddg:
            return unquote(uddg[0])
        if href.startswith("//"):
            return "https:" + href
        return href

    def fetch_url(self, url: str, max_chars: int | None = None) -> dict[str, Any]:
        limit = max_chars or self.context.web_fetch_char_limit
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        text = response.text
        title = ""
        if "html" in content_type:
            soup = BeautifulSoup(text, "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            text = soup.get_text("\n", strip=True)
        return {
            "url": url,
            "status_code": response.status_code,
            "content_type": content_type,
            "title": title,
            "text": text[:limit],
        }

    def html_extract(self, url: str | None = None, path: str | None = None, max_chars: int | None = None) -> dict[str, Any]:
        if not url and not path:
            raise ValueError("Provide either `url` or `path`.")
        limit = max_chars or self.context.web_fetch_char_limit
        warnings: list[str] = []
        content_type = ""
        resolved_path = ""
        title = ""
        links: list[dict[str, str]] = []
        if url:
            response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            html = response.text
        else:
            target = self._resolve_workspace_path(path or "")
            resolved_path = str(target)
            content_type = mimetypes.guess_type(str(target))[0] or "text/html"
            html = target.read_text(encoding="utf-8", errors="replace")
        extracted = ""
        try:
            import trafilatura

            extracted = trafilatura.extract(html, include_links=False, include_images=False) or ""
        except Exception as exc:
            warnings.append(f"Trafilatura unavailable: {type(exc).__name__}: {exc}")
        soup = BeautifulSoup(html, "lxml")
        if soup.title:
            title = soup.title.get_text(" ", strip=True)
        if not extracted:
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            extracted = soup.get_text("\n", strip=True)
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", "")).strip()
            text = anchor.get_text(" ", strip=True)
            if not href:
                continue
            links.append({"text": text[:160], "href": href})
            if len(links) >= 20:
                break
        return {
            "ok": True,
            "path": resolved_path,
            "url": url or "",
            "content": extracted[:limit],
            "metadata": {
                "title": title,
                "content_type": content_type,
                "link_count": len(links),
                "links": links,
            },
            "warnings": warnings,
        }

    def run_python(self, code: str, input_json: Any | None = None) -> dict[str, Any]:
        ensure_dir(self.context.temp_root)
        with tempfile.TemporaryDirectory(dir=self.context.temp_root) as tmpdir:
            script_path = Path(tmpdir) / "tool_snippet.py"
            script_path.write_text(code, encoding="utf-8")
            stdin_payload = json.dumps(input_json, ensure_ascii=False) if input_json is not None else ""
            completed = subprocess.run(
                [self.context.python_executable, str(script_path)],
                input=stdin_payload,
                text=True,
                capture_output=True,
                cwd=self.context.workspace_root,
                check=False,
            )
            payload: dict[str, Any] = {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-6000:],
                "stderr": completed.stderr[-4000:],
            }
            if completed.stdout.strip():
                try:
                    payload["stdout_json"] = json.loads(completed.stdout)
                except Exception:
                    pass
            return payload
