from __future__ import annotations

import argparse
import getpass
import html
import html.parser
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

APP_NAME = "nvidia-chat-cli"
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_VALIDATE_MODEL = "meta/llama-3.1-8b-instruct"
DEFAULT_FOLDER_MAX_FILES = 2000
DEFAULT_FOLDER_MAX_FILE_CHARS = 6000
DEFAULT_FOLDER_TREE_ENTRIES = 2000
DEFAULT_FOLDER_SMART_FILES = 20
CONSOLE = Console()

POPULAR_MODELS = [
    "meta/llama-3.3-70b-instruct",
    "meta/llama-4-maverick-17b-128e-instruct",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-pro",
    "qwen/qwen3-coder-480b-a35b-instruct",
    "qwen/qwen3-next-80b-a3b-instruct",
    "openai/gpt-oss-120b",
    "mistralai/mistral-large-2-instruct",
]

CATEGORY_ORDER = [
    "Popular",
    "General Chat",
    "Code",
    "Reasoning",
    "Vision and Multimodal",
    "Embeddings and Retrieval",
    "Safety, Moderation, and Reward",
    "Translation and Speech",
    "Domain Specific",
    "Other",
]

ALL_MODELS_LABEL = "All Models"
CHAT_CATEGORY_ORDER = [
    "Popular",
    "General Chat",
    "Code",
    "Reasoning",
    "Vision and Multimodal",
    "Translation and Speech",
    "Domain Specific",
]
NON_CHAT_CATEGORY_HINT = "All API Models"
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
}
TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".env",
    ".example",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    base_url: str
    default_model: str | None
    validate_model: str


@dataclass(frozen=True)
class ChatReply:
    content: str
    thinking: str | None = None
    streamed: bool = False


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str
    content: str = ""


@dataclass(frozen=True)
class FolderContext:
    root: Path
    tree: str
    files: list[tuple[str, str]]
    file_paths: list[str] = field(default_factory=list)
    mode: str = "smart"
    skipped: int = 0
    skipped_by_limit: int = 0
    skipped_reasons: dict[str, int] | None = None


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        self.status = status
        super().__init__(message)


def config_dir() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def config_file() -> Path:
    return config_dir() / "config.env"


def local_env_file() -> Path:
    return Path.cwd() / ".env"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings() -> Settings:
    config_values = parse_env_file(config_file())
    local_values = parse_env_file(local_env_file())

    def pick(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name) or local_values.get(name) or config_values.get(name) or default

    api_key = (
        local_values.get("NVIDIA_API_KEY")
        or local_values.get("NVIDIA_NIM_API_KEY")
        or config_values.get("NVIDIA_API_KEY")
        or config_values.get("NVIDIA_NIM_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NVIDIA_NIM_API_KEY")
    )
    return Settings(
        api_key=api_key,
        base_url=(pick("NVIDIA_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/"),
        default_model=pick("NVIDIA_MODEL"),
        validate_model=pick("NVIDIA_VALIDATE_MODEL", DEFAULT_VALIDATE_MODEL) or DEFAULT_VALIDATE_MODEL,
    )


def save_api_key(api_key: str) -> None:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    current = parse_env_file(path)
    current["NVIDIA_API_KEY"] = api_key

    lines = [f"{key}={value}" for key, value in sorted(current.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def request_json(
    method: str,
    url: str,
    *,
    api_key: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ApiError(exc.code, extract_error_message(details) or details or exc.reason) from exc
    except urllib.error.URLError as exc:
        raise ApiError(None, str(exc.reason)) from exc
    except json.JSONDecodeError as exc:
        raise ApiError(None, f"Invalid JSON response: {exc}") from exc


def request_sse(
    url: str,
    *,
    api_key: str | None,
    payload: dict[str, Any],
    timeout: int = 60,
):
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue

                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break

                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ApiError(exc.code, extract_error_message(details) or details or exc.reason) from exc
    except urllib.error.URLError as exc:
        raise ApiError(None, str(exc.reason)) from exc


def extract_error_message(raw: str) -> str | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    detail = payload.get("detail") or payload.get("message") or payload.get("error")
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    if detail:
        return str(detail)
    return None


def fetch_models(settings: Settings) -> list[str]:
    payload = request_json("GET", f"{settings.base_url}/models", api_key=settings.api_key)
    models = [item["id"] for item in payload.get("data", []) if item.get("id")]
    return sorted(set(models))


def validate_api_key(settings: Settings, api_key: str) -> bool:
    payload = {
        "model": settings.validate_model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "temperature": 0,
        "max_tokens": 4,
    }
    request_json(
        "POST",
        f"{settings.base_url}/chat/completions",
        api_key=api_key,
        payload=payload,
        timeout=45,
    )
    return True


def ensure_api_key(settings: Settings) -> Settings:
    if settings.api_key:
        return settings

    if not sys.stdin.isatty():
        raise SystemExit("NVIDIA_API_KEY is missing or invalid. Run interactively to save a key.")

    while True:
        api_key = getpass.getpass("NVIDIA API key: ").strip()
        if not api_key:
            print("API key cannot be empty.", file=sys.stderr)
            continue
        try:
            with CONSOLE.status("Validating NVIDIA API key...", spinner="dots"):
                validate_api_key(settings, api_key)
        except ApiError as exc:
            CONSOLE.print(Panel(str(exc), title="Key validation failed", border_style="red"))
            continue

        save_api_key(api_key)
        CONSOLE.print(Panel(f"Saved to [bold]{config_file()}[/bold]", title="API key verified", border_style="green"))
        return Settings(api_key, settings.base_url, settings.default_model, settings.validate_model)


def should_skip_path(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)


def is_probably_text_file(path: Path) -> bool:
    if path.name in {".env", ".gitignore", "Dockerfile", "Makefile"}:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def safe_read_text(path: Path, *, max_chars: int) -> str | None:
    byte_limit = max(4096, max_chars * 4)
    try:
        with path.open("rb") as file:
            raw = file.read(byte_limit + 1)
    except OSError:
        return None

    if b"\x00" in raw[:4096]:
        return None

    truncated = len(raw) > byte_limit
    text = raw[:byte_limit].decode("utf-8", errors="replace")
    if truncated or len(text) > max_chars:
        return f"{text[:max_chars].rstrip()}\n\n[truncated]"
    return text


def folder_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=CONSOLE,
        transient=True,
    )


def discover_folder_paths(root: Path) -> list[Path]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=CONSOLE,
        transient=True,
    ) as progress:
        progress.add_task(f"Scanning {root}", total=None)
        return sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)))


