import os
import re
import json
import shutil
import sublime
from .tools_read import run_git, SKIP_DIRS

try:
    import urllib.request as urllib_request
except ImportError:
    urllib_request = None


# ---------------------------------------------------------------------------
# Tool call parser
# ---------------------------------------------------------------------------

TOOL_PATTERN = re.compile(
    r'<gemini_tool\s+action="(\w+)">(.*?)</gemini_tool>',
    re.DOTALL
)

DESTRUCTIVE_ACTIONS = frozenset({
    'edit_file', 'patch_file', 'delete_file', 'move_file',
    'git_commit', 'git_stash',
})

# Maps action name → settings key that enables it
TOOL_SETTING_MAP = {
    'create_file':    'allow_create_file',
    'edit_file':      'allow_edit_file',
    'patch_file':     'allow_patch_file',
    'append_file':    'allow_append_file',
    'move_file':      'allow_move_file',
    'delete_file':    'allow_delete_file',
    'create_folder':  'allow_create_folder',
    'create_project': 'allow_create_project',
    'download_file':  'allow_download_file',
    'run_build':      'allow_run_build',
    'git_init':       'allow_git',
    'git_add':        'allow_git',
    'git_commit':     'allow_git',
    'git_checkout':   'allow_git',
    'git_branch':     'allow_git',
    'git_stash':      'allow_git',
    'git_pull':       'allow_git_pull',
}


def parse_tool_calls(text):
    """
    Extract all <gemini_tool> blocks from response text.
    Returns list of dicts: {action, params, content, raw_match}
    """
    tools = []
    for match in TOOL_PATTERN.finditer(text):
        action = match.group(1)
        body   = match.group(2)

        params  = {}
        content = ""

        if '---' in body:
            header_part, content = body.split('---', 1)
            content = content.strip()
        else:
            header_part = body

        for line in header_part.strip().splitlines():
            line = line.strip()
            if ':' in line:
                k, v = line.split(':', 1)
                params[k.strip()] = v.strip()

        tools.append({
            "action":  action,
            "params":  params,
            "content": content,
            "span":    (match.start(), match.end()),
        })
    return tools


def describe_tool(tool):
    """Return a short human-readable description for the confirmation UI."""
    action = tool["action"]
    p      = tool["params"]

    if action == "create_file":
        return "📝 create       {}".format(p.get("path", "?"))
    if action == "edit_file":
        return "✏️  edit         {}".format(p.get("path", "?"))
    if action == "patch_file":
        return "🔧 patch        {}".format(p.get("path", "?"))
    if action == "append_file":
        return "➕ append       {}".format(p.get("path", "?"))
    if action == "delete_file":
        return "🗑  delete       {}  →  _deleted/".format(p.get("path", "?"))
    if action == "move_file":
        return "📦 move         {}  →  {}".format(p.get("src", "?"), p.get("dst", "?"))
    if action == "create_folder":
        return "📁 mkdir        {}".format(p.get("path", "?"))
    if action == "create_project":
        return "🏗  new project  {}".format(p.get("name", "?"))
    if action == "download_file":
        return "⬇️  download     {}  →  {}".format(p.get("url", "?"), p.get("path", "?"))
    if action == "run_build":
        return "⚙️  run build"
    if action == "git_init":
        return "🔀 git init"
    if action == "git_add":
        return "🔀 git add      {}".format(p.get("files", "."))
    if action == "git_commit":
        return "🔀 git commit   \"{}\"".format(p.get("message", ""))
    if action == "git_checkout":
        return "🔀 git checkout {}".format(p.get("branch", "?"))
    if action == "git_branch":
        return "🔀 git branch   {}".format(p.get("name", "?"))
    if action == "git_stash":
        return "🔀 git stash"
    if action == "git_pull":
        return "🔀 git pull"
    return "❓ {}".format(action)


# ---------------------------------------------------------------------------
# Feedback queue
# ---------------------------------------------------------------------------

_feedback_tasks = []


def add_feedback(status, description):
    """status: 'ok' | 'skipped' | 'blocked' | 'error'"""
    icons = {'ok': '✅', 'skipped': '⏭', 'blocked': '🚫', 'error': '❌'}
    _feedback_tasks.append("{} {}".format(icons.get(status, '?'), description))


def get_feedback_text():
    """Return formatted feedback string and clear the queue."""
    if not _feedback_tasks:
        return None, "No pending tool feedback"
    text = "## Tool execution results\n\n" + "\n".join(_feedback_tasks)
    _feedback_tasks[:] = []
    return text, None


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------

def _resolve_path(rel_path, project_root):
    """Resolve a relative path against the project root."""
    if os.path.isabs(rel_path):
        return rel_path
    if project_root:
        return os.path.join(project_root, rel_path)
    return rel_path


