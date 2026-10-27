import json
from freecad_mcp.assembly.bridge import MARKER, build_script, call
from freecad_mcp.responses import ErrorResponse


class Connection:
    def __init__(self, reply):
        self.reply = reply
        self.script = ''

    def execute_code(self, script):
        self.script = script
        return self.reply


def reply(value):
    return {'success': True, 'message': 'Output:\n' + MARKER + json.dumps(value) + '\n'}


def test_arguments_are_data_not_python_source():
    payload = "x'); raise RuntimeError('injected') #"
    script = build_script('inspect_part', dict(doc_name=payload, part='Fixed'))
    compile(script, '<bridge>', 'exec')
    # Exercise the generated code with a fake cached runtime instead of importing FreeCAD.
    import re, sys, types
    package = re.search("package = '([^']+)'", script)[1]
    module = types.ModuleType(package + '.runtime')
    seen = []
    module.dispatch = lambda op, args: (seen.append((op, args)) or {'success': True})
    old = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module
    try:
        exec(script, {})
    finally:
        if old is None:
            sys.modules.pop(module.__name__)
        else:
            sys.modules[module.__name__] = old
    assert seen == [('inspect_part', {'doc_name': payload, 'part': 'Fixed'})]


def test_rpc_and_runtime_failures_are_mcp_errors():
    for response in [{'success': False, 'error': 'offline'}, reply({'success': False, 'error': 'stale'}),
                     {'success': True, 'message': 'No result'}]:
        assert isinstance(call(Connection(response), 'inspect_part', {}), ErrorResponse)


def test_views_are_native_images_not_base64_text():
    c = Connection(reply({'success': True, 'result': {'document': 'A', 'views': [{'view': 'Top', 'png': 'abc'}]}}))
    response = call(c, 'inspect_region', {})
    assert [r.type for r in response] == ['text', 'text', 'image']
    assert response[-1].mimeType == 'image/png'
    assert response[-1].data == 'abc'
    assert 'abc' not in response[0].text
