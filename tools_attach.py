import re
import os
import sublime
import sublime_plugin
from .tools_read import (
    SKIP_DIRS, IMAGE_EXTENSIONS,
    read_text_file, read_image_file,
    get_project_tree, get_build_log, get_clipboard,
    get_selection, get_open_files, get_git_diff, get_git_log,
)
from .tools_write import get_feedback_text

# Matches +`anything` in a message
ATTACH_PATTERN = re.compile(r'\+`([^`]+)`')

# Keeps PhantomSet objects alive — Sublime GCs them if nothing holds a reference
_persistent_phantom_sets = []

# Mailbox for passing attachment data into GeminiInstallAttachmentsCommand
_pending_attachments = {}

PHANTOM_BADGE = (
    '<body id="ga" style="margin:0;padding:0;">'
    '<a href="{href}" style="color:#79c0ff;text-decoration:none;'
    'font-family:monospace;font-size:0.85em;background:#161b22;'
    'padding:1px 6px;border-radius:3px;border:1px solid #30363d;">'
    '📎 {label}</a></body>'
)
PHANTOM_WARN = (
    '<body id="ga-w" style="margin:0;padding:0;">'
    '<a href="warn:" style="color:#f85149;text-decoration:none;'
    'font-family:monospace;font-size:0.85em;background:#161b22;'
    'padding:1px 6px;border-radius:3px;border:1px solid #f85149;">'
    '⚠️ {label}</a></body>'
)

# Single dispatch table — one source of truth for all virtual keywords
_VIRTUAL = {
    'console':   ('console',    lambda w: get_build_log(w)),
    'build':     ('console',    lambda w: get_build_log(w)),
    'clipboard': ('clipboard',  lambda w: get_clipboard()),
    'selection': ('selection',  lambda w: get_selection(w)),
    'open':      ('open files', lambda w: get_open_files(w)),
    'project':   ('project',    lambda w: get_project_tree(w)),
    'diff':      ('git diff',   lambda w: get_git_diff(w.folders())),
    'log':       ('git log',    lambda w: get_git_log(w.folders())),
    'feedback':  ('feedback',   lambda w: get_feedback_text()),
}


# ---------------------------------------------------------------------------
# Attachment data container
# ---------------------------------------------------------------------------

class AttachmentResult:
    def __init__(self, name, display_label, text_content=None,
                 image_data=None, image_mime=None,
                 preview_content=None, filepath=None, warning=None):
        self.name            = name
        self.display_label   = display_label
        self.text_content    = text_content
        self.image_data      = image_data
        self.image_mime      = image_mime
        self.preview_content = preview_content
        self.filepath        = filepath
        self.warning         = warning


# ---------------------------------------------------------------------------
# File finder
# ---------------------------------------------------------------------------

def _find_file_in_project(name, window):
    matches = []
    for folder in window.folders():
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs
                       if not d.startswith('.') and d not in SKIP_DIRS]
            if name in files:
                matches.append(os.path.join(root, name))
    return matches


# ---------------------------------------------------------------------------
# Resolution — single dispatch, no duplication
# ---------------------------------------------------------------------------

