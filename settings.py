import sublime
import os
from .api import GeminiAPIClient


_system_prompt_cache = [None]
_client_cache        = {}
_settings_obj        = None


def plugin_loaded():
    global _settings_obj
    _settings_obj = sublime.load_settings("Gemini Assistant.sublime-settings")
    _settings_obj.add_on_change("gemini_cache_invalidation", _invalidate_caches)


def get_settings():
    return _settings_obj


def _invalidate_caches():
    _system_prompt_cache[0] = None
    global _client_cache
    _client_cache = {}


def _load_gemini_context():
    path = os.path.join(os.path.dirname(__file__), "gemini_context.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _build_system_prompt(settings):
    if _system_prompt_cache[0] is not None:
        return _system_prompt_cache[0]
    prompt  = settings.get("system_prompt", "")
    context = _load_gemini_context()
    if context:
        prompt = prompt + "\n\n---\n\n" + context
    _system_prompt_cache[0] = prompt
    return prompt


def _make_client(settings):
    return GeminiAPIClient(
        settings.get("api_key", ""),
        settings.get("model", "gemini-2.5-flash"),
        settings.get("config", {})
    )


def _get_client(window, settings):
    global _client_cache
    win_id = window.id()
    if win_id not in _client_cache:
        _client_cache[win_id] = _make_client(settings)
    return _client_cache[win_id]
