import sublime
import sublime_plugin
import requests
import json
import re
import os
import threading
import traceback
from .database import Database
from .api import GeminiAPIClient
from .tools_attach import (
    ATTACH_PATTERN, process_attachments,
    install_phantoms, _pending_attachments,
)
from .tools_write import (
    parse_tool_calls, describe_tool, execute_tool,
    DESTRUCTIVE_ACTIONS,
)

SYNTAX_MAP = {
    "python":     "Packages/Python/Python.sublime-syntax",
    "py":         "Packages/Python/Python.sublime-syntax",
    "lua":        "Packages/Lua/Lua.sublime-syntax",
    "java":       "Packages/Java/Java.sublime-syntax",
    "js":         "Packages/JavaScript/JavaScript.sublime-syntax",
    "javascript": "Packages/JavaScript/JavaScript.sublime-syntax",
    "ts":         "Packages/TypeScript/TypeScript.sublime-syntax",
    "typescript": "Packages/TypeScript/TypeScript.sublime-syntax",
    "html":       "Packages/HTML/HTML.sublime-syntax",
    "css":        "Packages/CSS/CSS.sublime-syntax",
    "json":       "Packages/JSON/JSON.sublime-syntax",
    "xml":        "Packages/XML/XML.sublime-syntax",
    "bash":       "Packages/ShellScript/Shell-Unix-Generic.sublime-syntax",
    "sh":         "Packages/ShellScript/Shell-Unix-Generic.sublime-syntax",
    "c":          "Packages/C++/C.sublime-syntax",
    "cpp":        "Packages/C++/C++.sublime-syntax",
    "cs":         "Packages/C#/C#.sublime-syntax",
    "csharp":     "Packages/C#/C#.sublime-syntax",
    "ruby":       "Packages/Ruby/Ruby.sublime-syntax",
    "rb":         "Packages/Ruby/Ruby.sublime-syntax",
    "go":         "Packages/Go/Go.sublime-syntax",
    "rust":       "Packages/Rust/Rust.sublime-syntax",
    "php":        "Packages/PHP/PHP.sublime-syntax",
    "sql":        "Packages/SQL/SQL.sublime-syntax",
    "swift":      "Packages/Swift/Swift.sublime-syntax",
    "kotlin":     "Packages/Kotlin/Kotlin.sublime-syntax",
    "markdown":   "Packages/Markdown/Markdown.sublime-syntax",
    "md":         "Packages/Markdown/Markdown.sublime-syntax",
}

INPUT_PROMPT = "you ❯ "


# ---------------------------------------------------------------------------
# View helpers
# ---------------------------------------------------------------------------

def get_gemini_response_view(window):
    for view in window.views():
        if view.name() == "Gemini Response":
            return view
    new_view = window.new_file()
    new_view.set_name("Gemini Response")
    new_view.set_scratch(True)
    _apply_console_style(new_view)
    return new_view


def _apply_console_style(view):
    plugin_dir = os.path.dirname(__file__)
    pkg_name = os.path.basename(plugin_dir).replace('.sublime-package', '')
    print(pkg_name)
    view.set_syntax_file("Packages/{}/GeminiConsole.sublime-syntax".format(pkg_name))
    view.settings().set("color_scheme",
        "Packages/{}/GeminiConsole.sublime-color-scheme".format(pkg_name))
    view.settings().set("auto_indent",              False)
    view.settings().set("smart_indent",             False)
    view.settings().set("tab_size",                 4)
    view.settings().set("translate_tabs_to_spaces", True)
    view.settings().set("word_wrap",                True)
    view.settings().set("line_numbers",             False)
    view.settings().set("gutter",                   False)
    view.settings().set("fold_buttons",             False)
    view.settings().set("draw_indent_guides",       False)
    view.settings().set("font_face",                "Monospace")
    view.settings().set("margin",                   8)
    view.settings().set("scroll_past_end",          False)


def _place_input_prompt(response_view, window):
    """Append the you ❯ prompt and move cursor there."""
    response_view.run_command("append", {"characters": INPUT_PROMPT})
    response_view.show(response_view.size())
    window.focus_view(response_view)
    response_view.run_command("move_to", {"to": "eof"})


# ---------------------------------------------------------------------------
# System prompt — cached, invalidated on settings change
# ---------------------------------------------------------------------------

_system_prompt_cache = [None]


def _build_system_prompt(settings):
    if _system_prompt_cache[0] is not None:
        return _system_prompt_cache[0]
    prompt = settings.get("system_prompt", "")
    _system_prompt_cache[0] = prompt
    return prompt