def skip_reason(path: Path) -> str | None:
    return next((part for part in path.parts if part in IGNORED_DIRS), None)


def format_skip_reasons(context: FolderContext) -> str:
    parts: list[str] = []
    if context.skipped_by_limit:
        parts.append(f"tree limit: {context.skipped_by_limit}")
    if context.skipped_reasons:
        for reason, count in sorted(context.skipped_reasons.items(), key=lambda item: item[1], reverse=True)[:3]:
            parts.append(f"{reason}: {count}")
    return ", ".join(parts) or "-"


def build_folder_tree(root: Path, paths: list[Path], *, max_entries: int) -> tuple[str, int, int, dict[str, int]]:
    lines: list[str] = []
    skipped_by_limit = 0
    skipped_reasons: Counter[str] = Counter()

    for path in paths:
        relative = path.relative_to(root)
        reason = skip_reason(relative)
        if reason:
            skipped_reasons[reason] += 1
            continue
        if len(lines) >= max_entries:
            skipped_by_limit += 1
            continue

        depth = len(relative.parts) - 1
        suffix = "/" if path.is_dir() else ""
        lines.append(f"{'  ' * depth}- {relative.name}{suffix}")

    return "\n".join(lines), skipped_by_limit + sum(skipped_reasons.values()), skipped_by_limit, dict(skipped_reasons)


def load_folder_context(
    folder: str,
    *,
    max_files: int,
    max_file_chars: int,
    max_tree_entries: int,
    mode: str,
) -> FolderContext:
    root = Path(folder).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Folder not found: {root}")
    if not root.is_dir():
        raise SystemExit(f"Not a folder: {root}")

    files: list[tuple[str, str]] = []
    file_paths: list[str] = []

    paths = discover_folder_paths(root)
    tree, skipped, skipped_by_limit, skipped_reasons = build_folder_tree(root, paths, max_entries=max_tree_entries)

    with folder_progress() as progress:
        description = "Indexing folder context" if mode == "smart" else "Loading folder context"
        task = progress.add_task(f"{description} from {root}", total=len(paths))
        for path in paths:
            progress.advance(task)
            relative = path.relative_to(root)
            if should_skip_path(relative) or not path.is_file() or not is_probably_text_file(path):
                continue
            if len(file_paths) >= max_files:
                continue
            relative_name = str(relative)
            file_paths.append(relative_name)
            if mode == "all":
                text = safe_read_text(path, max_chars=max_file_chars)
                if text is None:
                    continue
                files.append((relative_name, text))

    return FolderContext(
        root=root,
        tree=tree,
        files=files,
        file_paths=file_paths,
        mode=mode,
        skipped=skipped,
        skipped_by_limit=skipped_by_limit,
        skipped_reasons=skipped_reasons,
    )


def folder_context_message(contexts: list[FolderContext]) -> dict[str, str]:
    sections = [
        "The user attached folder context. Use it to understand, review, explain, or summarize the project.",
        "Refer to file paths when discussing specific code or documents.",
        "If the attached context is insufficient, say which files or details are missing.",
        "",
    ]

    for context in contexts:
        sections.append(f"## Folder: {context.root}")
        sections.append("")
        sections.append("### Tree")
        sections.append(context.tree or "[empty tree]")
        if context.skipped:
            sections.append(f"\n[Skipped {context.skipped} tree entries: {format_skip_reasons(context)}.]")
        sections.append("")
        sections.append("### Files")
        for relative, text in context.files:
            sections.append(f"\n#### {relative}\n```text\n{text}\n```")
        sections.append("")

    return {"role": "system", "content": "\n".join(sections).strip()}


def file_selection_messages(
    conversation: list[dict[str, str]],
    prompt: str,
    context: FolderContext,
    *,
    max_files: int,
) -> list[dict[str, str]]:
    recent = conversation[-6:]
    file_list = "\n".join(f"- {path}" for path in context.file_paths) or "[no candidate text files]"
    content = f"""
You choose which local files are relevant for answering the user's next request.
Return only compact JSON in this exact shape:
{{"files":["relative/path.ext"]}}

Rules:
- Choose at most {max_files} files.
- Use only paths from the candidate file list.
- Prefer source, config, docs, tests, logs, and files directly related to the request.
- Avoid generated, dependency, cache, binary, and unrelated files.
- If the tree is enough and no file is needed, return {{"files":[]}}.

Folder: {context.root}

Folder tree:
{context.tree or "[empty tree]"}

Candidate text files:
{file_list}
""".strip()
    return [
        {"role": "system", "content": content},
        *recent,
        {"role": "user", "content": prompt},
    ]


