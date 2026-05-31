# NVIDIA Chat CLI

A polished terminal chat client for NVIDIA NIM and NVIDIA API Catalog models.

NVIDIA Chat CLI gives you a ChatGPT-like workflow in the terminal: model
selection, streaming answers, optional web context, markdown rendering, source
citations, and secure first-run API key storage.

## Highlights

- Interactive terminal chat with a clean Rich UI
- One-shot prompt mode for quick answers
- Live model discovery from NVIDIA's `/v1/models` endpoint
- Category-based model picker with curated popular models
- Streaming responses by default
- Optional web mode that lets the model choose what to search
- Folder context for project review and codebase summaries
- Web source table and citation-aware answer context
- Markdown rendering, including improved table display
- Thinking/reasoning capture for models that return it
- Secure local API key storage after first validation

## Preview

```text
Select a Category

  No   Category                 Models
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1   Popular                      10
   2   General Chat                 40
   3   Code                          8
   4   Reasoning                     2
   5   All Models                 chat
   6   All API Models              all
   0   Exit model selection
```

```text
Web Search Query
Tolga Eyinacar kimdir

Web Context
  1   Tolga Eyinacar - Bilgisayar Mühendisi | LinkedIn
  2   tolga eyinacar profile | Padlet
```

## Requirements

- Python 3.10+
- NVIDIA API key
- Internet access for NVIDIA API calls

The package uses `rich` for terminal rendering. It is installed automatically
when you install the project.

## Installation

Clone the repository:

```bash
git clone https://github.com/tolga-sparkles/nvidia-chat.git
cd nvidia-chat
```

Install in editable mode:

```bash
python3 -m pip install -e .
```

Run the CLI:

```bash
nvidia-chat
```

You can also run the local wrapper from the repository:

```bash
./nvidia-chat
```

## API Key Setup

On first run, the CLI asks for your NVIDIA API key, validates it with a tiny
chat request, and saves it here:

```text
~/.config/nvidia-chat-cli/config.env
```

The saved file is written with `600` permissions. After that, the CLI reuses
the saved key and does not ask again.

To replace the saved key:

```bash
nvidia-chat --set-key
```

You can also provide a key manually:

```bash
export NVIDIA_API_KEY="nvapi-..."
```

or create a local `.env`:

```env
NVIDIA_API_KEY=nvapi-your-key-here
```

Never commit real API keys.

## Usage

Start interactive chat:

```bash
nvidia-chat
```

Ask a one-shot question:

```bash
nvidia-chat "Explain HTTP 403 in one paragraph"
```

Use a specific model:

```bash
nvidia-chat -m meta/llama-3.3-70b-instruct "Merhaba"
```

List models:

```bash
nvidia-chat --models
nvidia-chat --popular
nvidia-chat --raw-models
```

Disable streaming:

```bash
nvidia-chat --no-stream "Give me a short answer"
```

Attach a folder as project context:

```bash
nvidia-chat --folder . "Review this project structure"
```

Show the config path:

```bash
nvidia-chat --config
```

## Model Selection

When no `--model` is provided, NVIDIA Chat CLI opens a two-step picker.

First choose a category:

- `Popular`
- `General Chat`
- `Code`
- `Reasoning`
- `Vision and Multimodal`
- `Translation and Speech`
- `Domain Specific`
- `All Models`
- `All API Models`

Then choose a model inside that category.

Navigation:

- Enter `0` inside a model list to go back to categories.
- Enter `0` on the category screen to exit model selection.

By default, the picker shows chat-friendly categories. `All API Models` also
includes non-chat entries such as embedding, safety, and reward models.

## Web Mode

Web mode is off by default. When enabled, the selected model first converts
your message into a concise search query. The CLI then searches the web, reads
short extracts from the top result pages, and passes that context back to the
model.

Use web mode:

```bash
nvidia-chat --web "What changed in the latest NVIDIA NIM model catalog?"
```

Control result count:

```bash
nvidia-chat --web --web-results 8 "latest CUDA release notes summary"
```

Search the prompt directly instead of letting the model choose the query:

```bash
nvidia-chat --web-direct "NVIDIA NIM model catalog"
```

Interactive commands:

```text
/web on
/web off
/smart-folder on
/smart-folder off
/folder
/folder path/to/start
/folders
/clear-folders
```

Folder options:

```bash
# Smart file selection is the default.
nvidia-chat --folder . "Bu projedeki auth bugını bul"

# Let the model choose up to 20 relevant files per prompt.
nvidia-chat --folder . --folder-smart-files 20 "Find the login/session bug"

# Load every matching text file up front instead of smart selection.
nvidia-chat --folder . --folder-mode all "Summarize everything loaded"
```