def _invalidate_prompt_cache():
    _system_prompt_cache[0] = None


_settings_obj = sublime.load_settings("gemini.sublime-settings")
_settings_obj.add_on_change("gemini_prompt_cache", _invalidate_prompt_cache)


def _make_client(settings):
    """Build a GeminiAPIClient from settings. api_key may be a string or list."""
    return GeminiAPIClient(
        settings.get("api_key", ""),
        settings.get("model", "gemini-2.5-flash"),
        settings.get("config", {})
    )


# ---------------------------------------------------------------------------
# Gemini context — loaded from gemini_context.md, injected once per conversation
# ---------------------------------------------------------------------------

def _load_gemini_context():
    """Load the unified context/rules/tools file. Returns string or empty."""
    path = os.path.join(os.path.dirname(__file__), "gemini_context.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Shared conversation panel builder
# ---------------------------------------------------------------------------

def _build_conversation_items(database):
    conversations = database.get_all_conversations_meta()
    items, ids = [], []
    for conv_id, first_msg, last_msg in conversations:
        title   = (first_msg or "").replace("\n", " ").strip() or "Empty conversation"
        preview = (last_msg  or "").replace("\n", " ").strip()
        items.append([
            "[{}]  {}".format(conv_id,
                title[:60] + "..." if len(title) > 60 else title),
            preview[:100] + "..." if len(preview) > 100 else preview
        ])
        ids.append(conv_id)
    return items, ids


# ---------------------------------------------------------------------------
# Gemini dispatch
# ---------------------------------------------------------------------------

def _dispatch_to_gemini(window, response_view, api_message, system_prompt,
                         gemini_client, db_path, conversation_id,
                         code_tab_threshold, images=None):
    database     = Database(db_path)
    database.save_message(conversation_id, "user", api_message)
    conversation = database.get_conversation(conversation_id)
    database.close()

    # Inject context as first user message once per conversation (not saved to DB)
    if len(conversation) == 1:
        context = _load_gemini_context()
        if context:
            conversation[0]["content"] = (
                context + "\n\n---\n\n" + conversation[0]["content"]
            )

    sublime.status_message("Gemini is thinking...")

    threading.Thread(
        target=_gemini_thread,
        args=(window, response_view, conversation, system_prompt,
              gemini_client, db_path, conversation_id, code_tab_threshold, images)
    ).start()


def _format_api_error(response_json):
    """
    Extract the useful parts from a Gemini API error response and return
    a clean, readable string instead of a raw JSON dump.
    Falls back to compact JSON if the structure is unexpected.
    """
    err = response_json.get("error", {})
    if not err:
        return json.dumps(response_json, indent=2)

    code    = err.get("code", "")
    status  = err.get("status", "")
    message = err.get("message", "").strip()

    # Build the header line
    parts = []
    if code and status:
        parts.append("[{}  {}]".format(code, status))
    elif code:
        parts.append("[{}]".format(code))

    if message:
        # The message often contains duplicate info after a newline — keep first paragraph
        first_para = message.split('\n')[0].strip()
        parts.append(first_para)

    # Pull out retry delay if present
    retry = None
    quota_info = []
    for detail in err.get("details", []):
        dtype = detail.get("@type", "")
        if "RetryInfo" in dtype:
            retry = detail.get("retryDelay", "")
        if "QuotaFailure" in dtype:
            for v in detail.get("violations", []):
                metric = v.get("quotaMetric", "").split("/")[-1]
                limit  = v.get("quotaValue", "")
                model  = v.get("quotaDimensions", {}).get("model", "")
                quota_info.append("  quota: {} / limit: {}{}".format(
                    metric, limit,
                    "  (model: {})".format(model) if model else ""))

    if retry:
        parts.append("retry after: {}".format(retry))
    if quota_info:
        parts.append("\n".join(quota_info))

    # Collect any help URLs
    urls = []
    for detail in err.get("details", []):
        for link in detail.get("links", []):
            url = link.get("url", "")
            if url:
                urls.append("  → {}".format(url))
    if urls:
        parts.append("\n".join(urls))

    return "\n".join(parts)


def _gemini_thread(window, response_view, conversation, system_prompt,
                    gemini_client, db_path, conversation_id,
                    code_tab_threshold, images=None):
    local_db = Database(db_path)
    try:
        result       = gemini_client.send_messages(system_prompt, conversation, images=images)
        swap_message = result.get("swap_message")
        response_json = result.get("response", result)  # fallback for old shape

        # Show key-swap notification inline before the response
        if swap_message:
            sublime.set_timeout(
                lambda: response_view.run_command("append",
                    {"characters": "{}\n\n".format(swap_message)}), 0)

        if "candidates" in response_json and response_json["candidates"]:
            candidate = response_json["candidates"][0]
            if "content" in candidate:
                text = candidate["content"]["parts"][0]["text"]
                local_db.save_message(conversation_id, "gemini", text)
                sublime.set_timeout(
                    lambda: _show_result(window, response_view, text, code_tab_threshold), 0)
            else:
                finish = candidate.get("finishReason", "UNKNOWN")
                msg = "Empty response (finishReason: {})".format(finish)
                if "error" in response_json:
                    msg = _format_api_error(response_json)
                sublime.set_timeout(lambda: _show_error(window, response_view, msg), 0)
        elif "error" in response_json:
            msg = _format_api_error(response_json)
            sublime.set_timeout(lambda: _show_error(window, response_view, msg), 0)
        else:
            msg = "Unexpected response shape:\n{}".format(
                json.dumps(response_json, indent=2))
            sublime.set_timeout(lambda: _show_error(window, response_view, msg), 0)

    except requests.exceptions.RequestException as e:
        sublime.set_timeout(
            lambda: _show_error(window, response_view,
                "Network error: {}".format(e)), 0)
    except Exception as e:
        sublime.set_timeout(
            lambda: _show_error(window, response_view,
                "Unexpected error: {}\n\n{}".format(e, traceback.format_exc())), 0)
    finally:
        sublime.set_timeout(lambda: sublime.status_message(""), 0)
        local_db.close()


def _show_result(window, response_view, text, code_tab_threshold):
    # Strip tool call blocks from display text before rendering
    from .tools_write import TOOL_PATTERN as _TP
    display = _TP.sub('', text).strip()
    display = process_response_code_blocks(window, display, threshold=code_tab_threshold)

    # Record where this block starts so we can place the fold triangle
    block_start = response_view.size()

    response_view.run_command("append", {"characters": "gemini ◆ {}\n\n".format(display)})
    response_view.show(response_view.size())

    # Install fold triangle phantom at the start of this block
    _install_fold_triangle(response_view, block_start)

    # Check for tool calls — show confirmation UI if any found
    tool_calls = parse_tool_calls(text)
    if tool_calls:
        _show_tool_confirmation(response_view, window, tool_calls)
    else:
        _place_input_prompt(response_view, window)


def _show_error(window, response_view, message):
    response_view.run_command("append", {"characters": "error ✖ {}\n\n".format(message)})
    response_view.show(response_view.size())
    _place_input_prompt(response_view, window)


# ---------------------------------------------------------------------------
# Fold triangle phantoms
# ---------------------------------------------------------------------------

# Keep phantom sets alive (Sublime GCs them otherwise)
_fold_phantom_sets = []

_TRIANGLE_OPEN   = "▼"
_TRIANGLE_CLOSED = "▶"

_TRIANGLE_HTML = (
    '<body id="gft" style="margin:0;padding:0;">'
    '<a href="fold:{pos}" style="color:{color};text-decoration:none;'
    'font-family:monospace;font-size:0.9em;padding-right:4px;">'
    '{symbol}</a></body>'
)


def _install_fold_triangle(view, block_start):
    """Install a clickable ▼/▶ phantom at the very start of a gemini block."""
    ps = sublime.PhantomSet(view, "gemini_fold_tri_{}".format(block_start))
    _fold_phantom_sets.append(ps)

    def _make_phantom(pos, folded=False):
        symbol = _TRIANGLE_CLOSED if folded else _TRIANGLE_OPEN
        color  = "#6e7681" if folded else "#79c0ff"
        html   = _TRIANGLE_HTML.format(pos=pos, color=color, symbol=symbol)
        return sublime.Phantom(
            sublime.Region(pos, pos),
            html,
            sublime.LAYOUT_INLINE,
            lambda href, v=view, p=pos, s=ps: _on_triangle_click(href, v, p, s)
        )

    ps.update([_make_phantom(block_start)])


def _on_triangle_click(href, view, block_start, phantom_set):
    """Toggle fold and swap the triangle symbol."""
    if not href.startswith("fold:"):
        return

    content = view.substr(sublime.Region(0, view.size()))

    # Build fold region same way as GeminiFoldResponseCommand
    MARKER        = "gemini ◆ "
    first_line_end = content.find('\n', block_start)
    if first_line_end == -1:
        return

    fold_start = first_line_end
    fold_end   = len(content)

    BLOCK_STARTS = (MARKER, INPUT_PROMPT, "error ✖ ", "❯ SESSION ", "─" * 10)
    for marker in BLOCK_STARTS:
        idx = content.find(marker, first_line_end + 1)
        if idx != -1 and idx < fold_end:
            nb = content.rfind('\n', first_line_end, idx)
            fold_end = nb if nb != -1 else idx

    if fold_end <= fold_start:
        return

    region = sublime.Region(fold_start, fold_end)
    folded = not view.unfold(region)   # unfold returns True if something was unfolded
    if folded:
        view.fold(region)

    # Swap triangle symbol
    symbol = _TRIANGLE_CLOSED if folded else _TRIANGLE_OPEN
    color  = "#6e7681" if folded else "#79c0ff"
    html   = _TRIANGLE_HTML.format(pos=block_start, color=color, symbol=symbol)
    phantom_set.update([sublime.Phantom(
        sublime.Region(block_start, block_start),
        html,
        sublime.LAYOUT_INLINE,
        lambda h, v=view, p=block_start, ps=phantom_set: _on_triangle_click(h, v, p, ps)
    )])


# ---------------------------------------------------------------------------
# Tool confirmation UI
# ---------------------------------------------------------------------------

def _show_tool_confirmation(response_view, window, tool_calls):
    """Display pending tool calls and ask for confirmation inline."""
    lines = ["⚙️  Gemini wants to perform {} action{}:\n".format(
        len(tool_calls), "s" if len(tool_calls) > 1 else "")]

    for i, tool in enumerate(tool_calls):
        lines.append("  [{}] {}".format(i + 1, describe_tool(tool)))

    lines.append("\nType y · n · or skip 1,2 to skip specific items\n")

    response_view.run_command("append", {"characters": "\n".join(lines) + "\n"})
    response_view.show(response_view.size())

    # Store pending tool calls as JSON in view settings
    response_view.settings().set("pending_tool_calls", json.dumps(tool_calls))
    _place_input_prompt(response_view, window)


# ---------------------------------------------------------------------------
# Code block side-tab logic
# ---------------------------------------------------------------------------

def ensure_two_column_layout(window):
    if window.num_groups() >= 2:
        return
    window.set_layout({
        "cols": [0.0, 0.5, 1.0],
        "rows": [0.0, 1.0],
        "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
    })


def open_code_in_side_tab(window, code, lang, snippet_index, tab_label=None):
    ensure_two_column_layout(window)
    # Use explicit label (e.g. filename from <gemfile path="...">) or fallback
    if tab_label:
        tab_name = "Gemini: {}".format(tab_label)
    else:
        tab_name = "Gemini: {} snippet #{}".format(lang if lang else "code", snippet_index)

    code_view = None
    for view in window.views():
        if view.name() == tab_name:
            code_view = view
            code_view.run_command("select_all")
            code_view.run_command("right_delete")
            break

    if code_view is None:
        code_view = window.new_file()
        code_view.set_name(tab_name)
        code_view.set_scratch(True)
        window.set_view_index(code_view, 1, len(window.views_in_group(1)))

    code_view.settings().set("auto_indent",                False)
    code_view.settings().set("smart_indent",               False)
    code_view.settings().set("indent_to_bracket",          False)
    code_view.settings().set("trim_automatic_white_space", False)

    syntax = SYNTAX_MAP.get(lang.lower() if lang else "", "")
    if syntax:
        try:
            code_view.set_syntax_file(syntax)
        except Exception:
            pass

    code_view.run_command("append", {"characters": code.strip()})
    return tab_name


def process_response_code_blocks(window, response_text, threshold=60):
    """
    Parse Gemini's response and open side tabs where appropriate.

    Two-pass approach:
      Pass 1 — <gemfile> tags: Gemini explicitly marks complete files or
               large reusable snippets. These always open in a side tab.
               The inner fenced block is extracted; the whole <gemfile>…</gemfile>
               is replaced with a reference line.

      Pass 2 — unclosed fences: if the response was cut off mid-fence,
               extract what arrived into a side tab.

      Everything else (plain ``` fences) is left completely untouched so
      inline examples, commands, and short illustrations stay in the console.

    The `threshold` is kept as a safety-net fallback (default 60) for plain
    fences only when Gemini forgets to tag — avoids enormous inline blocks.
    """
    counter = [0]

    # ------------------------------------------------------------------
    # Pass 1 — explicit <gemfile> tags
    # Format:
    #   <gemfile path="optional/path.lua">
    #   ```lang
    #   ...code...
    #   ```
    #   </gemfile>
    # ------------------------------------------------------------------
    gemfile_pattern = re.compile(
        r'<gemfile([^>]*)>\s*```(\w*)\n(.*?)```\s*</gemfile>',
        re.DOTALL
    )

    def handle_gemfile(match):
        attrs   = match.group(1).strip()
        lang    = match.group(2).strip()
        code    = match.group(3)
        lines   = len(code.splitlines())
        counter[0] += 1

        # Extract path attribute if present
        path_match = re.search(r'path=["\']([^"\']+)["\']', attrs)
        label = path_match.group(1) if path_match else "{} snippet #{}".format(
            lang if lang else "code", counter[0])

        tab = open_code_in_side_tab(window, code, lang, counter[0],
                                     tab_label=label)
        return "📄 *`{}` ({} lines) — opened in side tab \"{}\"*".format(
            label, lines, tab)

    result = gemfile_pattern.sub(handle_gemfile, response_text)

    # ------------------------------------------------------------------
    # Pass 2 — safety-net for plain fences over threshold
    # Only fires if Gemini forgot to tag. High default (60) means it
    # rarely triggers for normal conversational code.
    # ------------------------------------------------------------------
    fence_pattern = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)

    def handle_plain_fence(match):
        lang  = match.group(1).strip()
        code  = match.group(2)
        lines = len(code.splitlines())
        if lines >= threshold:
            counter[0] += 1
            tab = open_code_in_side_tab(window, code, lang, counter[0])
            return "📄 *Code snippet ({} lines) — opened in side tab \"{}\"*".format(
                lines, tab)
        return match.group(0)   # leave short blocks inline

    result = fence_pattern.sub(handle_plain_fence, result)

    # ------------------------------------------------------------------
    # Pass 3 — unclosed fence (response cut off by API)
    # ------------------------------------------------------------------
    unclosed = re.search(r'```(\w*)\n(.+)', result, re.DOTALL)
    if unclosed:
        lang   = unclosed.group(1).strip()
        code   = unclosed.group(2)
        lines  = len(code.splitlines())
        counter[0] += 1
        tab    = open_code_in_side_tab(window, code, lang, counter[0])
        result = result[:unclosed.start()] + \
            "📄 *Code snippet ({} lines, response cut off) — opened in side tab \"{}\"*".format(
                lines, tab)

    return result


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def start_new_conversation_in_db(database):
    cursor = database.conn.cursor()
    cursor.execute("INSERT INTO conversations DEFAULT VALUES")
    database.conn.commit()
    return cursor.lastrowid


