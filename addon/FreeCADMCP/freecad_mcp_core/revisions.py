"""Document lifetime identities and conservative GUI-thread change counters."""
import uuid
from typing import Any


class RevisionTracker:
    def __init__(self, app: Any):
        self.app = app
        self.entries: dict[str, dict] = {}
        app.addDocumentObserver(self)

    def snapshot(self, doc: Any) -> dict:
        entry = self.entries.get(doc.Name)
        if entry is None or entry["object"] is not doc:
            entry = {"object": doc, "token": uuid.uuid4().hex, "revision": 0}
            self.entries[doc.Name] = entry
        return {"token": entry["token"], "revision": entry["revision"]}

    def slotDeletedDocument(self, doc: Any) -> None:
        self.entries.pop(doc.Name, None)

    def slotChangedObject(self, obj: Any, prop: str) -> None:
        if prop not in ("Visibility", "_GroupTouched", "Label", "Label2"):
            self._bump(obj.Document)

    def slotCreatedObject(self, obj: Any) -> None:
        self._bump(obj.Document)

    def slotDeletedObject(self, obj: Any) -> None:
        self._bump(obj.Document)

    def _bump(self, doc: Any) -> None:
        self.snapshot(doc)
        self.entries[doc.Name]["revision"] += 1