def extract_json_object(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


def parse_selected_files(raw: str, allowed_files: set[str], *, max_files: int) -> list[str]:
    parsed = extract_json_object(raw)
    candidates: list[Any] = []
    if isinstance(parsed, dict):
        value = parsed.get("files") or parsed.get("paths") or parsed.get("selected_files")
        if isinstance(value, list):
            candidates = value
    elif isinstance(parsed, list):
        candidates = parsed

    selected: list[str] = []
    for item in candidates:
        path = str(item).strip()
        if path in allowed_files and path not in selected:
            selected.append(path)
        if len(selected) >= max_files:
            break

    if selected:
        return selected

    for path in allowed_files:
        if re.search(rf"(?<![\w./-]){re.escape(path)}(?![\w./-])", raw):
            selected.append(path)
        if len(selected) >= max_files:
            break
    return selected


def render_selected_folder_files(selections: list[tuple[Path, list[str]]]) -> None:
    if not selections:
        return

    table = Table(title="Smart Context", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("Folder", style="white")
    table.add_column("Selected files", justify="right", style="green")
    table.add_column("Files", style="cyan")

    for root, files in selections:
        preview = "\n".join(files[:8])
        if len(files) > 8:
            preview += f"\n... +{len(files) - 8} more"
        table.add_row(str(root), str(len(files)), preview or "-")
    CONSOLE.print(table)


def load_selected_folder_context(context: FolderContext, selected_files: list[str], *, max_file_chars: int) -> FolderContext:
    allowed = set(context.file_paths)
    files: list[tuple[str, str]] = []

    with folder_progress() as progress:
        task = progress.add_task(f"Loading selected files from {context.root}", total=max(1, len(selected_files)))
        for relative in selected_files:
            progress.advance(task)
            if relative not in allowed:
                continue
            path = context.root / relative
            text = safe_read_text(path, max_chars=max_file_chars)
            if text is None:
                continue
            files.append((relative, text))

    return FolderContext(
        root=context.root,
        tree=context.tree,
        files=files,
        file_paths=context.file_paths,
        mode=context.mode,
        skipped=context.skipped,
        skipped_by_limit=context.skipped_by_limit,
        skipped_reasons=context.skipped_reasons,
    )


def prepare_folder_contexts_for_prompt(
    settings: Settings,
    model: str,
    conversation: list[dict[str, str]],
    prompt: str,
    contexts: list[FolderContext],
    *,
    max_file_chars: int,
    smart_files: int,
) -> list[FolderContext]:
    prepared: list[FolderContext] = []
    selections: list[tuple[Path, list[str]]] = []

    for context in contexts:
        if context.mode != "smart":
            prepared.append(context)
            continue

        if not context.file_paths:
            prepared.append(context)
            selections.append((context.root, []))
            continue

        try:
            with CONSOLE.status(f"Choosing relevant files from {context.root}...", spinner="dots"):
                reply = chat_completion(
                    settings,
                    file_selection_messages(conversation, prompt, context, max_files=smart_files),
                    model,
                )
        except ApiError as exc:
            CONSOLE.print(Panel(f"Could not choose smart folder files: {exc}\nUsing folder tree only.", title="Smart Context", border_style="yellow"))
            prepared.append(context)
            continue

        selected = parse_selected_files(reply.content, set(context.file_paths), max_files=smart_files)
        selections.append((context.root, selected))
        prepared.append(load_selected_folder_context(context, selected, max_file_chars=max_file_chars))

    render_selected_folder_files(selections)
    return prepared


def render_folder_contexts(contexts: list[FolderContext]) -> None:
    if not contexts:
        return

    table = Table(title="Folder Context", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("Folder", style="white")
    table.add_column("Mode", style="cyan")
    table.add_column("Candidate files", justify="right", style="blue")
    table.add_column("Files loaded", justify="right", style="green")
    table.add_column("Tree skipped", justify="right", style="yellow")
    table.add_column("Why", style="yellow")

    for context in contexts:
        table.add_row(
            str(context.root),
            context.mode,
            str(len(context.file_paths)),
            str(len(context.files)),
            str(context.skipped),
            format_skip_reasons(context),
        )
    CONSOLE.print(table)


def folder_browser(start: Path | str | None = None) -> Path | None:
    current = (Path(start) if start is not None else Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    if not current.exists():
        current = Path.cwd().resolve()

    while True:
        try:
            dirs = [
                path
                for path in sorted(current.iterdir(), key=lambda item: item.name.lower())
                if path.is_dir() and path.name not in IGNORED_DIRS
            ]
        except OSError as exc:
            CONSOLE.print(Panel(str(exc), title="Folder Browser", border_style="red"))
            current = current.parent
            continue

        table = Table(title=f"Folder Browser: {current}", box=box.SIMPLE_HEAVY)
        table.add_column("No", justify="right", style="cyan", no_wrap=True)
        table.add_column("Folder", style="white")
        table.add_row(".", "Attach this folder")
        table.add_row("0", "Go to parent folder")
        table.add_row("q", "Cancel")
        for index, path in enumerate(dirs, start=1):
            table.add_row(str(index), f"{path.name}/")
        CONSOLE.print(table)

        answer = ask_number_or_text("Choose folder")
        lowered = answer.lower()
        if lowered in {"q", "quit", "cancel", "iptal"}:
            return None
        if answer == ".":
            return current
        if answer == "0":
            current = current.parent
            continue
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(dirs):
                current = dirs[index - 1]
                continue
        candidate = Path(answer).expanduser()
        if candidate.exists() and candidate.is_dir():
            current = candidate.resolve()
            continue

        CONSOLE.print("[red]Invalid folder choice. Use a number, 0, ., q, or a folder path.[/red]")


def attach_folder_interactively(
    folder_contexts: list[FolderContext],
    *,
    start: str | None,
    max_files: int,
    max_file_chars: int,
    max_tree_entries: int,
    mode: str,
) -> None:
    selected = folder_browser(Path(start).expanduser() if start else None)
    if selected is None:
        CONSOLE.print(Panel("Folder attach cancelled.", border_style="yellow"))
        return

    try:
        context = load_folder_context(
            str(selected),
            max_files=max_files,
            max_file_chars=max_file_chars,
            max_tree_entries=max_tree_entries,
            mode=mode,
        )
    except SystemExit as exc:
        CONSOLE.print(Panel(str(exc), title="Folder Context", border_style="red"))
        return

    folder_contexts.append(context)
    render_folder_contexts([context])


class DuckDuckGoHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[WebResult] = []
        self._current_title: list[str] = []
        self._current_url = ""
        self._current_snippet: list[str] = []
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        classes = attrs_map.get("class", "")

        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._flush_current()
            self._capture_title = True
            self._current_url = clean_duckduckgo_url(attrs_map.get("href", ""))
            self._current_title = []
            self._current_snippet = []
            return

        if "result__snippet" in classes or "result-snippet" in classes:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
        if tag in {"a", "div", "span"} and self._capture_snippet:
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title.append(data)
        if self._capture_snippet:
            self._current_snippet.append(data)

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        title = normalize_space(" ".join(self._current_title))
        snippet = normalize_space(" ".join(self._current_snippet))
        if title and self._current_url:
            self.results.append(WebResult(title=title, url=self._current_url, snippet=snippet))
        self._current_title = []
        self._current_url = ""
        self._current_snippet = []


class PageTextParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = normalize_space(data)
        if len(text) >= 40:
            self.parts.append(text)

    def text(self, *, limit: int) -> str:
        content = normalize_space(" ".join(self.parts))
        return content[:limit]


def normalize_space(value: str) -> str:
    return " ".join(html.unescape(value).split())


def format_thinking_text(value: str) -> str:
    text = html.unescape(value).replace("\\n", "\n")
    text = re.sub(r"\s+", " ", text).strip()

    # Some models stream reasoning as compact token fragments. Add readable
    # breaks around common sentence and casing boundaries without touching code.
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[.!?])(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?<=[\"')\]])(?=[A-Z])", " ", text)
    replacements = {
        "Theusersays": "The user says",
        "Theuserused": "The user used",
        "whichis": "which is",
        "Needto": "Need to",
        "respondappropriately": "respond appropriately",
        "Probablygreetback": "Probably greet back",
        "Turkishgreeting": "Turkish greeting",
        "sorespondin": "so respond in",
        "Couldaskhowcanhelp": "Could ask how can help",
        "Sizenasılyardımcıolabilirim": "Size nasıl yardımcı olabilirim",
        "Soreply": "So reply",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"(?<=[a-zA-Z])(?=Need|Probably|Use|Could|Should|The user|So reply)", " ", text)
    return text.strip()


def should_show_thinking(text: str) -> bool:
    mode = os.environ.get("NVIDIA_SHOW_THINKING", "false").strip().lower()
    if mode in {"0", "false", "no", "off", "never"}:
        return False
    if mode in {"1", "true", "yes", "on", "always"}:
        return True

    formatted = format_thinking_text(text)
    if len(formatted) < 180:
        return False

    trivial_markers = (
        "hello",
        "greet",
        "selam",
        "nasıl yardımcı",
        "how can help",
    )
    lowered = formatted.lower()
    return not any(marker in lowered for marker in trivial_markers)


def sanitize_search_query(prompt: str) -> str:
    query = normalize_space(prompt)
    query = re.sub(
        r"^(selam|selamlar|merhaba|hey|hi|hello|sana|bana|lütfen|lutfen)\b[,\s:;-]*",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"\b(web|internette|internet|içeriğini|içerğini|icerigini|icerğini|içeriği|icerigi|kullan|bul|ara|sana|bana|sordum|sordun)\b",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    query = normalize_space(query)
    return query or normalize_space(prompt)


def clean_duckduckgo_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(html.unescape(url))
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    return html.unescape(url)


def search_web(query: str, *, limit: int) -> list[WebResult]:
    encoded = urllib.parse.urlencode({"q": query})
    url = f"https://lite.duckduckgo.com/lite/?{encoded}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "nvidia-chat-cli/0.1 (+https://github.com/)",
            "Accept": "text/html",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise ApiError(None, f"Web search failed: {exc.reason}") from exc

    parser = DuckDuckGoHTMLParser()
    parser.feed(body)
    parser.close()

    seen: set[str] = set()
    results: list[WebResult] = []
    for result in parser.results:
        if result.url in seen:
            continue
        seen.add(result.url)
        results.append(result)
        if len(results) >= limit:
            break
    return results


def fetch_page_text(url: str, *, limit: int = 2500, timeout: int = 5) -> str:
    if not url.startswith(("http://", "https://")):
        return ""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "nvidia-chat-cli/0.1 (+https://github.com/)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                return ""
            body = response.read(500_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return ""

    parser = PageTextParser()
    parser.feed(body)
    parser.close()
    return parser.text(limit=limit)


def enrich_web_results(results: list[WebResult], *, page_limit: int = 2500, max_pages: int = 3) -> list[WebResult]:
    enriched: list[WebResult] = []
    for index, result in enumerate(results):
        content = ""
        if index < max_pages:
            content = fetch_page_text(result.url, limit=page_limit)
        enriched.append(
            WebResult(
                title=result.title,
                url=result.url,
                snippet=result.snippet,
                content=content,
            )
        )
    return enriched


def web_context_message(query: str, results: list[WebResult]) -> dict[str, str]:
    if not results:
        content = "No web search results were found for this query."
    else:
        lines = [
            "Use the following web search results and extracted page text as fresh context.",
            "When using web information, cite source numbers like [1], [2].",
            "Do not make claims from the web without a citation.",
            "If the search results are insufficient, say that clearly.",
            "",
            f"Query: {query}",
            "",
        ]
        for index, result in enumerate(results, start=1):
            lines.append(f"[{index}] {result.title}")
            lines.append(f"URL: {result.url}")
            if result.snippet:
                lines.append(f"Snippet: {result.snippet}")
            if result.content:
                lines.append(f"Extracted text: {result.content}")
            lines.append("")
        content = "\n".join(lines).strip()

    return {"role": "system", "content": content}


def render_web_results(results: list[WebResult]) -> None:
    if not results:
        CONSOLE.print(Panel("No web results found.", title="Web", border_style="yellow"))
        return

    table = Table(title="Web Context", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("No", justify="right", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Source", style="blue")

    for index, result in enumerate(results, start=1):
        table.add_row(str(index), result.title, result.url)
    CONSOLE.print(table)


def render_web_query(query: str) -> None:
    CONSOLE.print(Panel(query, title="Web Search Query", border_style="blue", expand=False))


def decide_web_query(
    settings: Settings,
    model: str,
    conversation: list[dict[str, str]],
    prompt: str,
) -> str:
    fallback = sanitize_search_query(prompt)
    recent = conversation[-6:]
    planning_messages = [
        {
            "role": "system",
            "content": (
                "You create a web search query before answering. "
                "Do not answer the user's question. Return only compact JSON. "
                "Return {\"query\":\"search terms\"}. "
                "Never return null. Never say search is not needed. "
                "Use a concise search-engine query in the user's language when possible."
            ),
        },
        *recent,
        {"role": "user", "content": prompt},
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": planning_messages,
        "temperature": 0,
        "max_tokens": 80,
    }

    data = request_json(
        "POST",
        f"{settings.base_url}/chat/completions",
        api_key=settings.api_key,
        payload=payload,
        timeout=45,
    )
    reply = parse_chat_response(data).content.strip()
    json_match = re.search(r"\{.*?\}", reply, flags=re.DOTALL)
    if json_match:
        reply = json_match.group(0)

    try:
        parsed = json.loads(reply)
        query = parsed.get("query")
        if query is None:
            return fallback
        query = str(query).strip()
        if query.startswith("{") or "\n" in query:
            return fallback
        return sanitize_search_query(query) or fallback
    except json.JSONDecodeError:
        cleaned = reply.strip().strip("`").strip()
        if cleaned.lower() in {"none", "null", "no", "no search", "search not needed"}:
            return fallback
        if cleaned.startswith("{") or len(cleaned.split()) > 12:
            return fallback
        return sanitize_search_query(cleaned[:200]) or fallback


def category_for(model: str) -> str:
    lowered = model.lower()

    if any(token in lowered for token in ["embed", "bge", "retriever", "nvclip"]):
        return "Embeddings and Retrieval"
    if any(token in lowered for token in ["guard", "safety", "pii", "detector", "reward"]):
        return "Safety, Moderation, and Reward"
    if any(token in lowered for token in ["vision", "vl", "vila", "neva", "fuyu", "kosmos", "multimodal", "deplot", "video"]):
        return "Vision and Multimodal"
    if any(token in lowered for token in ["code", "coder", "codestral", "starcoder"]):
        return "Code"
    if any(token in lowered for token in ["reasoning", "reason"]):
        return "Reasoning"
    if any(token in lowered for token in ["translate", "riva"]):
        return "Translation and Speech"
    if any(token in lowered for token in ["med", "fin", "creative"]):
        return "Domain Specific"
    if any(token in lowered for token in ["instruct", "chat", "glm", "jamba", "mistral", "llama", "deepseek", "qwen", "gpt-oss", "kimi", "minimax"]):
        return "General Chat"
    return "Other"


def categorized_models(models: list[str]) -> dict[str, list[str]]:
    popular = [model for model in POPULAR_MODELS if model in models]
    popular_set = set(popular)
    categories: dict[str, list[str]] = {"Popular": popular}

    for model in models:
        if model in popular_set:
            continue
        categories.setdefault(category_for(model), []).append(model)

    return {
        category: categories[category]
        for category in CATEGORY_ORDER
        if categories.get(category)
    }


def chat_categories(models: list[str]) -> dict[str, list[str]]:
    categories = categorized_models(models)
    return {
        category: categories[category]
        for category in CHAT_CATEGORY_ORDER
        if categories.get(category)
    }


def print_numbered(items: list[str], start: int = 1) -> int:
    for index, item in enumerate(items, start=start):
        CONSOLE.print(f"[cyan]{index:>3})[/cyan] {item}")
    return start + len(items)


def model_table(title: str, items: list[str], start: int = 1) -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("No", justify="right", style="cyan", no_wrap=True)
    table.add_column("Model", style="white")
    for index, item in enumerate(items, start=start):
        table.add_row(str(index), item)
    return table


def category_table(categories: dict[str, list[str]], *, include_all_api: bool) -> Table:
    table = Table(title="Select a Category", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("No", justify="right", style="cyan", no_wrap=True)
    table.add_column("Category", style="bold white")
    table.add_column("Models", justify="right", style="green")

    for index, category in enumerate(categories, start=1):
        table.add_row(str(index), category, str(len(categories[category])))
    table.add_row(str(len(categories) + 1), ALL_MODELS_LABEL, "chat")
    if include_all_api:
        table.add_row(str(len(categories) + 2), NON_CHAT_CATEGORY_HINT, "all")
    table.add_row("0", "Exit model selection", "")
    return table


def print_models(models: list[str], *, raw: bool = False, popular_only: bool = False) -> None:
    if raw:
        print("\n".join(models))
        return

    if popular_only:
        CONSOLE.print(model_table("Popular Models", [model for model in POPULAR_MODELS if model in models]))
        return

    next_index = 1
    for category, values in categorized_models(models).items():
        CONSOLE.print(model_table(category, values, next_index))
        next_index += len(values)


def flatten_categorized(models: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    for values in categorized_models(models).values():
        for model in values:
            if model not in seen:
                ordered.append(model)
                seen.add(model)

    return ordered


def flatten_categorized_from_categories(categories: dict[str, list[str]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    for values in categories.values():
        for model in values:
            if model not in seen:
                ordered.append(model)
                seen.add(model)

    return ordered


def ask_number_or_text(prompt: str) -> str:
    try:
        return Prompt.ask(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        CONSOLE.print()
        raise SystemExit(130)


def choose_category(categories: dict[str, list[str]], *, include_all_api: bool) -> str | None:
    options = list(categories)
    all_index = len(options) + 1
    all_api_index = len(options) + 2

    CONSOLE.print(category_table(categories, include_all_api=include_all_api))

    while True:
        answer = ask_number_or_text("Category number or name")
        if answer.isdigit():
            index = int(answer)
            if index == 0:
                return None
            if 1 <= index <= len(options):
                return options[index - 1]
            if index == all_index:
                return ALL_MODELS_LABEL
            if include_all_api and index == all_api_index:
                return NON_CHAT_CATEGORY_HINT

        normalized = answer.lower()
        if normalized in {"all", "all models", "all chat", "hepsi", "hepsini goster", "hepsini göster"}:
            return ALL_MODELS_LABEL
        if normalized in {"all api", "all api models", "api", "tum api", "tüm api"}:
            return NON_CHAT_CATEGORY_HINT

        for category in options:
            if normalized == category.lower():
                return category

        CONSOLE.print("[red]Invalid category. Enter 0 to exit model selection.[/red]")


def choose_from_models(models: list[str], *, title: str) -> str | None:
    table = model_table(title, models)
    table.add_row("0", "Back to categories")
    CONSOLE.print(table)

    while True:
        answer = ask_number_or_text("Model number or model id")
        if answer.isdigit():
            index = int(answer)
            if index == 0:
                return None
            if 1 <= index <= len(models):
                return models[index - 1]
        if answer in models:
            return answer
        CONSOLE.print("[red]Invalid model. Enter 0 to go back.[/red]")


def choose_from_all_models(models: list[str]) -> str | None:
    ordered = flatten_categorized(models)
    print_models(models)

    while True:
        answer = ask_number_or_text("Model number or model id")
        if answer.isdigit():
            index = int(answer)
            if index == 0:
                return None
            if 1 <= index <= len(ordered):
                return ordered[index - 1]
        if answer in models:
            return answer
        CONSOLE.print("[red]Invalid model. Enter 0 to go back.[/red]")


def choose_model(models: list[str], default_model: str | None) -> str:
    if default_model:
        return default_model

    selectable_categories = chat_categories(models)
    while True:
        category = choose_category(selectable_categories, include_all_api=True)
        if category is None:
            raise SystemExit(0)
        if category == ALL_MODELS_LABEL:
            selected = choose_from_all_models(flatten_categorized_from_categories(selectable_categories))
        elif category == NON_CHAT_CATEGORY_HINT:
            selected = choose_from_all_models(models)
        else:
            selected = choose_from_models(selectable_categories[category], title=category)

        if selected is not None:
            return selected


def stringify_response_part(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(
                    stringify_response_part(
                        item.get("text")
                        or item.get("content")
                        or item.get("reasoning")
                        or item.get("summary")
                        or item
                    )
                )
            else:
                parts.append(stringify_response_part(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        preferred = value.get("text") or value.get("content") or value.get("summary")
        if preferred:
            return stringify_response_part(preferred)
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def split_think_tags(content: str) -> ChatReply:
    start_tag = "<think>"
    end_tag = "</think>"
    if start_tag not in content or end_tag not in content:
        return ChatReply(content=content)

    before, rest = content.split(start_tag, 1)
    thinking, after = rest.split(end_tag, 1)
    visible = f"{before}{after}".strip()
    return ChatReply(content=visible, thinking=thinking.strip() or None)


def extract_thinking(choice: dict[str, Any], message: dict[str, Any]) -> str | None:
    fields = (
        "reasoning_content",
        "reasoning",
        "thinking",
        "thoughts",
        "analysis",
        "reasoning_details",
    )

    candidates: list[str] = []
    for source in (message, choice):
        for field in fields:
            value = source.get(field)
            text = stringify_response_part(value).strip()
            if text:
                candidates.append(text)

    if not candidates:
        return None

    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return "\n\n".join(unique)


def render_reply(reply: ChatReply) -> None:
    if reply.thinking and should_show_thinking(reply.thinking):
        render_thinking(reply.thinking)
    render_assistant_content(reply.content or "(empty response)")


def clean_markdown(value: str) -> str:
    text = value.replace("\\n", "\n")
    text = clean_table_breaks(text)
    text = text.replace("<br/>", "\n").replace("<br />", "\n").replace("<br>", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_table_breaks(text: str) -> str:
    cleaned_lines = []
    for line in text.splitlines():
        if "|" in line:
            line = re.sub(r"<br\s*/?>", "; ", line, flags=re.IGNORECASE)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def render_assistant_content(content: str) -> None:
    text = clean_markdown(content)
    if try_render_markdown_table(text):
        return
    CONSOLE.print(Panel(Markdown(text), title="Assistant", border_style="green"))


def try_render_markdown_table(text: str) -> bool:
    lines = text.splitlines()
    table_start = None
    for index in range(len(lines) - 1):
        if "|" in lines[index] and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", lines[index + 1]):
            table_start = index
            break

    if table_start is None:
        return False

    before = "\n".join(lines[:table_start]).strip()
    table_lines: list[str] = []
    after_lines: list[str] = []
    in_table = False
    for line in lines[table_start:]:
        if "|" in line and (in_table or re.match(r"\s*\|", line)):
            table_lines.append(line)
            in_table = True
        else:
            after_lines.append(line)

    parsed = parse_markdown_table(table_lines)
    if parsed is None:
        return False

    headers, rows = parsed
    if before:
        CONSOLE.print(Panel(Markdown(before), title="Assistant", border_style="green"))

    table = Table(title="Assistant", box=box.SIMPLE_HEAVY, show_lines=True, expand=True)
    for header in headers:
        table.add_column(header or " ", overflow="fold")
    for row in rows:
        table.add_row(*(Markdown(cell) for cell in row))
    CONSOLE.print(table)

    after = "\n".join(after_lines).strip()
    if after:
        CONSOLE.print(Panel(Markdown(after), title="Assistant", border_style="green"))
    return True


def parse_markdown_table(lines: list[str]) -> tuple[list[str], list[list[str]]] | None:
    if len(lines) < 2:
        return None

    def split_row(line: str) -> list[str]:
        stripped = line.strip().strip("|")
        return [cell.strip() for cell in stripped.split("|")]

    headers = split_row(lines[0])
    rows = [split_row(line) for line in lines[2:] if "|" in line]
    if not headers or not rows:
        return None

    width = len(headers)
    normalized_rows = []
    for row in rows:
        if len(row) < width:
            row = row + [""] * (width - len(row))
        elif len(row) > width:
            row = row[: width - 1] + [" | ".join(row[width - 1 :])]
        normalized_rows.append(row)
    return headers, normalized_rows


def render_thinking(text: str) -> None:
    preview_limit = int(os.environ.get("NVIDIA_THINKING_PREVIEW_CHARS", "4000"))
    text = format_thinking_text(text)
    if len(text) > preview_limit:
        text = f"{text[:preview_limit].rstrip()} ... [truncated]"

    CONSOLE.print(
        Panel(
            Text(text, style="dim italic"),
            title="[dim]Thinking[/dim]",
            border_style="bright_black",
            expand=False,
            padding=(0, 1),
        )
    )


def render_thinking_collected() -> None:
    CONSOLE.print(
        Panel(
            Text("collecting reasoning; will show after the answer", style="dim italic"),
            title="[dim]Thinking[/dim]",
            border_style="bright_black",
            expand=False,
            padding=(0, 1),
        )
    )


def should_stream() -> bool:
    value = os.environ.get("NVIDIA_STREAM", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def chat_completion(settings: Settings, messages: list[dict[str, str]], model: str) -> ChatReply:
    payload = chat_payload(messages, model)
    data = request_json(
        "POST",
        f"{settings.base_url}/chat/completions",
        api_key=settings.api_key,
        payload=payload,
    )
    return parse_chat_response(data)


def chat_payload(messages: list[dict[str, str]], model: str, *, stream: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(os.environ.get("NVIDIA_TEMPERATURE", "0.7")),
        "max_tokens": int(os.environ.get("NVIDIA_MAX_TOKENS", "1024")),
    }
    if stream:
        payload["stream"] = True
    return payload


def parse_chat_response(data: dict[str, Any]) -> ChatReply:
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = stringify_response_part(message.get("content")).strip()
        thinking = extract_thinking(choice, message)
        reply = split_think_tags(content)

        if thinking and reply.thinking:
            thinking = f"{reply.thinking}\n\n{thinking}"
        elif reply.thinking:
            thinking = reply.thinking

        return ChatReply(content=reply.content, thinking=thinking)
    except (KeyError, IndexError, TypeError) as exc:
        raise ApiError(None, f"Unexpected chat response: {data}") from exc


def extract_stream_delta(chunk: dict[str, Any]) -> ChatReply:
    try:
        choice = chunk["choices"][0]
    except (KeyError, IndexError, TypeError):
        return ChatReply(content="")

    delta = choice.get("delta") or {}
    content = stringify_response_part(delta.get("content"))
    thinking = extract_thinking(choice, delta)

    if not content and "message" in choice:
        parsed = parse_chat_response({"choices": [choice]})
        content = parsed.content
        thinking = thinking or parsed.thinking

    return ChatReply(content=content, thinking=thinking)


def chat_completion_stream(settings: Settings, messages: list[dict[str, str]], model: str) -> ChatReply:
    payload = chat_payload(messages, model, stream=True)
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    thinking_started = False
    answer_started = False
    inside_think_tag = False

    def emit_thinking(text: str) -> None:
        nonlocal thinking_started
        if not text:
            return
        if not thinking_started:
            render_thinking_collected()
            thinking_started = True
        thinking_parts.append(text)

    def emit_answer(text: str) -> None:
        nonlocal answer_started
        if not text:
            return
        if not answer_started:
            CONSOLE.print(Panel("Receiving answer...", title="Assistant", border_style="green"))
            answer_started = True
        content_parts.append(text)

    def route_content(text: str) -> None:
        nonlocal inside_think_tag
        remaining = text

        while remaining:
            if inside_think_tag:
                end_index = remaining.find("</think>")
                if end_index == -1:
                    emit_thinking(remaining)
                    return
                emit_thinking(remaining[:end_index])
                remaining = remaining[end_index + len("</think>") :]
                inside_think_tag = False
                continue

            start_index = remaining.find("<think>")
            if start_index == -1:
                emit_answer(remaining)
                return

            emit_answer(remaining[:start_index])
            remaining = remaining[start_index + len("<think>") :]
            inside_think_tag = True

    for chunk in request_sse(
        f"{settings.base_url}/chat/completions",
        api_key=settings.api_key,
        payload=payload,
        timeout=120,
    ):
        delta = extract_stream_delta(chunk)

        if delta.thinking:
            emit_thinking(delta.thinking)

        if delta.content:
            route_content(delta.content)

    content = "".join(content_parts).strip()
    thinking = "".join(thinking_parts).strip() or None
    reply = split_think_tags(content)
    if thinking and reply.thinking:
        thinking = f"{reply.thinking}\n\n{thinking}"
    elif reply.thinking:
        thinking = reply.thinking
    final_reply = ChatReply(content=reply.content, thinking=thinking, streamed=True)
    if final_reply.content:
        render_assistant_content(final_reply.content)
    if final_reply.thinking and should_show_thinking(final_reply.thinking):
        render_thinking(final_reply.thinking)
    return final_reply


def get_chat_reply(settings: Settings, messages: list[dict[str, str]], model: str, *, stream: bool) -> ChatReply:
    if not stream:
        with CONSOLE.status("Waiting for NVIDIA...", spinner="dots"):
            return chat_completion(settings, messages, model)

    try:
        return chat_completion_stream(settings, messages, model)
    except ApiError as exc:
        if exc.status in {400, 404, 405, 422}:
            CONSOLE.print(Panel("Streaming is not available for this model; retrying normally.", border_style="yellow"))
            with CONSOLE.status("Waiting for NVIDIA...", spinner="dots"):
                return chat_completion(settings, messages, model)
        raise


def build_messages(
    conversation: list[dict[str, str]],
    prompt: str,
    *,
    settings: Settings,
    model: str,
    web_enabled: bool,
    web_results: int,
    web_direct: bool,
    folder_contexts: list[FolderContext],
    folder_max_file_chars: int,
    folder_smart_files: int,
) -> tuple[list[dict[str, str]], list[WebResult]]:
    messages = list(conversation)
    results: list[WebResult] = []

    if folder_contexts:
        prepared_contexts = prepare_folder_contexts_for_prompt(
            settings,
            model,
            conversation,
            prompt,
            folder_contexts,
            max_file_chars=folder_max_file_chars,
            smart_files=folder_smart_files,
        )
        messages.append(folder_context_message(prepared_contexts))

    if web_enabled:
        query = prompt
        if not web_direct:
            with CONSOLE.status("Deciding what to search...", spinner="dots"):
                try:
                    query = decide_web_query(settings, model, conversation, prompt)
                except ApiError as exc:
                    CONSOLE.print(Panel(f"Could not plan web search: {exc}\nUsing the prompt as the query.", title="Web", border_style="yellow"))
                    query = sanitize_search_query(prompt)
        else:
            query = sanitize_search_query(prompt)

        query = sanitize_search_query(query) or sanitize_search_query(prompt)
        render_web_query(query)

        with CONSOLE.status("Searching the web...", spinner="dots"):
            results = search_web(query, limit=web_results)
        with CONSOLE.status("Reading web pages...", spinner="dots"):
            results = enrich_web_results(results)
        render_web_results(results)
        messages.append(web_context_message(query, results))

    messages.append({"role": "user", "content": prompt})
    return messages, results


def run_one_shot(
    settings: Settings,
    model: str,
    prompt: str,
    *,
    web_enabled: bool,
    web_results: int,
    web_direct: bool,
    folder_contexts: list[FolderContext],
    folder_max_file_chars: int,
    folder_smart_files: int,
) -> None:
    messages, _ = build_messages(
        [],
        prompt,
        settings=settings,
        model=model,
        web_enabled=web_enabled,
        web_results=web_results,
        web_direct=web_direct,
        folder_contexts=folder_contexts,
        folder_max_file_chars=folder_max_file_chars,
        folder_smart_files=folder_smart_files,
    )
    CONSOLE.print(Panel(prompt, title="You", border_style="cyan", expand=False))
    reply = get_chat_reply(settings, messages, model, stream=should_stream())
    if not reply.streamed:
        render_reply(reply)


def run_interactive(
    settings: Settings,
    model: str,
    *,
    web_enabled: bool,
    web_results: int,
    web_direct: bool,
    folder_contexts: list[FolderContext],
    folder_max_files: int,
    folder_max_file_chars: int,
    folder_tree_entries: int,
    folder_mode: str,
    folder_smart_files: int,
) -> None:
    messages: list[dict[str, str]] = []
    header = Text()
    header.append("NVIDIA Chat CLI\n", style="bold green")
    header.append("Model: ", style="dim")
    header.append(model, style="bold white")
    header.append("\nCommands: /clear, /exit, /web on, /web off, /folder", style="dim")
    header.append(f"\nWeb: {'on' if web_enabled else 'off'}", style="dim")
    if folder_contexts:
        header.append(f"\nFolders: {len(folder_contexts)} attached", style="dim")
    CONSOLE.print(Panel(header, border_style="green"))

    while True:
        try:
            prompt = Prompt.ask("[bold cyan]you[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            CONSOLE.print()
            return

        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return
        if prompt == "/clear":
            messages.clear()
            CONSOLE.print(Panel("Conversation cleared.", border_style="yellow"))
            continue
        if prompt == "/web on":
            web_enabled = True
            CONSOLE.print(Panel(f"Web context enabled. Using up to {web_results} results per prompt.", border_style="blue"))
            continue
        if prompt == "/web off":
            web_enabled = False
            CONSOLE.print(Panel("Web context disabled.", border_style="blue"))
            continue
        if prompt == "/folders":
            render_folder_contexts(folder_contexts)
            continue
        if prompt == "/clear-folders":
            folder_contexts.clear()
            CONSOLE.print(Panel("Folder context cleared.", border_style="yellow"))
            continue
        if prompt == "/folder" or prompt.startswith("/folder "):
            parts = prompt.split(maxsplit=1)
            attach_folder_interactively(
                folder_contexts,
                start=parts[1] if len(parts) == 2 else None,
                max_files=folder_max_files,
                max_file_chars=folder_max_file_chars,
                max_tree_entries=folder_tree_entries,
                mode=folder_mode,
            )
            continue

        request_messages, _ = build_messages(
            messages,
            prompt,
            settings=settings,
            model=model,
            web_enabled=web_enabled,
            web_results=web_results,
            web_direct=web_direct,
            folder_contexts=folder_contexts,
            folder_max_file_chars=folder_max_file_chars,
            folder_smart_files=folder_smart_files,
        )
        CONSOLE.print(Panel(prompt, title="You", border_style="cyan", expand=False))
        try:
            reply = get_chat_reply(settings, request_messages, model, stream=should_stream())
        except ApiError as exc:
            CONSOLE.print(Panel(str(exc), title="Error", border_style="red"))
            continue

        messages.append({"role": "user", "content": prompt})
        messages.append({"role": "assistant", "content": reply.content})
        if not reply.streamed:
            render_reply(reply)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nvidia-chat",
        description="Terminal chat client for NVIDIA NIM models.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("prompt", nargs="*", help="One-shot prompt. Omit for interactive chat.")
    parser.add_argument("-m", "--model", help="Model id to use.")
    parser.add_argument("--models", action="store_true", help="Show categorized live model list.")
    parser.add_argument("--raw-models", action="store_true", help="Show raw live model ids.")
    parser.add_argument("--popular", action="store_true", help="Show curated popular top 10 models.")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming responses.")
    parser.add_argument("--web", action="store_true", help="Use web search context for answers.")
    parser.add_argument("--web-direct", action="store_true", help="Search the user prompt directly instead of asking the model to choose a query.")
    parser.add_argument("--web-results", type=int, default=5, help="Number of web search results to use.")
    parser.add_argument("--folder", action="append", default=[], help="Attach a folder as project context. Can be used multiple times.")
    parser.add_argument("--folder-max-files", type=int, default=DEFAULT_FOLDER_MAX_FILES, help="Maximum text files to include per folder.")
    parser.add_argument("--folder-max-file-chars", type=int, default=DEFAULT_FOLDER_MAX_FILE_CHARS, help="Maximum characters to include per file.")
    parser.add_argument("--folder-tree-entries", type=int, default=DEFAULT_FOLDER_TREE_ENTRIES, help="Maximum folder tree entries to show.")
    parser.add_argument("--folder-mode", choices=["smart", "all"], default="smart", help="How attached folders are loaded.")
    parser.add_argument("--folder-smart-files", type=int, default=DEFAULT_FOLDER_SMART_FILES, help="Maximum files the model can select per smart folder per prompt.")
    parser.add_argument("--set-key", action="store_true", help="Prompt for a new API key, validate it, and save it.")
    parser.add_argument("--config", action="store_true", help="Show config file location.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.no_stream:
        os.environ["NVIDIA_STREAM"] = "false"

    settings = load_settings()

    if args.config:
        print(config_file())
        return

    if args.set_key:
        settings = Settings(None, settings.base_url, settings.default_model, settings.validate_model)
        ensure_api_key(settings)
        return

    if args.models or args.raw_models or args.popular:
        try:
            models = fetch_models(settings)
        except ApiError as exc:
            raise SystemExit(f"Could not fetch models: {exc}") from exc
        print_models(models, raw=args.raw_models, popular_only=args.popular)
        return

    settings = ensure_api_key(settings)
    try:
        models = fetch_models(settings)
    except ApiError as exc:
        raise SystemExit(f"Could not fetch models: {exc}") from exc

    model = args.model or choose_model(models, settings.default_model)
    folder_contexts = [
        load_folder_context(
            folder,
            max_files=args.folder_max_files,
            max_file_chars=args.folder_max_file_chars,
            max_tree_entries=args.folder_tree_entries,
            mode=args.folder_mode,
        )
        for folder in args.folder
    ]
    render_folder_contexts(folder_contexts)

    prompt = " ".join(args.prompt).strip()
    if prompt:
        run_one_shot(
            settings,
            model,
            prompt,
            web_enabled=args.web,
            web_results=args.web_results,
            web_direct=args.web_direct,
            folder_contexts=folder_contexts,
            folder_max_file_chars=args.folder_max_file_chars,
            folder_smart_files=args.folder_smart_files,
        )
    else:
        run_interactive(
            settings,
            model,
            web_enabled=args.web,
            web_results=args.web_results,
            web_direct=args.web_direct,
            folder_contexts=folder_contexts,
            folder_max_files=args.folder_max_files,
            folder_max_file_chars=args.folder_max_file_chars,
            folder_tree_entries=args.folder_tree_entries,
            folder_mode=args.folder_mode,
            folder_smart_files=args.folder_smart_files,
        )


if __name__ == "__main__":
    main()