def getlang(view):
    s_p = view.settings().get('syntax')
    if s_p:
        l_s   = s_p.rfind('/')
        n_w_e = s_p[l_s + 1:] if l_s != -1 else s_p
        l_d   = n_w_e.rfind('.')
        return n_w_e[:l_d] if l_d != -1 else n_w_e
    return ""


# ---------------------------------------------------------------------------
# GeminiSetContentCommand
# ---------------------------------------------------------------------------

class GeminiSetContentCommand(sublime_plugin.TextCommand):
    """Replace entire view content, targeting the specific view object."""
    def run(self, edit, characters=""):
        self.view.replace(edit, sublime.Region(0, self.view.size()), characters)


# ---------------------------------------------------------------------------
# GeminiCodeAssistantCommand (Alt+G — also handles selected code attachment)
# ---------------------------------------------------------------------------

class GeminiCodeAssistantCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        settings = sublime.load_settings("gemini.sublime-settings")
        self.system_prompt      = _build_system_prompt(settings)
        self.db_path            = settings.get("db_path",
                                    sublime.packages_path() + "/User/gemini_db.db")
        self.code_tab_threshold = settings.get("code_tab_threshold", 15)
        self.gemini_client      = _make_client(settings)

        self.database      = Database(self.db_path)
        self.response_view = get_gemini_response_view(self.view.window())

        if self.response_view.settings().get("conversation_id"):
            self.conversation_id = self.response_view.settings().get("conversation_id")
        else:
            self.conversation_id = start_new_conversation_in_db(self.database)
            self.response_view.settings().set("conversation_id", self.conversation_id)
            self.response_view.run_command("append", {"characters": "❯ SESSION #{}\n{}\n\n".format(
                self.conversation_id, "─" * 60)})
            # Place prompt immediately so the view looks ready
            _place_input_prompt(self.response_view, self.view.window())

        sel           = self.view.sel()
        selected_text = self.view.substr(sel[0]) if len(sel) > 0 and not sel[0].empty() else ""
        lang          = getlang(self.view)
        initial       = "Selected code:\n```{}\n{}\n```\nYour question:\n\t ".format(
            lang, selected_text) if selected_text else ""
        self.view.window().show_input_panel("Ask Gemini:", initial, self.on_done, None, None)

    def on_done(self, user_input):
        window  = self.view.window()
        results, text_context, images, matches = process_attachments(user_input, window)

        clean       = ATTACH_PATTERN.sub('', user_input).strip()
        api_message = (clean + "\n\n" + text_context).strip() if text_context else clean

        # The response view already ends with "you ❯ " from _place_input_prompt.
        # Just append the user's typed text after it — no extra "you ❯" prefix.
        self.response_view.run_command("append", {"characters": user_input + "\n\n"})
        self.response_view.show(self.response_view.size())

        if matches:
            content     = self.response_view.substr(
                sublime.Region(0, self.response_view.size()))
            last_prompt = content.rfind(INPUT_PROMPT)
            msg_start   = last_prompt + len(INPUT_PROMPT)
            key = "{}:{}".format(self.response_view.id(), msg_start)
            _pending_attachments[key] = (results, matches, window)
            self.response_view.run_command("gemini_install_attachments",
                                           {"key": key, "msg_start": msg_start})

        for r in results:
            if r.warning:
                self.response_view.run_command("append",
                    {"characters": "error ✖ {}\n\n".format(r.warning)})

        _dispatch_to_gemini(
            window, self.response_view, api_message,
            self.system_prompt, self.gemini_client,
            self.db_path, self.conversation_id,
            self.code_tab_threshold, images
        )


