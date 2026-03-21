import os
import base64
import subprocess
import sublime

# Shared constant — imported by tools_attach.py and tools_write.py
SKIP_DIRS = frozenset({
    'node_modules', '__pycache__', '.git', 'build',
    'dist', 'target', '.gradle', '_deleted',
})

IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.gif'})
SUPPORTED_IMAGE_MIME = {
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif':  'image/gif',
}
TEXT_SIZE_LIMIT_KB = 500


# ---------------------------------------------------------------------------
# Git execution — public, used by both tools_read and tools_write
# ---------------------------------------------------------------------------

def run_git(args, cwd):
    """Run a git command. Returns (stdout, error). Python 3.3 compatible."""
    try:
        out = subprocess.check_output(
            ['git'] + args, cwd=cwd, stderr=subprocess.STDOUT)
        return out.decode('utf-8', errors='replace').strip(), None
    except subprocess.CalledProcessError as e:
        return None, e.output.decode('utf-8', errors='replace').strip()
    except OSError:
        return None, "Git not found in PATH"
    except Exception as e:
        return None, "git {} failed: {}".format(' '.join(args), e)


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

def read_text_file(filepath, size_limit_kb=TEXT_SIZE_LIMIT_KB):
    """Returns (content, error)."""
    try:
        size_kb = os.path.getsize(filepath) / 1024
        if size_kb > size_limit_kb:
            return None, "`{}` is {:.0f}KB — exceeds the {}KB limit".format(
                os.path.basename(filepath), size_kb, size_limit_kb)
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(), None
    except Exception as e:
        return None, "Could not read `{}`: {}".format(os.path.basename(filepath), e)


def read_image_file(filepath):
    """Returns (base64_str, mime_type, error)."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_IMAGE_MIME:
        return None, None, "Unsupported image format — supported: PNG, JPEG, WEBP, GIF"
    try:
        with open(filepath, 'rb') as f:
            data = base64.b64encode(f.read()).decode('utf-8')
        return data, SUPPORTED_IMAGE_MIME[ext], None
    except Exception as e:
        return None, None, "Could not read image `{}`: {}".format(os.path.basename(filepath), e)


# ---------------------------------------------------------------------------
# Project tree
# ---------------------------------------------------------------------------

def get_project_tree(window):
    """Returns (content, error)."""
    folders = window.folders()
    if not folders:
        return None, "No project folder is open"
    lines = []
    for folder in folders:
        lines.append("📁 {}/".format(os.path.basename(folder)))
        _walk_tree(folder, lines, indent=1, max_depth=4)
    return "\n".join(lines), None


def _walk_tree(current, lines, indent, max_depth):
    if indent > max_depth:
        lines.append("{}...".format("  " * indent))
        return
    try:
        names = sorted(os.listdir(current))
        dirs  = [n for n in names if os.path.isdir(os.path.join(current, n))]
        files = [n for n in names if not os.path.isdir(os.path.join(current, n))]
        for name in dirs + files:
            if name.startswith('.') or name in SKIP_DIRS:
                continue
            prefix   = "  " * indent
            fullpath = os.path.join(current, name)
            if os.path.isdir(fullpath):
                lines.append("{}{}/".format(prefix, name))
                _walk_tree(fullpath, lines, indent + 1, max_depth)
            else:
                lines.append("{}{}".format(prefix, name))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Sublime state readers
# ---------------------------------------------------------------------------

def get_build_log(window):
    """Returns (content, error)."""
    panel = window.find_output_panel("exec")
    if not panel:
        return None, "No build output found — run a build first"
    content = panel.substr(sublime.Region(0, panel.size()))
    if not content.strip():
        return None, "Build log is empty"
    return content.strip(), None


def get_clipboard():
    """Returns (content, error)."""
    content = sublime.get_clipboard()
    if not content or not content.strip():
        return None, "Clipboard is empty"
    return content.strip(), None


def get_selection(window):
    """Returns (content, error)."""
    view = window.active_view()
    if not view:
        return None, "No active view"
    sel = view.sel()
    if not sel or all(r.empty() for r in sel):
        return None, "No text selected — select some code first"
    filename = os.path.basename(view.file_name() or view.name() or "untitled")
    parts = [view.substr(r) for r in sel if not r.empty()]
    return "# Selection from {}\n\n{}".format(filename, "\n---\n".join(parts)), None


def get_open_files(window):
    """Returns (content, error)."""
    views = [v for v in window.views()
             if v.file_name() and v.name() != "Gemini Response"]
    if not views:
        return None, "No files are currently open"
    parts = []
    for view in views:
        fname = view.file_name()
        ext   = os.path.splitext(fname)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            continue
        content = view.substr(sublime.Region(0, view.size()))
        lang    = ext[1:] if ext else ''
        parts.append("## {}\n```{}\n{}\n```".format(os.path.basename(fname), lang, content))
    if not parts:
        return None, "No readable files currently open"
    return "\n\n".join(parts), None


# ---------------------------------------------------------------------------
# Git readers
# ---------------------------------------------------------------------------

def get_git_diff(folders):
    """Returns (content, error)."""
    if not folders:
        return None, "No project folder open"
    diff, err = run_git(['diff'], folders[0])
    if err:
        return None, err
    if not diff:
        diff, err = run_git(['diff', '--staged'], folders[0])
        if err:
            return None, err
    if not diff:
        return None, "No git changes found"
    return diff, None


def get_git_log(folders):
    """Returns (content, error)."""
    if not folders:
        return None, "No project folder open"
    log, err = run_git(['log', '--oneline', '-20'], folders[0])
    if err:
        return None, err
    if not log:
        return None, "No git history found"
    return log, None