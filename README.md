# Gemini Assistant for Sublime Text

A Google Gemini AI assistant integrated directly into Sublime Text as an interactive console. Ask questions, attach files, generate code, and let Gemini manage your project — all without leaving your editor.

---

## NOTE
this package is still highly experimental few bugs are still present however it still works ok 
i hope i will be able to fix em as soon as possible 
## Features

**Interactive console** — a dedicated terminal-style panel where you type naturally and hit Enter to send. No popups, no input panels after the first message. Responses appear inline with syntax-aware formatting.

**Side-by-side code tabs** — complete files and large snippets open automatically in a side tab with proper syntax highlighting, keeping the console readable. Short examples and commands stay inline.

**File attachments** — attach project files, images, clipboard content, selections, build logs, git diffs and more using the `+\`name\`` syntax directly in your message.

**Write tools** — Gemini can create files, edit files, patch functions, move, rename and soft-delete files, create projects, run builds, and execute git commands. Every action goes through a confirmation step before touching disk.

**Multi-key rotation** — provide multiple Gemini API keys and the plugin rotates automatically when one hits its quota, notifying you inline without interrupting the conversation.

**Conversation history** — all conversations are stored in a local SQLite database. Browse, resume, or delete past sessions from the right-click menu or command palette.

**Fold/unfold responses** — click the `▼` triangle next to any response or press Tab to collapse it. Useful for long conversations.

---

## Installation

### Via Package Control *(recommended)*

1. Open the Command Palette (`Ctrl+Shift+P`)
2. Run `Package Control: Install Package`
3. Search for `Gemini Assistant`

### Manual

```bash
cd ~/.config/sublime-text/Packages   # Linux
# cd ~/Library/Application Support/Sublime Text/Packages   # macOS
# cd %APPDATA%\Sublime Text\Packages                       # Windows

git clone https://github.com/radda-ui/GeminiAssistant "GeminiAssistant"
```

Then restart Sublime Text.

---

## Setup

1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey) — it's free.
2. Open settings: `Preferences > Package Settings > Gemini Assistant > Settings`
3. Set your API key:

```json
{
    "api_key": ["YOUR_API_KEY"]
}
```

Multiple keys for automatic rotation:
```json
{
    "api_key": [
        "AIza...key1",
        "AIza...key2"
    ]
}
```

---

## Usage

### Starting a conversation

Press **Alt+G** from any file to open the console and start typing. The console appears on the right side of your editor. Hit **Enter** to send.

To attach selected code to your first message, select some code first then press **Alt+G** — it pre-fills the question with the selection.

### Attaching context with `+\`name\``

Type `+\`name\`` anywhere in your message to attach context before sending:

| Syntax | What it attaches |
|---|---|
| `+\`player.lua\`` | File from your project |
| `+\`screenshot.png\`` | Image (sent to Gemini's vision) |
| `+\`project\`` | Project folder tree |
| `+\`selection\`` | Current editor selection |
| `+\`open\`` | All open file tabs |
| `+\`console\`` | Latest build output |
| `+\`diff\`` | Unstaged git changes |
| `+\`log\`` | Recent git commits |
| `+\`clipboard\`` | Clipboard contents |
| `+\`feedback\`` | Results of last Gemini tool actions |

Attached files appear as clickable badges in the console. Click a badge to open the file or preview the content.

### Code in responses

Gemini uses two formats:

- **Plain fences** — short examples, commands, and illustrations stay inline in the console
- **`<gemfile>` tags** — complete files and large snippets open in a side tab automatically

### Keyboard shortcuts

| Key | Action |
|---|---|
| **Enter** | Send message (in console) |
| **Tab** | Fold / unfold response at cursor (in console) |
| **Alt+G** | Open Ask Gemini panel (any view) |
| **Alt+N** | New conversation |

### Right-click menu

Right-clicking anywhere opens a **Gemini** submenu with: New Conversation, Open Conversation, Delete Conversation, Copy Last Response, Move / Rename File.

---

## Write Tools

When Gemini needs to create or modify files it emits tool calls that the plugin intercepts. You see a confirmation list like:

```
⚙️  Gemini wants to perform 3 actions:

  [1] 📁 mkdir        src/
  [2] 📝 create       src/player.lua
  [3] 📝 create       src/enemy.lua

Type y · n · or skip 1,2 to skip specific items
```

Type `y` to approve all, `n` to cancel all, or `skip 2` to skip specific items.

Deleted files are moved to a `_deleted/` folder in your project root rather than permanently removed. To restore, use the sidebar rename to move the file back.

### Permissions

Write tools are controlled per-category in settings. Most are enabled by default. Network access (`allow_download_file`) and build execution (`allow_run_build`) are off by default. Git push is intentionally not implemented.

---

## Settings Reference

```jsonc
{
    // API key — string or list for multi-key rotation
    "api_key": ["YOUR_API_KEY"],

    // Gemini model to use
    "model": "gemini-2.5-flash",

    // Plain fenced blocks open in a side tab only above this line count
    // (safety net — <gemfile> tags are the primary mechanism)
    "code_tab_threshold": 60,

    // Your system prompt — code rules and tool formats are
    // appended automatically from gemini_context.md
    "system_prompt": "You are a helpful AI coding assistant...",

    // Always confirm before non-destructive actions (create, append, mkdir)
    // Destructive actions always confirm regardless of this setting
    "confirm_writes": true,

    // File operation permissions
    "allow_create_file":    true,
    "allow_edit_file":      true,
    "allow_patch_file":     true,
    "allow_append_file":    true,
    "allow_move_file":      true,
    "allow_delete_file":    true,
    "allow_create_folder":  true,
    "allow_create_project": true,
    "allow_download_file":  false,
    "allow_run_build":      false,

    // Git — local only, push is not implemented
    "allow_git":            false,
    "allow_git_pull":       false
}
```

---

## Requirements

- Sublime Text 4
- Python 3.3+ (bundled with Sublime Text)
- A [Google Gemini API key](https://aistudio.google.com/app/apikey)

---

## License

MIT
