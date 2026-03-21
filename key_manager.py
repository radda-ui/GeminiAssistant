import time

# HTTP codes / API statuses that mean "this key is quota-exhausted"
QUOTA_CODES   = frozenset({429})
QUOTA_STATUSES = frozenset({"RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"})


class KeyManager:
    """
    Manages a list of Gemini API keys.
    Dead keys are tracked per-session with a timestamp so they can be
    retried after a configurable cooldown (default 3 hours).
    """

    def __init__(self, keys, cooldown_seconds=3 * 3600):
        # Accept a single string or a list
        if isinstance(keys, str):
            keys = [keys]
        self._keys     = [k.strip() for k in keys if k and k.strip()]
        self._dead     = {}   # key → time.time() when it died
        self._index    = 0
        self._cooldown = cooldown_seconds

        if not self._keys:
            raise ValueError("No API keys provided")

    # ------------------------------------------------------------------ #

    @property
    def current_key(self):
        return self._keys[self._index]

    def mark_dead(self, key):
        """Mark a key as quota-exhausted right now."""
        self._dead[key] = time.time()

    def is_dead(self, key):
        """A key is dead if it was marked dead within the cooldown window."""
        ts = self._dead.get(key)
        if ts is None:
            return False
        if time.time() - ts > self._cooldown:
            # Cooldown expired — give it another chance
            del self._dead[key]
            return False
        return True

    def advance(self):
        """
        Try to move to the next live key.
        Returns (new_key_index, swapped, all_dead).
          swapped  — True if we actually moved to a different key
          all_dead — True if every key is exhausted
        """
        start = self._index
        n     = len(self._keys)

        for _ in range(n):
            self._index = (self._index + 1) % n
            if not self.is_dead(self._keys[self._index]):
                return self._index, self._index != start, False

        # Went full circle — all keys are dead
        return self._index, False, True

    def all_dead(self):
        return all(self.is_dead(k) for k in self._keys)

    def status_lines(self):
        """Return a list of status strings for error reporting."""
        lines = []
        for i, key in enumerate(self._keys):
            label = "key #{} (…{})".format(i + 1, key[-6:])
            if self.is_dead(key):
                elapsed = int(time.time() - self._dead[key])
                remaining = max(0, self._cooldown - elapsed)
                h, m = divmod(remaining // 60, 60)
                lines.append("  {} — quota exhausted, retry in {:d}h {:02d}m".format(
                    label, h, m))
            else:
                marker = " ← active" if i == self._index else ""
                lines.append("  {} — ok{}".format(label, marker))
        return lines

    def __len__(self):
        return len(self._keys)