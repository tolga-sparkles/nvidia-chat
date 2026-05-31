# NVIDIA Chat CLI Roadmap

This roadmap tracks ideas for turning `nvidia-chat` into a polished terminal
assistant for NVIDIA API Catalog / NIM models.

## Near Term

### Context management

- [ ] Add `/context` to show active folders, loaded files, skipped entries, and
  approximate context size.
- [ ] Add `/remove-folder <number>` to detach a folder from the current chat.
- [ ] Add `/refresh-folders` to rescan attached folders after local changes.
- [x] Show the list of files that were actually loaded from each folder.
- [x] Keep skip reasons visible, such as `node_modules`, `.git`, cache folders, or
  tree limits.

### File and git helpers

- [ ] Add `/file <path>` to attach one specific file.
- [ ] Add `/diff` to send the current `git diff` as context.
- [ ] Add `/review` to review the current working tree changes.
- [ ] Add `/commit-message` to generate a commit message from the current diff.
- [ ] Add `/write <path>` to save the latest assistant response to a file.

### Chat usability

- [ ] Add `/thinking on`, `/thinking off`, and `/thinking auto`.
- [x] Improve thinking display so long reasoning output stays readable.
- [x] Add `/model` inside chat to switch models without restarting.
- [x] Add `/models` inside chat to reopen the categorized model picker.
- [ ] Add `/help` with all interactive commands.

## Mid Term

### Smart context selection

- [x] Improve the current smart folder mode where the model first sees the file tree
  and then chooses which files should be loaded.
- [x] Refine file ranking by relevance to the current user request before sending
  context.
- [x] Prefer local project files over generated, vendored, dependency, or cache
  files.
- [x] Add a context budget so the CLI can decide how many files and characters to
  include without flooding the model.
- [x] Show a short "selected context" summary before answering.

### Presets

- [ ] Add `/preset code-review`.
- [ ] Add `/preset security-audit`.
- [ ] Add `/preset explain-project`.
- [ ] Add `/preset refactor-plan`.
- [ ] Add `/preset malware-analysis`.
- [ ] Allow user-defined presets in the config directory.

### Web mode

- [ ] Add `/web auto`, `/web on`, and `/web off`.
- [x] Show the search query chosen by the model.
- [x] Show which sources were used in the final answer.
- [x] Improve result extraction and citation formatting.
- [x] Add a clear warning when web search is enabled but no useful source was
  found.

### Sessions

- [ ] Add `/save <name>` to save the current conversation.
- [ ] Add `/load <name>` to restore a conversation.
- [ ] Add `/sessions` to list saved chats.
- [ ] Add `/delete-session <name>`.
- [ ] Store sessions locally without including API keys.

## Long Term

### Model profiles

- [ ] Add model aliases such as `fastest`, `coding`, `reasoning`, `vision`, and
  `balanced`.
- [ ] Track the last selected model and offer it first on the next run.
- [ ] Show model metadata when available, such as context window, provider, and
  modality.
- [ ] Add per-profile defaults for streaming, thinking display, and web mode.

### Packaging and releases

- [ ] Add `nvidia-chat --version`.
- [ ] Add GitHub Actions for linting and package checks.
- [ ] Publish installable releases.
- [ ] Support `pipx install nvidia-chat-cli`.
- [ ] Add release notes and changelog automation.

### Advanced developer workflows

- [ ] Add PR description generation from git diffs.
- [ ] Add issue triage prompts for pasted logs and stack traces.
- [x] Add project indexing for faster repeated folder analysis.
- [ ] Add optional local cache for web search snippets and model lists.
- [ ] Add export formats for Markdown, JSON, and plain text transcripts.

## Design Principles

- Keep the first run simple: ask for an API key, validate it, save it locally,
  then start chatting.
- Prefer readable terminal UI over dense raw output.
- Make large context behavior transparent: show what was loaded and what was
  skipped.
- Avoid sending dependency folders, cache folders, binary files, or secrets as
  context.
- Keep API keys and user sessions out of the repository.
- Favor focused commands that compose well instead of a complex menu-heavy
  interface.
