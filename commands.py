import sublime
import sublime_plugin
from .database import Database
from .settings import get_settings, _build_system_prompt, _get_client
from .console_view import INPUT_PROMPT, get_gemini_response_view, _place_input_prompt
from .dispatch import (
    _dispatch_to_gemini, start_new_conversation_in_db,
    _build_conversation_items, getlang
)


class GeminiCodeAssistantCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        settings                = get_settings()
        self.system_prompt      = _build_system_prompt(settings)
        self.db_path            = settings.get("db_path",
                                    sublime.packages_path() + "/User/gemini_db.db")
        self.code_tab_threshold = settings.get("code_tab_threshold", 15)
        self.gemini_client      = _get_client(self.view.window(), settings)
        self.response_view      = get_gemini_response_view(self.view.window())

        if self.response_view.settings().get("conversation_id"):
            self.conversation_id = self.response_view.settings().get("conversation_id")
        else:
            db = Database(self.db_path)
            self.conversation_id = start_new_conversation_in_db(db)
            db.close()
            self.response_view.settings().set("conversation_id", self.conversation_id)
            self.response_view.run_command("append", {"characters": "❯ SESSION #{}\n{}\n\n".format(
                self.conversation_id, "─" * 60)})
            _place_input_prompt(self.response_view, self.view.window())

        sel           = self.view.sel()
        selected_text = self.view.substr(sel[0]) if len(sel) > 0 and not sel[0].empty() else ""
        lang          = getlang(self.view)
        initial       = "Selected code:\n```{}\n{}\n```\nYour question:\n\t ".format(
            lang, selected_text) if selected_text else ""
        self.view.window().show_input_panel("Ask Gemini:", initial, self.on_done, None, None)

    def on_done(self, user_input):
        window      = self.view.window()
        api_message = user_input.strip()
        self.response_view.run_command("append", {"characters": user_input + "\n\n"})
        self.response_view.show(self.response_view.size())
        _dispatch_to_gemini(
            window, self.response_view, api_message,
            self.system_prompt, self.gemini_client,
            self.db_path, self.conversation_id,
            self.code_tab_threshold
        )


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

        window          = self.view.window()
        settings        = get_settings()
        conversation_id = self.view.settings().get("conversation_id")

        self.view.replace(edit, sublime.Region(prompt_end, self.view.size()),
                          user_input + "\n\n")

        if not conversation_id:
            self.view.run_command("append", {"characters":
                "error ✖ No active session — use Alt+G to start one.\n\n"})
            _place_input_prompt(self.view, window)
            return

        _dispatch_to_gemini(
            window, self.view, user_input.strip(),
            _build_system_prompt(settings),
            _get_client(window, settings),
            settings.get("db_path", sublime.packages_path() + "/User/gemini_db.db"),
            conversation_id,
            settings.get("code_tab_threshold", 15)
        )


class GeminiNewConversationCommand(sublime_plugin.WindowCommand):
    def run(self):
        settings       = get_settings()
        db_path        = settings.get("db_path",
                            sublime.packages_path() + "/User/gemini_db.db")
        response_view  = get_gemini_response_view(self.window)

        db = Database(db_path)
        conversation_id = start_new_conversation_in_db(db)
        db.close()

        response_view.settings().set("conversation_id", conversation_id)
        response_view.run_command("append", {"characters": "❯ SESSION #{}\n{}\n\n".format(
            conversation_id, "─" * 60)})
        _place_input_prompt(response_view, self.window)
        self.window.focus_view(response_view)


class GeminiOpenConversationCommand(sublime_plugin.WindowCommand):
    def run(self):
        settings    = get_settings()
        self.db_path = settings.get("db_path",
                            sublime.packages_path() + "/User/gemini_db.db")
        db = Database(self.db_path)
        self.items, self.ids = _build_conversation_items(db)
        db.close()

        if not self.items:
            sublime.status_message("No saved conversations.")
            return

        self.window.show_quick_panel(self.items, self._on_selected)

    def _on_selected(self, index):
        if index == -1:
            return

        settings        = get_settings()
        conversation_id = self.ids[index]
        response_view   = get_gemini_response_view(self.window)

        db           = Database(self.db_path)
        messages     = db.get_conversation(conversation_id)
        db.close()

        response_view.run_command("gemini_set_content", {"characters": ""})
        response_view.settings().set("conversation_id", conversation_id)
        response_view.run_command("append", {"characters": "❯ SESSION #{}\n{}\n\n".format(
            conversation_id, "─" * 60)})

        for msg in messages:
            if msg["role"] == "user":
                response_view.run_command("append",
                    {"characters": "you ❯ {}\n\n".format(msg["content"])})
            else:
                response_view.run_command("append",
                    {"characters": "gemini ◆ {}\n\n".format(msg["content"])})

        _place_input_prompt(response_view, self.window)
        self.window.focus_view(response_view)


class GeminiDeleteConversationCommand(sublime_plugin.WindowCommand):
    def run(self):
        settings     = get_settings()
        self.db_path = settings.get("db_path",
                            sublime.packages_path() + "/User/gemini_db.db")
        db = Database(self.db_path)
        self.items, self.ids = _build_conversation_items(db)
        db.close()

        if not self.items:
            sublime.status_message("No saved conversations.")
            return

        self.window.show_quick_panel(self.items, self._on_selected)

    def _on_selected(self, index):
        if index == -1:
            return

        conversation_id = self.ids[index]
        db = Database(self.db_path)
        db.delete_conversation(conversation_id)
        db.close()

        response_view = get_gemini_response_view(self.window)
        if response_view.settings().get("conversation_id") == conversation_id:
            response_view.settings().erase("conversation_id")
            response_view.run_command("gemini_set_content", {"characters": ""})

        sublime.status_message("Conversation #{} deleted.".format(conversation_id))