# ---------------------------------------------------------------------------
# GeminiConsoleSubmitCommand — Enter in the console view
# ---------------------------------------------------------------------------

class GeminiConsoleSubmitCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        content  = self.view.substr(sublime.Region(0, self.view.size()))
        last_idx = content.rfind(INPUT_PROMPT)
        if last_idx == -1:
            return

        prompt_end = last_idx + len(INPUT_PROMPT)
        user_input = content[prompt_end:].strip()
        if not user_input:
            return

        window = self.view.window()

        # ── Confirmation mode ────────────────────────────────────────────────
        pending_json = self.view.settings().get("pending_tool_calls")
        if pending_json:
            self.view.replace(edit, sublime.Region(prompt_end, self.view.size()),
                              user_input + "\n\n")
            self._handle_confirmation(edit, user_input, pending_json, window)
            return

        # ── Normal message mode ──────────────────────────────────────────────
        settings = sublime.load_settings("gemini.sublime-settings")
        results, text_context, images, matches = process_attachments(user_input, window)

        clean       = ATTACH_PATTERN.sub('', user_input).strip()
        api_message = (clean + "\n\n" + text_context).strip() if text_context else clean

        self.view.replace(edit, sublime.Region(prompt_end, self.view.size()),
                          user_input + "\n\n")
        if matches:
            install_phantoms(self.view, edit, prompt_end, results, matches, window)

        for r in results:
            if r.warning:
                self.view.run_command("append",
                    {"characters": "error ✖ {}\n\n".format(r.warning)})

        conversation_id = self.view.settings().get("conversation_id")
        if not conversation_id:
            self.view.run_command("append", {"characters":
                "error ✖ No active session — use Alt+G to start one.\n\n"})
            _place_input_prompt(self.view, window)
            return

        _dispatch_to_gemini(
            window, self.view, api_message,
            _build_system_prompt(settings),
            _make_client(settings),
            settings.get("db_path", sublime.packages_path() + "/User/gemini_db.db"),
            conversation_id,
            settings.get("code_tab_threshold", 15),
            images
        )

    def _handle_confirmation(self, edit, user_input, pending_json, window):
        """Process y / n / skip N responses to the tool confirmation prompt."""
        settings = sublime.load_settings("gemini.sublime-settings")
        text     = user_input.strip().lower()

        try:
            tool_calls = json.loads(pending_json)
        except Exception:
            self.view.settings().erase("pending_tool_calls")
            _place_input_prompt(self.view, window)
            return

        n = len(tool_calls)

        if text == 'y':
            run_indices = list(range(n))
        elif text == 'n':
            run_indices = []
        elif text.startswith('skip '):
            skip_nums = []
            for x in text[5:].replace(',', ' ').split():
                if x.isdigit():
                    skip_nums.append(int(x) - 1)
            run_indices = [i for i in range(n) if i not in skip_nums]
        else:
            self.view.run_command("append", {"characters":
                "error ✖ Type y, n, or skip 1,2\n\n"})
            _place_input_prompt(self.view, window)
            return

        project_root = window.folders()[0] if window.folders() else None
        results_lines = []

        for i, tool in enumerate(tool_calls):
            if i in run_indices:
                ok, msg = execute_tool(tool, settings, project_root, window)
                icon = "✅" if ok else "❌"
            else:
                from .tools_write import add_feedback
                add_feedback('skipped', describe_tool(tool))
                icon = "⏭"
                msg  = "skipped — {}".format(describe_tool(tool))
            results_lines.append("  {} {}".format(icon, msg))

        self.view.run_command("append", {"characters":
            "\n".join(results_lines) + "\n\n"})
        self.view.settings().erase("pending_tool_calls")
        self.view.show(self.view.size())
        _place_input_prompt(self.view, window)

    def is_enabled(self):
        return self.view.name() == "Gemini Response"


