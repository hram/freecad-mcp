"""Native assembly protocol client, job polling and MCP result formatting.

Source delivery through execute_code is an explicit legacy compatibility path.
"""
import hashlib
import json
import time
import uuid
from importlib.resources import files
from typing import Any

from ..responses import ToolResponse, error_response, json_response
from mcp.types import ImageContent, TextContent
from ..version import is_missing_method_fault

MARKER = "__VERIFIED_CAD_RESULT__"


def build_script(operation: str, arguments: dict[str, Any]) -> str:
    sources = {name: files("freecad_mcp_core").joinpath(name + ".py").read_text() for name in ("geometry", "revisions", "runtime")}
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


def format_result(result: dict, operation: str) -> ToolResponse:
    if not result.get("success"):
        from ..responses import ErrorResponse
        return ErrorResponse(json_response(result))
    data = result["result"]
    if operation in ("inspect_region", "preview_alignment"):
        views = data.pop("views")
        response = json_response({**data, "views": [v["view"] for v in views]})
        for view in views:
            response.extend([TextContent(type="text", text=view["view"]),
                             ImageContent(type="image", mimeType="image/png", data=view["png"])])
        return response
    return json_response(data)


def legacy_call(freecad: Any, operation: str, arguments: dict) -> ToolResponse:
    reply = freecad.execute_code(build_script(operation, arguments))
    if not reply.get("success"):
        return error_response("Legacy bridge failed: " + str(reply.get("error", "unknown error")))
    message = reply.get("message", "")
    lines = [line[len(MARKER):] for line in message.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise ValueError("Missing or ambiguous structured result from FreeCAD")
    return [TextContent(type="text", text="Compatibility mode: runtime delivered through execute_code, not native RPC."),
            *format_result(json.loads(lines[0]), operation)]


def capabilities(freecad: Any) -> dict:
    result = freecad.assembly_capabilities()
    if not result.get("success") or result.get("assembly_protocol") != 1:
        raise ValueError("Incompatible assembly protocol. Install the matching addon from this repository.")
    return result


def get_job(freecad: Any, job_id: str, include_result: bool = True) -> ToolResponse:
    try:
        result = freecad.assembly_job_status(job_id, include_result)
        if not result.get("success"):
            return error_response(result.get("error", "Job lookup failed"))
        job = result["job"]
        response = json_response({k: v for k, v in job.items() if k != "result"})
        if include_result and "result" in job:
            formatted = format_result(job["result"], job["operation"])
            from ..responses import ErrorResponse
            if isinstance(formatted, ErrorResponse):
                return ErrorResponse([*response, *formatted])
            response.extend(formatted)
        return response
    except Exception as exc:
        return error_response("Assembly job lookup failed: " + str(exc))


def call(freecad: Any, operation: str, arguments: dict[str, Any]) -> ToolResponse:
    request_id, key = None, None
    try:
        try:
            capabilities(freecad)
        except Exception as exc:
            if is_missing_method_fault(exc):
                if getattr(freecad, "allow_legacy_assembly", False):
                    return legacy_call(freecad, operation, arguments)
                return error_response("This addon has no native assembly API. Install addon/FreeCADMCP from this release. "
                                      "Explicit --allow-legacy-addon enables temporary execute_code compatibility.")
            raise
        request_id = uuid.uuid4().hex
        response = freecad.assembly_submit(operation, arguments, request_id)
        if not response.get("success"):
            return error_response(response.get("error", "Failed to submit assembly job"))
        key = response["job_id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = freecad.assembly_job_status(key, False)
            if not result.get("success"):
                raise ValueError(result.get("error", "Job disappeared"))
            if result["job"]["state"] not in ("queued", "running"):
                return get_job(freecad, key)
            time.sleep(0.1)
        return json_response({"job_id": key, "request_id": request_id, "operation": operation,
                              "state": result["job"]["state"], "next": "get_assembly_job",
                              "note": "Still running; poll this job instead of submitting the operation again"})
    except Exception as exc:
        from ..responses import ErrorResponse
        return ErrorResponse(json_response({"success": False, "error": str(exc), "operation": operation,
                                            "request_id": request_id, "job_id": key,
                                            "next": "get_assembly_job" if key else "Retry start_assembly_operation with the same request_id if submission may have reached FreeCAD"}))
