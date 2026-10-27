"""Load the packaged runtime into the existing addon's GUI-thread interpreter.

Source is sent over the authenticated/local XML-RPC execute_code channel. The
server needs no shared filesystem with FreeCAD and no addon replacement.
"""
import hashlib
import json
from pathlib import Path
from typing import Any

from ..responses import ToolResponse, error_response, json_response
from mcp.types import ImageContent, TextContent

MARKER = "__VERIFIED_CAD_RESULT__"


def build_script(operation: str, arguments: dict[str, Any]) -> str:
    sources = {name: Path(__file__).with_name(name + ".py").read_text() for name in ("geometry", "runtime")}
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()[:16]
    package = "_verified_cad_" + digest
    # Use a function scope so existing execute_code users' globals cannot collide.
    return f'''def _verified_cad_call():
    import sys, types, json
    package = {package!r}
    if package + '.runtime' not in sys.modules:
        root = types.ModuleType(package)
        root.__path__ = []
        sys.modules[package] = root
        for name, source in {sources!r}.items():
            module = types.ModuleType(package + '.' + name)
            module.__package__ = package
            sys.modules[module.__name__] = module
            setattr(root, name, module)
            exec(compile(source, '<verified-cad-' + name + '>', 'exec'), module.__dict__)
    runtime = sys.modules[package + '.runtime']
    result = runtime.dispatch({operation!r}, json.loads({json.dumps(arguments, allow_nan=False)!r}))
    print("\\n" + {MARKER!r} + json.dumps(result, allow_nan=False))
_verified_cad_call()
del _verified_cad_call
'''


def call(freecad: Any, operation: str, arguments: dict[str, Any]) -> ToolResponse:
    try:
        reply = freecad.execute_code(build_script(operation, arguments))
        if not reply.get("success"):
            return error_response("FreeCAD bridge failed: " + str(reply.get("error", "unknown error")))
        message = reply.get("message", "")
        lines = [line[len(MARKER):] for line in message.splitlines() if line.startswith(MARKER)]
        if len(lines) != 1:
            raise ValueError("Missing or ambiguous structured result from FreeCAD")
        result = json.loads(lines[0])
        if not result.get("success"):
            return error_response(result.get("error", "Unknown CAD operation failure"))
        data = result["result"]
        if operation == "inspect_region":
            views = data.pop("views")
            response = json_response({**data, "views": [v["view"] for v in views]})
            for view in views:
                response.extend([TextContent(type="text", text=view["view"]),
                                 ImageContent(type="image", mimeType="image/png", data=view["png"])])
            return response
        return json_response(data)
    except Exception as exc:
        return error_response("CAD operation failed: " + str(exc))