# ---------------------------------------------------------------------------
# GeminiNewConversationCommand
# ---------------------------------------------------------------------------

class GeminiNewConversationCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        settings = sublime.load_settings("gemini.sublime-settings")
        db_path  = settings.get("db_path", sublime.packages_path() + "/User/gemini_db.db")
        database = Database(db_path)

        response_view = get_gemini_response_view(self.view.window())
        new_id        = start_new_conversation_in_db(database)

        _apply_console_style(response_view)
        response_view.run_command("gemini_set_content", {
            "characters": "❯ SESSION #{}\n{}\n\n".format(new_id, "─" * 60)
        })
        response_view.settings().set("conversation_id", new_id)
        response_view.settings().erase("pending_tool_calls")
        _place_input_prompt(response_view, self.view.window())


# ---------------------------------------------------------------------------
# GeminiOpenConversationCommand
# ---------------------------------------------------------------------------

class GeminiOpenConversationCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        settings      = sublime.load_settings("gemini.sublime-settings")
        db_path       = settings.get("db_path", sublime.packages_path() + "/User/gemini_db.db")
        self.database = Database(db_path)

        items, ids = _build_conversation_items(self.database)
        if not items:
            sublime.status_message("No previous conversations found.")
            return

        self.conversation_ids = ids
        self.view.window().show_quick_panel(items, self._on_selected)

    def _on_selected(self, index):
        if index == -1:
            return
        conv_id = self.conversation_ids[index]
        msgs    = self.database.get_conversation(conv_id)

        text = "❯ SESSION #{}\n{}\n\n".format(conv_id, "─" * 60)
        for msg in msgs:
            text += "you ❯ {}\n\n".format(msg['content']) if msg['role'] == 'user' \
                else "gemini ◆ {}\n\n".format(msg['content'])

        response_view = get_gemini_response_view(self.view.window())
        _apply_console_style(response_view)
        response_view.run_command("gemini_set_content", {"characters": text})
        response_view.settings().set("conversation_id", conv_id)
        response_view.settings().erase("pending_tool_calls")
        _place_input_prompt(response_view, self.view.window())


