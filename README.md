# Gemini Assistant for Sublime Text

A Google Gemini AI assistant integrated directly into Sublime Text as an interactive console. Ask questions, get code explanations, and engage in a smooth, project-aware conversational experience — all without leaving your editor.

---

## Features

**Interactive console** — a dedicated terminal-style panel where you type naturally and hit Enter to send. Responses appear inline with syntax-aware formatting.

**Side-by-side code tabs** — complete files and large snippets open automatically in a side tab with proper syntax highlighting, keeping the console readable.

**Multi-key rotation** — provide multiple Gemini API keys and the plugin rotates automatically when one hits its quota, notifying you inline without interrupting the conversation.

**Conversation history** — all conversations are stored in a local SQLite database. Browse, resume, or delete past sessions from the right-click menu or command palette.

**Fold/unfold responses** — click the `▼` triangle next to any response or press Tab to collapse it.

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

Press **Alt+G** from any file to open the console and start typing. Hit **Enter** to send.

### Keyboard shortcuts

| Key | Action |
|---|---|
| **Enter** | Send message (in console) |
| **Tab** | Fold / unfold response at cursor (in console) |
| **Alt+G** | Open Ask Gemini panel (any view) |
| **Alt+N** | New conversation |

---

## Settings Reference

```jsonc
{
    // API key — string or list for multi-key rotation
    "api_key": ["YOUR_API_KEY"],

    // Gemini model to use
    "model": "gemini-2.5-flash",

    // Plain fenced blocks open in a side tab only above this line count
    "code_tab_threshold": 60,

    // Your system prompt
    "system_prompt": "You are a helpful AI coding assistant..."
}
```

---

## Requirements

- Sublime Text 4
- Python 3.8 (bundled with Sublime Text)
- A [Google Gemini API key](https://aistudio.google.com/app/apikey)

---

## License

MIT