The model is instructed to cite web-backed claims with source numbers like
`[1]` and `[2]`.

## Folder Context

Attach a folder when you want the model to interpret, review, summarize, or
explain a project:

```bash
nvidia-chat --folder path/to/project "Bu klasörü yorumla"
nvidia-chat --folder . "Review this repository and list improvement ideas"
```

You can also attach folders inside an interactive chat:

```text
you> /folder
```

This opens a small terminal folder browser:

- Enter a number to go into a child folder.
- Enter `0` to go to the parent folder.
- Enter `.` to attach the current folder.
- Enter `q` to cancel.

Start the browser from a specific path:

```text
you> /folder path/to/project
```

Manage attached folders:

```text
you> /folders
you> /clear-folders
```

Attach multiple folders:

```bash
nvidia-chat --folder backend --folder frontend "Explain how these parts fit together"
```

By default, folder context uses smart selection. The CLI indexes the folder
tree and candidate text files first. For each prompt, the selected model chooses
the most relevant files to read, then only those files are sent as context. This
keeps large projects useful without flooding the model with unrelated files.
For example, if you ask for an auth bug, smart mode should prefer files related
to auth, login, sessions, middleware, config, and tests instead of sending the
whole project.

Heavy folders such as `.git`, `node_modules`, `.venv`, `dist`, `build`, and
cache directories are ignored.

Useful limits:

```bash
nvidia-chat --folder . \
  --folder-max-files 2000 \
  --folder-max-file-chars 8000 \
  --folder-tree-entries 2000 \
  --folder-smart-files 20 \
  "Summarize the architecture"
```

Use the old eager behavior when you really want to load all matching files up
front:

```bash
nvidia-chat --folder . --folder-mode all "Summarize everything loaded"
```

When a folder is attached, `nvidia-chat` shows scan/load progress in the
terminal. By default it indexes up to 2000 candidate text files per folder,
shows up to 2000 folder tree entries, and lets the model select up to 20 files
per prompt.
The folder summary also shows why tree entries were skipped, for example
`node_modules: 2444` or `tree limit: 300`.

Folder context is attached to each request but is not permanently written into
the chat history.

If NVIDIA returns `429 Too Many Requests` while a large folder is attached,
switch smart folder selection back on or reduce folder limits:

```text
you> /smart-folder on
```

```bash
nvidia-chat --folder . --folder-max-files 100 --folder-smart-files 10
```

## Thinking Output

Some reasoning models return separate thinking/reasoning fields or embed
thinking inside `<think>...</think>` tags.

By default, thinking output is hidden because many models return noisy internal
text. You can enable it when you want to inspect reasoning:

```bash
NVIDIA_SHOW_THINKING=always nvidia-chat
```

Disable it explicitly:

```bash
NVIDIA_SHOW_THINKING=false nvidia-chat
```

During streaming, thinking text is collected and rendered separately so it does
not mix into the final answer.

## Configuration

Configuration can come from:

1. Local `.env`
2. Saved config file at `~/.config/nvidia-chat-cli/config.env`
3. Shell environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `NVIDIA_API_KEY` | NVIDIA API key | Required for chat |
| `NVIDIA_NIM_API_KEY` | Alternative API key name | Required for chat |
| `NVIDIA_BASE_URL` | API base URL | `https://integrate.api.nvidia.com/v1` |
| `NVIDIA_MODEL` | Default model id | Ask on startup |
| `NVIDIA_VALIDATE_MODEL` | Model used for key validation | `meta/llama-3.1-8b-instruct` |
| `NVIDIA_TEMPERATURE` | Chat temperature | `0.7` |
| `NVIDIA_MAX_TOKENS` | Max response tokens | `1024` |
| `NVIDIA_STREAM` | Stream responses as they arrive | `true` |
| `NVIDIA_SHOW_THINKING` | Show reasoning/thinking output | `false` |
| `NVIDIA_THINKING_PREVIEW_CHARS` | Max thinking preview length | `4000` |

## Examples

Summarize current web information:

```bash
nvidia-chat --web "bugün dünyada öne çıkan haberleri kaynaklı özetle"
```

Use a coding model:

```bash
nvidia-chat -m qwen/qwen3-coder-480b-a35b-instruct "Write a Python CLI skeleton"
```

Review a local project folder:

```bash
nvidia-chat --folder . "Bu projeyi mimari, riskler ve geliştirme önerileriyle yorumla"
```

Use a non-streamed answer for cleaner copy/paste:

```bash
nvidia-chat --no-stream "Create a short README intro"
```

## Security Notes

- `.env` files are ignored by git.
- Saved API keys are stored outside the repository.
- The saved config file is written with user-only permissions.
- Do not paste real API keys into issues, commits, screenshots, or examples.

## License

MIT
