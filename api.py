import requests
import json
from .key_manager import KeyManager, QUOTA_CODES, QUOTA_STATUSES


class GeminiAPIClient:

    def __init__(self, api_keys, model_name, config):
        """
        api_keys: str (single key) or list of str (multiple keys).
        """
        self.key_manager = KeyManager(api_keys)
        self.model_name  = model_name
        self.config      = config
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "{}:generateContent".format(self.model_name)
        )

    def send_messages(self, system_instruction, conversation, images=None):
        """
        Send a conversation to Gemini, rotating API keys on quota errors.

        Returns a dict with:
            "response"     : the raw Gemini JSON response
            "swap_message" : str or None — set if a key was rotated mid-call
        """
        payload = self._build_payload(system_instruction, conversation, images)
        swap_message = None

        # Try each key at most once
        attempts = len(self.key_manager)
        for attempt in range(attempts):
            key = self.key_manager.current_key
            try:
                response = requests.post(
                    self.url,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": key,
                    },
                    json=payload
                )
                data = response.json()
            except requests.exceptions.RequestException as e:
                raise ConnectionError(
                    "Failed to connect to Gemini API: " + str(e)) from e
            except json.JSONDecodeError as e:
                raise ValueError(
                    "Failed to parse Gemini API response: " + str(e)) from e

            # Check for quota error
            if self._is_quota_error(response.status_code, data):
                self.key_manager.mark_dead(key)
                _, swapped, all_dead = self.key_manager.advance()

                if all_dead:
                    # Build a rich all-exhausted error
                    lines = ["All API keys are quota-exhausted:"]
                    lines += self.key_manager.status_lines()
                    return {
                        "response": {
                            "error": {
                                "code": 429,
                                "status": "RESOURCE_EXHAUSTED",
                                "message": "\n".join(lines),
                                "details": []
                            }
                        },
                        "swap_message": None
                    }

                if swapped:
                    prev_idx  = (self.key_manager._index - 1) % len(self.key_manager)
                    swap_message = (
                        "⚠ API key #{} hit quota — switched to key #{}".format(
                            prev_idx + 1, self.key_manager._index + 1)
                    )
                continue  # retry with new key

            # Success (or non-quota error — surface it as-is)
            return {"response": data, "swap_message": swap_message}

        # Should not reach here, but guard anyway
        return {
            "response": {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "All keys tried, all failed.",
                    "details": []
                }
            },
            "swap_message": None
        }

    # ------------------------------------------------------------------ #

    def _is_quota_error(self, http_code, data):
        if http_code in QUOTA_CODES:
            return True
        err = data.get("error", {})
        return err.get("status", "") in QUOTA_STATUSES

    def _build_payload(self, system_instruction, conversation, images):
        contents = []
        for i, msg in enumerate(conversation):
            role  = "model" if msg["role"] == "gemini" else "user"
            parts = [{"text": msg["content"]}]

            is_last = (i == len(conversation) - 1)
            if images and is_last and role == "user":
                for b64_data, mime_type in images:
                    parts.append({
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data
                        }
                    })

            contents.append({"role": role, "parts": parts})

        return {
            "system_instruction": {"parts": {"text": system_instruction}},
            "contents": contents,
            "generationConfig": self.config,
        }