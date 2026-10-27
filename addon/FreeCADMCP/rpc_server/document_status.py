"""Read-only save-state snapshot. Call only on the FreeCAD GUI thread."""
import os
from typing import Any


def document_status(app: Any, gui: Any, doc_name: str = "") -> dict:
    documents = app.listDocuments()
    if doc_name:
        if doc_name not in documents:
            raise ValueError("Document is not open: " + doc_name)
        documents = {doc_name: documents[doc_name]}
    active = getattr(app, "ActiveDocument", None)
    result = []
    for name, doc in documents.items():
        path = doc.FileName or None
        saved_once = bool(doc.isSaved())
        exists = bool(path and os.path.isfile(path))
        modified, error = None, None
        try:
            value = gui.getDocument(name).Modified
            if not isinstance(value, bool):
                raise ValueError("GUI Modified flag is not a boolean")
            modified = value
        except Exception as exc:
            error = str(exc)
        needs_save = True if not saved_once or not exists else modified
        row = {"name": name, "label": doc.Label, "file_path": path,
               "is_active": active is not None and active.Name == name,
               "modified": modified, "saved_once": saved_once, "file_exists": exists,
               "needs_save": needs_save, "needs_recompute": bool(doc.isTouched()),
               "modified_source": "Gui.Document.Modified" if error is None else None}
        if error is not None:
            row["status_error"] = error
        result.append(row)
    return {"documents": result,
            "summary": {"open_count": len(result),
                        "needs_save_count": sum(r["needs_save"] is True for r in result),
                        "unknown_count": sum(r["needs_save"] is None for r in result)}}
