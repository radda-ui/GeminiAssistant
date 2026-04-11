import sublime
import json
import threading
import traceback
import requests
from .database import Database
from .code_tabs import process_response_code_blocks
from .console_view import INPUT_PROMPT, _place_input_prompt, _install_fold_triangle


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


def _dispatch_to_gemini(window, response_view, api_message, system_prompt,
                         gemini_client, db_path, conversation_id,
                         code_tab_threshold, images=None):
    database     = Database(db_path)
    database.save_message(conversation_id, "user", api_message)
    conversation = database.get_conversation(conversation_id)
    database.close()

    sublime.status_message("Gemini is thinking...")

    threading.Thread(
        target=_gemini_thread,
        args=(window, response_view, conversation, system_prompt,
              gemini_client, db_path, conversation_id, code_tab_threshold, images)
    ).start()


def _format_api_error(response_json):
    err = response_json.get("error", {})
    if not err:
        return json.dumps(response_json, indent=2)

    code    = err.get("code", "")
    status  = err.get("status", "")
    message = err.get("message", "").strip()

    parts = []
    if code and status:
        parts.append("[{}  {}]".format(code, status))
    elif code:
        parts.append("[{}]".format(code))

    if message:
        parts.append(message.split('\n')[0].strip())

    retry      = None
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
        result        = gemini_client.send_messages(system_prompt, conversation, images=images)
        swap_message  = result.get("swap_message")
        response_json = result.get("response", result)

        if swap_message:
            sublime.set_timeout(
                lambda m=swap_message: response_view.run_command("append",
                    {"characters": "{}\n\n".format(m)}), 0)

        if "candidates" in response_json and response_json["candidates"]:
            candidate = response_json["candidates"][0]
            if "content" in candidate:
                text = candidate["content"]["parts"][0]["text"]
                local_db.save_message(conversation_id, "gemini", text)
                sublime.set_timeout(
                    lambda t=text: _show_result(window, response_view, t, code_tab_threshold), 0)
            else:
                finish = candidate.get("finishReason", "UNKNOWN")
                msg    = "Empty response (finishReason: {})".format(finish)
                if "error" in response_json:
                    msg = _format_api_error(response_json)
                sublime.set_timeout(lambda m=msg: _show_error(window, response_view, m), 0)
        elif "error" in response_json:
            msg = _format_api_error(response_json)
            sublime.set_timeout(lambda m=msg: _show_error(window, response_view, m), 0)
        else:
            msg = "Unexpected response shape:\n{}".format(json.dumps(response_json, indent=2))
            sublime.set_timeout(lambda m=msg: _show_error(window, response_view, m), 0)

    except requests.exceptions.RequestException as e:
        msg = "Network error: {}".format(e)
        sublime.set_timeout(lambda m=msg: _show_error(window, response_view, m), 0)
    except Exception as e:
        msg = "Unexpected error: {}\n\n{}".format(e, traceback.format_exc())
        sublime.set_timeout(lambda m=msg: _show_error(window, response_view, m), 0)
    finally:
        sublime.set_timeout(lambda: sublime.status_message(""), 0)
        local_db.close()


def _show_result(window, response_view, text, code_tab_threshold):
    display     = process_response_code_blocks(window, text, threshold=code_tab_threshold)
    block_start = response_view.size()
    response_view.run_command("append", {"characters": "gemini ◆ {}\n\n".format(display)})
    response_view.show(response_view.size())
    _install_fold_triangle(response_view, block_start)
    _place_input_prompt(response_view, window)


def _show_error(window, response_view, message):
    response_view.run_command("append", {"characters": "error ✖ {}\n\n".format(message)})
    response_view.show(response_view.size())
    _place_input_prompt(response_view, window)
