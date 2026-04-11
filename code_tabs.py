import re
from .console_view import SYNTAX_MAP


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
    counter = [0]

    gemfile_pattern = re.compile(
        r'<gemfile([^>]*)>\s*```(\w*)\s*\n(.*?)```\s*</gemfile>',
        re.DOTALL
    )

    def handle_gemfile(match):
        attrs      = match.group(1).strip()
        lang       = match.group(2).strip()
        code       = match.group(3)
        lines      = len(code.splitlines())
        counter[0] += 1
        path_match = re.search(r'path=["\']([^"\']+)["\']', attrs)
        label      = path_match.group(1) if path_match else "{} snippet #{}".format(
            lang if lang else "code", counter[0])
        tab = open_code_in_side_tab(window, code, lang, counter[0], tab_label=label)
        return "📄 *`{}` ({} lines) — opened in side tab \"{}\"*".format(label, lines, tab)

    result = gemfile_pattern.sub(handle_gemfile, response_text)

    gemsnippet_pattern = re.compile(
        r'<gemsnippet>\s*```(\w*)\s*\n(.*?)```\s*</gemsnippet>',
        re.DOTALL
    )

    def handle_gemsnippet(match):
        lang       = match.group(1).strip()
        code       = match.group(2)
        lines      = len(code.splitlines())
        counter[0] += 1
        label      = "{} snippet #{}".format(lang if lang else "code", counter[0])
        tab = open_code_in_side_tab(window, code, lang, counter[0], tab_label=label)
        return "📄 *`{}` ({} lines) — opened in side tab \"{}\"*".format(label, lines, tab)

    result = gemsnippet_pattern.sub(handle_gemsnippet, result)

    result = re.sub(r'</?gemsnippet[^>]*>', '', result)
    result = re.sub(r'</?gemfile[^>]*>', '', result)

    fence_pattern = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)

    def handle_plain_fence(match):
        lang  = match.group(1).strip()
        code  = match.group(2)
        lines = len(code.splitlines())
        if lines >= threshold:
            counter[0] += 1
            tab = open_code_in_side_tab(window, code, lang, counter[0])
            return "📄 *Code snippet ({} lines) — opened in side tab \"{}\"*".format(lines, tab)
        return match.group(0)

    result = fence_pattern.sub(handle_plain_fence, result)

    unclosed_open = re.search(r'```(\w+)\n', result)
    if unclosed_open and '```' not in result[unclosed_open.end():]:
        lang   = unclosed_open.group(1).strip()
        code   = result[unclosed_open.end():]
        lines  = len(code.splitlines())
        counter[0] += 1
        tab    = open_code_in_side_tab(window, code, lang, counter[0])
        result = result[:unclosed_open.start()] + \
            "📄 *Code snippet ({} lines, response cut off) — opened in side tab \"{}\"*".format(
                lines, tab)

    return result