def _safe_path(path, project_root):
    """
    Resolve path and verify it stays inside the project root.
    Returns (abs_path, error).
    """
    abs_path = os.path.realpath(_resolve_path(path, project_root))
    if project_root:
        real_root = os.path.realpath(project_root)
        if not abs_path.startswith(real_root):
            return None, "Path '{}' is outside the project root — blocked".format(path)
    return abs_path, None


# ---------------------------------------------------------------------------
# Individual operations
# ---------------------------------------------------------------------------

def op_create_file(path, content, project_root):
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    if os.path.exists(abs_path):
        return False, "File already exists: {}".format(path)
    try:
        parent = os.path.dirname(abs_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "Created {}".format(path)
    except Exception as e:
        return False, "Could not create {}: {}".format(path, e)


def op_edit_file(path, content, project_root):
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    if not os.path.exists(abs_path):
        return False, "File not found: {}".format(path)
    try:
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "Edited {}".format(path)
    except Exception as e:
        return False, "Could not edit {}: {}".format(path, e)


def op_append_file(path, content, project_root):
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    if not os.path.exists(abs_path):
        return False, "File not found: {}".format(path)
    try:
        with open(abs_path, 'a', encoding='utf-8') as f:
            f.write(content)
        return True, "Appended to {}".format(path)
    except Exception as e:
        return False, "Could not append to {}: {}".format(path, e)


def op_patch_file(path, content, project_root):
    """
    content format:
    <<<OLD
    exact old text
    >>>NEW
    new replacement text
    """
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    if not os.path.exists(abs_path):
        return False, "File not found: {}".format(path)

    if '<<<OLD' not in content or '>>>NEW' not in content:
        return False, "patch_file content must contain <<<OLD and >>>NEW markers"

    try:
        parts = content.split('<<<OLD', 1)[1].split('>>>NEW', 1)
        if len(parts) != 2:
            return False, "Malformed patch: could not split <<<OLD / >>>NEW"
        old_text = parts[0].strip('\n')
        new_text = parts[1].strip('\n')
    except Exception as e:
        return False, "Could not parse patch: {}".format(e)

    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            original = f.read()
    except Exception as e:
        return False, "Could not read {}: {}".format(path, e)

    if old_text not in original:
        return False, "Patch target not found in {} — the file may have changed".format(path)

    patched = original.replace(old_text, new_text, 1)
    try:
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(patched)
        return True, "Patched {}".format(path)
    except Exception as e:
        return False, "Could not write patch to {}: {}".format(path, e)


def op_delete_file(path, project_root):
    """Move file to _deleted/ with flattened path name."""
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    if not os.path.exists(abs_path):
        return False, "File not found: {}".format(path)

    # Flatten path: src/player/movement.lua → src_player_movement.lua
    flat_name = path.replace('/', '_').replace('\\', '_').lstrip('_')

    deleted_dir = os.path.join(project_root, '_deleted') if project_root else '_deleted'
    try:
        if not os.path.exists(deleted_dir):
            os.makedirs(deleted_dir)
        dst = os.path.join(deleted_dir, flat_name)
        # Avoid overwriting existing deleted file
        base, ext = os.path.splitext(dst)
        counter = 1
        while os.path.exists(dst):
            dst = "{}_{}{}".format(base, counter, ext)
            counter += 1
        shutil.move(abs_path, dst)
        return True, "Deleted {} → _deleted/{}".format(path, os.path.basename(dst))
    except Exception as e:
        return False, "Could not delete {}: {}".format(path, e)


def op_move_file(src, dst, project_root):
    abs_src, err = _safe_path(src, project_root)
    if err:
        return False, err
    abs_dst, err = _safe_path(dst, project_root)
    if err:
        return False, err
    if not os.path.exists(abs_src):
        return False, "Source not found: {}".format(src)

    try:
        parent = os.path.dirname(abs_dst)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        shutil.move(abs_src, abs_dst)
        return True, "Moved {} → {}".format(src, dst)
    except Exception as e:
        return False, "Could not move {} → {}: {}".format(src, dst, e)


def op_create_folder(path, project_root):
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    if os.path.exists(abs_path):
        return True, "Folder already exists: {}".format(path)
    try:
        os.makedirs(abs_path)
        return True, "Created folder {}".format(path)
    except Exception as e:
        return False, "Could not create folder {}: {}".format(path, e)


def op_create_project(name, path, window):
    """Create a new project folder with a .sublime-project file and load it."""
    try:
        abs_path = os.path.expanduser(path)
        project_dir = os.path.join(abs_path, name)
        if not os.path.exists(project_dir):
            os.makedirs(project_dir)

        project_file = os.path.join(project_dir, "{}.sublime-project".format(name))
        project_data = {
            "folders": [{"path": "."}],
            "build_systems": [],
            "settings": {}
        }
        with open(project_file, 'w', encoding='utf-8') as f:
            json.dump(project_data, f, indent=4)

        # Load the project in the current window
        window.set_project_data(project_data)
        return True, "Created project {} at {}".format(name, project_dir)
    except Exception as e:
        return False, "Could not create project {}: {}".format(name, e)


def op_download_file(url, path, project_root):
    if urllib_request is None:
        return False, "urllib.request not available"
    abs_path, err = _safe_path(path, project_root)
    if err:
        return False, err
    try:
        parent = os.path.dirname(abs_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        urllib_request.urlretrieve(url, abs_path)
        return True, "Downloaded {} → {}".format(url, path)
    except Exception as e:
        return False, "Download failed: {}".format(e)


def op_run_build(window):
    window.run_command("build")
    return True, "Build triggered"


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def op_git_init(project_root):
    if not project_root:
        return False, "No project root"
    out, err = run_git(['init'], project_root)
    if err:
        return False, err
    return True, "git init: {}".format(out)


def op_git_add(files, project_root):
    if not project_root:
        return False, "No project root"
    args = files.split() if files and files != '.' else ['.']
    out, err = run_git(['add'] + args, project_root)
    if err:
        return False, err
    return True, "git add: {}".format(files)


def op_git_commit(message, project_root):
    if not project_root:
        return False, "No project root"
    out, err = run_git(['commit', '-m', message], project_root)
    if err:
        return False, err
    return True, "git commit: {}".format(message)


def op_git_checkout(branch, project_root):
    if not project_root:
        return False, "No project root"
    out, err = run_git(['checkout', branch], project_root)
    if err:
        return False, err
    return True, "git checkout: {}".format(branch)


def op_git_branch(name, project_root):
    if not project_root:
        return False, "No project root"
    out, err = run_git(['checkout', '-b', name], project_root)
    if err:
        return False, err
    return True, "git branch created: {}".format(name)


def op_git_stash(project_root):
    if not project_root:
        return False, "No project root"
    out, err = run_git(['stash'], project_root)
    if err:
        return False, err
    return True, "git stash: {}".format(out)


def op_git_pull(project_root):
    if not project_root:
        return False, "No project root"
    out, err = run_git(['pull'], project_root)
    if err:
        return False, err
    return True, "git pull: {}".format(out)


# ---------------------------------------------------------------------------
# Main execute dispatcher
# ---------------------------------------------------------------------------

def execute_tool(tool, settings, project_root, window):
    """
    Execute a single tool call.
    Returns (success, message).
    Automatically adds result to feedback queue.
    """
    action = tool["action"]
    p      = tool["params"]
    c      = tool["content"]

    # Check settings permission
    setting_key = TOOL_SETTING_MAP.get(action)
    if setting_key and not settings.get(setting_key, False):
        msg = "{} is disabled — enable '{}' in settings".format(action, setting_key)
        add_feedback('blocked', "{}: {}".format(describe_tool(tool), msg))
        return False, msg

    ok, msg = _dispatch(action, p, c, project_root, window)
    add_feedback('ok' if ok else 'error', msg)
    return ok, msg


def _dispatch(action, p, content, project_root, window):
    if action == 'create_file':
        return op_create_file(p.get('path', ''), content, project_root)
    if action == 'edit_file':
        return op_edit_file(p.get('path', ''), content, project_root)
    if action == 'append_file':
        return op_append_file(p.get('path', ''), content, project_root)
    if action == 'patch_file':
        return op_patch_file(p.get('path', ''), content, project_root)
    if action == 'delete_file':
        return op_delete_file(p.get('path', ''), project_root)
    if action == 'move_file':
        return op_move_file(p.get('src', ''), p.get('dst', ''), project_root)
    if action == 'create_folder':
        return op_create_folder(p.get('path', ''), project_root)
    if action == 'create_project':
        return op_create_project(p.get('name', 'project'), p.get('path', '~'), window)
    if action == 'download_file':
        return op_download_file(p.get('url', ''), p.get('path', ''), project_root)
    if action == 'run_build':
        return op_run_build(window)
    if action == 'git_init':
        return op_git_init(project_root)
    if action == 'git_add':
        return op_git_add(p.get('files', '.'), project_root)
    if action == 'git_commit':
        return op_git_commit(p.get('message', 'commit'), project_root)
    if action == 'git_checkout':
        return op_git_checkout(p.get('branch', ''), project_root)
    if action == 'git_branch':
        return op_git_branch(p.get('name', ''), project_root)
    if action == 'git_stash':
        return op_git_stash(project_root)
    if action == 'git_pull':
        return op_git_pull(project_root)
    return False, "Unknown tool action: {}".format(action)
