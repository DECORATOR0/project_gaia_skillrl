from __future__ import annotations

import csv
import io
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from PIL import Image
from pypdf import PdfReader

from .utils import ensure_dir, ensure_preferred_proxy_env, read_text


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


class Toolbox:
    def __init__(self, context: ToolContext):
        ensure_preferred_proxy_env()
        self.context = context
        self._registry: dict[str, ToolSpec] = {}
        self.active_skill_dir: Path | None = None
        self.active_task_data_dir: Path | None = None
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
