import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

from freecad_mcp.freecad_client import FreeCADConnection
from freecad_mcp.operations.core import get_document_status_operation
from freecad_mcp.responses import ErrorResponse

spec = importlib.util.spec_from_file_location('document_status_test', Path(__file__).parents[1]/'addon/FreeCADMCP/rpc_server/document_status.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def snapshot(path='', saved=False, modified=False, touched=False, gui_error=False):
    doc = SimpleNamespace(Name='D', Label='Деталь', FileName=str(path), isSaved=lambda: saved, isTouched=lambda: touched)
    app = SimpleNamespace(listDocuments=lambda: {'D': doc}, ActiveDocument=doc)
    def get_gui(name):
        if gui_error:
            raise RuntimeError('No GUI document')
        return SimpleNamespace(Modified=modified)
    return module.document_status(app, SimpleNamespace(getDocument=get_gui))


def test_new_empty_document_needs_saving_even_when_gui_clean():
    row = snapshot()['documents'][0]
    assert row['modified'] is False
    assert row['saved_once'] is False
    assert row['needs_save'] is True
    assert row['file_path'] is None


def test_recompute_and_save_state_are_independent(tmp_path):
    path = tmp_path/'part.FCStd'
    path.write_bytes(b'fixture')
    clean = snapshot(path, saved=True)['documents'][0]
    assert clean['needs_save'] is False
    edited = snapshot(path, saved=True, modified=True, touched=False)['documents'][0]
    assert edited['needs_save'] is True
    assert edited['needs_recompute'] is False
    assert edited['saved_once'] is True
    assert edited['modified_source'] == 'Gui.Document.Modified'


def test_missing_file_and_unavailable_gui_are_not_reported_clean(tmp_path):
    missing = snapshot(tmp_path/'missing.FCStd', saved=True)['documents'][0]
    assert missing['needs_save'] is True and not missing['file_exists']
    path = tmp_path/'existing.FCStd'
    path.touch()
    unknown = snapshot(path, saved=True, gui_error=True)
    row = unknown['documents'][0]
    assert row['modified'] is None and row['needs_save'] is None
    assert unknown['summary']['unknown_count'] == 1
    assert 'No GUI' in row['status_error']


def test_filter_is_exact_document_name_and_missing_is_error():
    doc = SimpleNamespace(Name='D',Label='D',FileName='',isSaved=lambda:False,isTouched=lambda:False)
    app = SimpleNamespace(listDocuments=lambda:{'D':doc},ActiveDocument=None)
    gui = SimpleNamespace(getDocument=lambda name:SimpleNamespace(Modified=False))
    assert len(module.document_status(app,gui,'D')['documents']) == 1
    with pytest.raises(ValueError,match='not open'):
        module.document_status(app,gui,'missing')


def test_client_preserves_legacy_list_contract_and_forwards_status(monkeypatch):
    calls=[]
    c=FreeCADConnection()
    monkeypatch.setattr(c,'_call',lambda *args:calls.append(args))
    c.list_documents()
    c.list_documents(True)
    c.get_document_status('D')
    assert calls == [('list_documents',),('list_documents',True),('get_document_status','D')]


def test_unavailable_status_rpc_is_mcp_error():
    def fail(name):
        raise RuntimeError('old addon')
    assert isinstance(get_document_status_operation(SimpleNamespace(get_document_status=fail)),ErrorResponse)