def _resolve(name, window):
    n = name.lower()

    if n in _VIRTUAL:
        label, reader = _VIRTUAL[n]
        content, err  = reader(window)
        if err:
            return AttachmentResult(name, label, warning=err)
        return AttachmentResult(name, label, text_content=content, preview_content=content)

    ext     = os.path.splitext(name)[1].lower()
    matches = _find_file_in_project(name, window)

    if not matches:
        return AttachmentResult(name, name,
            warning="`{}` not found in project".format(name))

    if len(matches) > 1:
        paths = "\n".join("  " + m for m in matches)
        return AttachmentResult(name, name,
            warning="Ambiguous — `{}` found in multiple locations:\n{}".format(name, paths))

    filepath = matches[0]

    if ext in IMAGE_EXTENSIONS:
        data, mime, err = read_image_file(filepath)
        if err:
            return AttachmentResult(name, name, warning=err)
        return AttachmentResult(name, name, image_data=data, image_mime=mime,
                                filepath=filepath,
                                preview_content="[Image: {}]".format(name))

    content, err = read_text_file(filepath)
    if err:
        return AttachmentResult(name, name, warning=err)
    return AttachmentResult(name, name, text_content=content,
                            filepath=filepath, preview_content=content)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def process_attachments(message, window):
    matches = list(ATTACH_PATTERN.finditer(message))
    results = [_resolve(m.group(1), window) for m in matches]

    context_parts = []
    images        = []
    for r in results:
        if r.warning:
            continue
        if r.text_content:
            if r.filepath:
                lang = os.path.splitext(r.name)[1][1:]
                context_parts.append(
                    "### Attached: {}\n```{}\n{}\n```".format(r.name, lang, r.text_content))
            else:
                context_parts.append(
                    "### Attached: {}\n{}".format(r.display_label, r.text_content))
        if r.image_data:
            images.append((r.image_data, r.image_mime))

    return results, "\n\n".join(context_parts), images, matches


# ---------------------------------------------------------------------------
# Phantom installation
# ---------------------------------------------------------------------------

def install_phantoms(view, edit, msg_start, results, matches, window):
    phantom_set = sublime.PhantomSet(view, "gemini_attach_{}".format(msg_start))
    _persistent_phantom_sets.append(phantom_set)

    phantoms   = []
    adjustment = 0

    for match, result in zip(matches, results):
        abs_start = msg_start + match.start() - adjustment
        abs_end   = msg_start + match.end()   - adjustment

        view.erase(edit, sublime.Region(abs_start, abs_end))
        adjustment += match.end() - match.start()

        if result.warning:
            html = PHANTOM_WARN.format(label=result.name)
        elif result.filepath:
            html = PHANTOM_BADGE.format(href="open:" + result.filepath,
                                        label=result.display_label)
        else:
            html = PHANTOM_BADGE.format(href="preview:" + result.name,
                                        label=result.display_label)

        phantoms.append(sublime.Phantom(
            sublime.Region(abs_start, abs_start),
            html,
            sublime.LAYOUT_INLINE,
            lambda h, w=window: _on_navigate(h, w)
        ))

    phantom_set.update(phantoms)


# ---------------------------------------------------------------------------
# Navigation callbacks
# ---------------------------------------------------------------------------

def _on_navigate(href, window):
    if href.startswith("open:"):
        _open_or_focus_file(href[5:], window)
    elif href.startswith("preview:"):
        _open_preview_tab(href[8:], window)


def _open_or_focus_file(filepath, window):
    for view in window.views():
        if view.file_name() == filepath:
            window.focus_view(view)
            return
    view = window.open_file(filepath)
    if window.num_groups() >= 2:
        window.set_view_index(view, 0, len(window.views_in_group(0)))


def _open_preview_tab(name, window):
    tab_name = "Preview: {}".format(name)
    for view in window.views():
        if view.name() == tab_name:
            window.focus_view(view)
            return

    result = _resolve(name, window)
    if result.warning:
        sublime.status_message("Preview unavailable: {}".format(result.warning))
        return

    content = result.preview_content or result.text_content or ""
    view    = window.new_file()
    view.set_name(tab_name)
    view.set_scratch(True)
    view.run_command("append", {"characters": content})

    if window.num_groups() >= 2:
        window.set_view_index(view, 1, len(window.views_in_group(1)))
    window.focus_view(view)


# ---------------------------------------------------------------------------
# GeminiInstallAttachmentsCommand
# ---------------------------------------------------------------------------

class GeminiInstallAttachmentsCommand(sublime_plugin.TextCommand):
    def run(self, edit, key, msg_start):
        if key not in _pending_attachments:
            return
        results, matches, window = _pending_attachments.pop(key)
        install_phantoms(self.view, edit, msg_start, results, matches, window)