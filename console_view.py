import sublime
import sublime_plugin
import collections


INPUT_PROMPT = "you ❯ "

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

_fold_phantom_sets = collections.deque(maxlen=200)

_TRIANGLE_OPEN   = "▼"
_TRIANGLE_CLOSED = "▶"

_TRIANGLE_HTML = (
    '<body id="gft" style="margin:0;padding:0;">'
    '<a href="fold:{pos}" style="color:{color};text-decoration:none;'
    'font-family:monospace;font-size:0.9em;padding-right:4px;">'
    '{symbol}</a></body>'
)


def get_gemini_response_view(window):
    for view in window.views():
        if view.name() == "Gemini Response":
            return view
    new_view = window.new_file()
    new_view.set_name("Gemini Response")
    new_view.set_scratch(True)
    _apply_console_settings(new_view)
    return new_view


def _apply_console_settings(view):
    view.set_syntax_file("Packages/GeminiAssistant/GeminiConsole.sublime-syntax")
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
    response_view.run_command("append", {"characters": INPUT_PROMPT})
    response_view.show(response_view.size())
    if window.active_view() == response_view:
        window.focus_view(response_view)
    response_view.run_command("move_to", {"to": "eof"})


def _install_fold_triangle(view, block_start):
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
    if not href.startswith("fold:"):
        return

    content = view.substr(sublime.Region(0, view.size()))

    MARKER         = "gemini ◆ "
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
    folded = not view.unfold(region)
    if folded:
        view.fold(region)

    symbol = _TRIANGLE_CLOSED if folded else _TRIANGLE_OPEN
    color  = "#6e7681" if folded else "#79c0ff"
    html   = _TRIANGLE_HTML.format(pos=block_start, color=color, symbol=symbol)
    phantom_set.update([sublime.Phantom(
        sublime.Region(block_start, block_start),
        html,
        sublime.LAYOUT_INLINE,
        lambda h, v=view, p=block_start, ps=phantom_set: _on_triangle_click(h, v, p, ps)
    )])


class GeminiSetContentCommand(sublime_plugin.TextCommand):
    def run(self, edit, characters=""):
        self.view.replace(edit, sublime.Region(0, self.view.size()), characters)


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


class GeminiFoldResponseCommand(sublime_plugin.TextCommand):
    BLOCK_STARTS = ("gemini ◆ ", INPUT_PROMPT, "you ❯ ",
                    "error ✖ ", "❯ SESSION ", "─" * 10)

    def run(self, edit):
        content = self.view.substr(sublime.Region(0, self.view.size()))
        cursor  = self.view.sel()[0].begin() if self.view.sel() else 0

        fold_region = self._find_fold_region(content, cursor)
        if fold_region is None:
            sublime.status_message("No Gemini response to fold here.")
            return

        if self.view.unfold(fold_region):
            pass
        else:
            self.view.fold(fold_region)

    def _find_fold_region(self, content, cursor):
        MARKER      = "gemini ◆ "
        search_area = content[:cursor + 1]
        block_start = search_area.rfind(MARKER)

        if block_start == -1:
            block_start = content.find(MARKER)
        if block_start == -1:
            return None

        first_line_end = content.find('\n', block_start)
        if first_line_end == -1:
            return None
        fold_start = first_line_end

        fold_end = len(content)
        for marker in self.BLOCK_STARTS:
            idx = content.find(marker, first_line_end + 1)
            if idx != -1 and idx < fold_end:
                newline_before = content.rfind('\n', first_line_end, idx)
                fold_end = newline_before if newline_before != -1 else idx

        if fold_end <= fold_start:
            return None

        return sublime.Region(fold_start, fold_end)

    def is_enabled(self):
        return self.view.name() == "Gemini Response"
