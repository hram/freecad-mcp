"""Opt-in GUI smoke for save-state RPC + MCP; only a disposable document is saved."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from freecad_mcp.freecad_client import FreeCADConnection


def main() -> None:
    client = FreeCADConnection(host='127.0.0.1', port=9875)
    name = 'MCP_SaveStatus_' + uuid.uuid4().hex[:8]
    def execute(code: str) -> str:
        result = client.execute_code(code)
        assert result.get('success'), result
        return result['message']
    def status() -> dict:
        return client.get_document_status(name)['documents'][0]
    previous = execute("print('__ACTIVE__' + (App.ActiveDocument.Name if App.ActiveDocument else ''))").split('__ACTIVE__')[-1].strip()
    try:
        execute(f"_save_status_doc = App.newDocument({name!r})")
        fresh = status()
        assert not fresh['saved_once'] and fresh['needs_save']
        with tempfile.TemporaryDirectory(prefix='mcp-save-status-') as folder:
            path = str(Path(folder)/(name+'.FCStd'))
            execute(f"_save_status_box = _save_status_doc.addObject('Part::Box','Box')\n_save_status_doc.recompute()\n_save_status_doc.saveAs({path!r})\nGui.getDocument({name!r}).save()")
            saved = status()
            assert saved['saved_once'] and saved['file_exists'] and saved['needs_save'] is False, saved
            execute('_save_status_box.Length = 13\n_save_status_doc.recompute()')
            dirty = status()
            assert dirty['modified'] and dirty['needs_save'] and not dirty['needs_recompute'], dirty
            rows = client.list_documents(True)['documents']
            assert next(r for r in rows if r['name'] == name) == dirty
            asyncio.run(check_mcp(name))
            assert status() == dirty, 'Reading status changed the document'
            execute(f"Gui.getDocument({name!r}).save()")
            assert not status()['needs_save']
        print('PASS new / saved / edited+recomputed / saved-again states, detailed list, native RPC and MCP; reads do not change status')
    finally:
        execute(f"App.closeDocument({name!r})" + (f"\nApp.setActiveDocument({previous!r})" if previous else ''))
        client.disconnect()


async def check_mcp(name: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=['-c', 'from freecad_mcp.server import main; main()', '--host', '127.0.0.1'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert any(t.name == 'get_document_status' for t in tools.tools)
            result = await session.call_tool('get_document_status', {'doc_name': name})
            assert not result.model_dump(by_alias=True).get('isError', False)
            data = json.loads(next(c.text for c in result.content if c.type == 'text' and c.text.startswith('{')))
            assert data['documents'][0]['needs_save'] is True
            assert data['documents'][0]['needs_recompute'] is False


if __name__ == '__main__':
    main()
