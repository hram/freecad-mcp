"""Install a verified addon copy with backup; optional guarded activation without GUI restart."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


def install(source: Path, target: Path, activate: Callable[[], None] | None = None) -> Path | None:
    source, target = source.absolute(), target.absolute()
    for required in ('package.xml', 'rpc_server/rpc_server.py', 'freecad_mcp_core/runtime.py'):
        if not (source / required).is_file():
            raise ValueError('Incomplete addon source: ' + required)
    if source.resolve() == target.resolve():
        raise ValueError('Installation target is the development source; choose a separate path')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / ('.' + target.name + '.install-' + uuid.uuid4().hex)
    backup = None
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    try:
        if target.exists() or target.is_symlink():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            backup = target.parent.parent / 'mcp-addon-backups' / stamp / target.name
            backup.parent.mkdir(parents=True)
            target.rename(backup)
        staging.rename(target)
        if activate:
            activate()
    except BaseException:
        if target.exists() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.is_symlink():
            target.unlink()
        if backup:
            backup.rename(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return backup


def activate_rpc(target: Path, host: str, port: int, token: str | None) -> None:
    from freecad_mcp.freecad_client import FreeCADConnection
    client = FreeCADConnection(host=host, port=port, token=token)
    # All activation and guards run in the existing GUI queue as one operation.
    code = f'''
def _activate_verified_addon():
    import sys, importlib, importlib.util, pathlib
    import rpc_server as package
    import rpc_server.rpc_server as old
    if old.rpc_server_instance is None:
        raise RuntimeError('No running RPC server to activate')
    if any(j.get('state') == 'running' for j in old._ASYNC_JOBS.values()):
        raise RuntimeError('Background scripts are running; retry after completion')
    service = getattr(old, '_assembly_service', None)
    if service and any(j['state'] in ('queued','running','outcome_unknown') for j in service.status()['jobs']):
        raise RuntimeError('Native assembly jobs are pending; retry after completion')
    if any(d.HasPendingTransaction for d in App.listDocuments().values()):
        raise RuntimeError('A document has an open transaction; finish editing before activation')
    root = pathlib.Path({str(target)!r})
    previous_core = {{k:v for k,v in sys.modules.items() if k == 'freecad_mcp_core' or k.startswith('freecad_mcp_core.')}}
    for module in previous_core.values():
        tracker = getattr(module, '_tracker', None)
        if tracker is not None:
            App.removeDocumentObserver(tracker)
    for key in previous_core:
        del sys.modules[key]
    sys.path.insert(0, str(root))
    try:
        import rpc_server.version
        importlib.reload(rpc_server.version)
        spec = importlib.util.spec_from_file_location('rpc_server.rpc_server', root/'rpc_server/rpc_server.py')
        replacement = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replacement)
        for name in ('rpc_server_instance','rpc_server_thread','_stop_thread'):
            setattr(replacement, name, getattr(old, name))
        replacement._EXEC_NAMESPACE = old._EXEC_NAMESPACE
        instance = replacement.FreeCADRPC()
        assert instance.assembly_capabilities()['assembly_protocol'] == 1
        old.rpc_server_instance.register_instance(instance)
        sys.modules['rpc_server.rpc_server'] = replacement
        package.rpc_server = replacement
        print('Activated native assembly addon ' + replacement.ADDON_VERSION + '; documents untouched')
    except BaseException:
        old.rpc_server_instance.register_instance(old.FreeCADRPC())
        sys.modules['rpc_server.rpc_server'] = old
        package.rpc_server = old
        sys.modules.update(previous_core)
        for module in previous_core.values():
            tracker = getattr(module, '_tracker', None)
            if tracker is not None:
                App.addDocumentObserver(tracker)
        raise
_activate_verified_addon()
del _activate_verified_addon
'''
    result = client.execute_code(code)
    if not result.get('success'):
        raise RuntimeError(result.get('error', 'Activation failed'))
    # Verify through the new public protocol, not just script success.
    caps = client.assembly_capabilities()
    if caps.get('assembly_protocol') != 1:
        raise RuntimeError('Activated addon protocol check failed')
    print(result['message'])


def main() -> None:
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True, help='FreeCAD user Mod/FreeCADMCP directory')
    parser.add_argument('--activate', action='store_true', help='Activate through an existing healthy local GUI RPC server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9875)
    args = parser.parse_args()
    callback = (lambda: activate_rpc(args.target.absolute(), args.host, args.port, os.environ.get('FREECAD_MCP_TOKEN'))) if args.activate else None
    backup = install(ROOT/'addon/FreeCADMCP', args.target, callback)
    print('Installed:', args.target)
    print('Previous addon backup:', backup or '(new install)')
    if not args.activate:
        print('Restart FreeCAD to load the installed addon.')


if __name__ == '__main__':
    main()