# ---------------------------------------------------------------------------
# GeminiDeleteConversationCommand
# ---------------------------------------------------------------------------

class GeminiDeleteConversationCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        settings      = sublime.load_settings("gemini.sublime-settings")
        db_path       = settings.get("db_path", sublime.packages_path() + "/User/gemini_db.db")
        self.database = Database(db_path)

        items, ids = _build_conversation_items(self.database)
        if not items:
            sublime.status_message("No conversations to delete.")
            return

        self.conversation_ids = ids
        self.view.window().show_quick_panel(items, self._on_selected)

    def _on_selected(self, index):
        if index == -1:
            return
        conv_id = self.conversation_ids[index]
        self.database.delete_conversation(conv_id)

        response_view = get_gemini_response_view(self.view.window())
        if response_view.settings().get("conversation_id") == conv_id:
            response_view.settings().erase("conversation_id")
            response_view.settings().erase("pending_tool_calls")
            response_view.run_command("gemini_set_content", {
                "characters": "error ✖ Session #{} was deleted\n{}\n\n".format(
                    conv_id, "─" * 60)
            })

        sublime.status_message("Conversation {} deleted.".format(conv_id))
        self.database.close()


# ---------------------------------------------------------------------------
# GeminiCopyLastResponseCommand
# ---------------------------------------------------------------------------

class GeminiCopyLastResponseCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        response_view = get_gemini_response_view(self.view.window())
        content       = response_view.substr(sublime.Region(0, response_view.size()))

        marker   = "gemini ◆ "
        last_idx = content.rfind(marker)
        if last_idx == -1:
            sublime.status_message("No Gemini response found to copy.")
            return

        response_start = last_idx + len(marker)
        next_idx       = content.find("\n" + INPUT_PROMPT, response_start)
        response_end   = next_idx if next_idx != -1 else len(content)

        sublime.set_clipboard(content[response_start:response_end].strip())
        sublime.status_message("Last Gemini response copied to clipboard!")


# ---------------------------------------------------------------------------
# GeminiFoldResponseCommand — fold / unfold the gemini response at cursor
# ---------------------------------------------------------------------------

class GeminiFoldResponseCommand(sublime_plugin.TextCommand):
    """
    Fold or unfold the Gemini response block nearest to the cursor.
    The fold region covers everything after the first line of `gemini ◆ ...`
    up to (but not including) the next prompt marker or end of view.

    Folded it shows as:  gemini ◆ [first line of response] ···
    """
    # Markers that signal the start of a new block
    BLOCK_STARTS = ("gemini ◆ ", INPUT_PROMPT, "you ❯ ",
                    "error ✖ ", "❯ SESSION ", "─" * 10)

    def run(self, edit):
        content = self.view.substr(sublime.Region(0, self.view.size()))
        cursor  = self.view.sel()[0].begin() if self.view.sel() else 0

        fold_region = self._find_fold_region(content, cursor)
        if fold_region is None:
            sublime.status_message("No Gemini response to fold here.")
            return

        # Toggle: unfold if already folded, fold if not
        if self.view.unfold(fold_region):
            pass  # was folded — now unfolded
        else:
            self.view.fold(fold_region)

    def _find_fold_region(self, content, cursor):
        """
        Find the gemini ◆ block that contains or is nearest before the cursor.
        Returns a Region covering lines 2..end of that block, or None.
        """
        MARKER = "gemini ◆ "

        # Find the start of the gemini block that owns the cursor position.
        # Walk backwards from cursor to find the last "gemini ◆ " before it.
        search_area = content[:cursor + 1]
        block_start = search_area.rfind(MARKER)

        # If cursor is before any gemini block, try the first one after cursor
        if block_start == -1:
            block_start = content.find(MARKER)
        if block_start == -1:
            return None

        # The fold starts after the first line of the gemini block
        # (we keep the first line visible as the fold summary)
        first_line_end = content.find('\n', block_start)
        if first_line_end == -1:
            return None  # single-line response, nothing to fold
        fold_start = first_line_end  # keep the \n so line stays terminated

        # The fold ends just before the next block-start marker
        fold_end = len(content)
        for marker in self.BLOCK_STARTS:
            idx = content.find(marker, first_line_end + 1)
            if idx != -1 and idx < fold_end:
                # End just before the preceding \n so blank line stays outside
                newline_before = content.rfind('\n', first_line_end, idx)
                fold_end = newline_before if newline_before != -1 else idx

        if fold_end <= fold_start:
            return None

        return sublime.Region(fold_start, fold_end)

    def is_enabled(self):
        return self.view.name() == "Gemini Response"




class GeminiMoveFileCommand(sublime_plugin.WindowCommand):
    def run(self, paths=None):
        if not paths:
            sublime.status_message("No file selected.")
            return
        self.src_path = paths[0]
        rel = self._rel(self.src_path)
        self.window.show_input_panel(
            "Move / Rename (path relative to project root):",
            rel,
            self.on_done, None, None
        )

    def on_done(self, dest):
        settings     = sublime.load_settings("gemini.sublime-settings")
        project_root = self.window.folders()[0] if self.window.folders() else None

        if not settings.get("allow_move_file", True):
            sublime.status_message("Move is disabled in Gemini settings.")
            return

        from .tools_write import op_move_file
        src_rel = self._rel(self.src_path)
        ok, msg = op_move_file(src_rel, dest, project_root)
        sublime.status_message(msg)

    def _rel(self, path):
        folders = self.window.folders()
        if folders:
            root = folders[0]
            if path.startswith(root):
                return path[len(root):].lstrip(os.sep)
        return os.path.basename(path)

    def is_visible(self, paths=None):
        return bool(paths)