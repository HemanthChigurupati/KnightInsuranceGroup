"""In-memory submission store. Results are lost on restart (no database in scope)."""

import threading
from collections import OrderedDict

from app.core.models import SubmissionResult

MAX_SUBMISSIONS = 100  # oldest are dropped first, so memory stays bounded


class SubmissionStore:
    def __init__(self, max_items: int = MAX_SUBMISSIONS):
        self._items: OrderedDict[str, SubmissionResult] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items

    def save(self, result: SubmissionResult) -> SubmissionResult:
        with self._lock:
            self._items[result.submission_id] = result
            self._items.move_to_end(result.submission_id)
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return result

    def get(self, submission_id: str) -> SubmissionResult | None:
        with self._lock:
            return self._items.get(submission_id)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


store = SubmissionStore()
