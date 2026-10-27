import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('addon_installer', Path(__file__).parents[1]/'scripts/install_addon.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def fixture_source(root):
    for path in ['package.xml', 'rpc_server/rpc_server.py', 'freecad_mcp_core/runtime.py']:
        target = root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('new')
    return root


def test_install_preserves_old_symlink_and_rolls_back_failed_activation(tmp_path):
    old = tmp_path/'original'
    old.mkdir()
    (old/'marker').write_text('old')
    target = tmp_path/'FreeCAD/Mod/FreeCADMCP'
    target.parent.mkdir(parents=True)
    target.symlink_to(old, target_is_directory=True)
    source = fixture_source(tmp_path/'source')
    def fail():
        assert (target/'package.xml').read_text() == 'new'
        raise RuntimeError('busy GUI')
    with pytest.raises(RuntimeError, match='busy GUI'):
        installer.install(source, target, fail)
    assert target.is_symlink() and target.resolve() == old
    assert (old/'marker').read_text() == 'old'
    backup = installer.install(source, target)
    assert not target.is_symlink()
    assert backup.is_symlink() and backup.resolve() == old
    assert (target/'package.xml').read_text() == 'new'


def test_invalid_source_leaves_installed_addon_untouched(tmp_path):
    target = tmp_path/'installed'
    target.mkdir()
    (target/'marker').write_text('old')
    with pytest.raises(ValueError, match='Incomplete'):
        installer.install(tmp_path/'missing', target)
    assert (target/'marker').read_text() == 'old'
