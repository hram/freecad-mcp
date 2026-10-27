import threading
import time

from freecad_mcp_core.jobs import AssemblyService
from freecad_mcp_core.revisions import RevisionTracker


def eventually(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.005)


def immediate(task, **kwargs):
    return task()


def test_submission_is_idempotent_and_conflicting_retries_fail():
    gate = threading.Event()
    seen = []
    def run(op, args, checkpoint):
        seen.append(args)
        assert gate.wait(2)
        return {'success': True, 'result': 42}
    service = AssemblyService(immediate, run)
    first = service.submit('op', {'a': 1}, 'request')
    second = service.submit('op', {'a': 1}, 'request')
    assert first['job_id'] == second['job_id']
    assert second['replayed']
    assert not service.submit('op', {'a': 2}, 'request')['success']
    gate.set()
    eventually(lambda: service.status(first['job_id'])['job']['state'] == 'done')
    assert seen == [{'a': 1}]
    assert service.status(first['job_id'])['job']['result']['result'] == 42


def test_cancel_queued_job_never_runs_mutation():
    queue = []
    service = AssemblyService(lambda task, **kw: (queue.append(task) or (True,)),
                              lambda *args: (_ for _ in ()).throw(AssertionError('must not run')))
    # Dispatch ordinarily waits; emulate a queued GUI by blocking the dispatcher.
    gate = threading.Event()
    service.dispatch = lambda task, **kw: (queue.append(task), gate.wait(2), (True,))[-1]
    key = service.submit('move', {}, 'r')['job_id']
    eventually(lambda: len(queue) == 1)
    assert service.cancel(key)['accepted']
    queue[0]()
    gate.set()
    assert service.status(key)['job']['state'] == 'cancelled'


def test_running_cancel_raises_at_safe_checkpoint_and_commit_closes_cancellation():
    gate = threading.Event()
    entered = threading.Event()
    def run(op, args, checkpoint):
        entered.set()
        assert gate.wait(2)
        checkpoint('verifying', .5)
        return {'success': True}
    service = AssemblyService(immediate, run)
    key = service.submit('move', {}, 'r')['job_id']
    assert entered.wait(2)
    assert service.cancel(key)['accepted']
    gate.set()
    eventually(lambda: service.status(key)['job']['state'] == 'cancelled')
    assert service.status(key)['job']['result']['code'] == 'JOB_CANCELLED'
    committed = threading.Event()
    release = threading.Event()
    def committing(op, args, checkpoint):
        checkpoint('committing', .95)
        committed.set()
        assert release.wait(2)
        return {'success': True, 'result': 'done'}
    service.run = committing
    key = service.submit('move', {}, 'r2')['job_id']
    assert committed.wait(2)
    assert not service.cancel(key)['accepted']
    release.set()
    eventually(lambda: service.status(key)['job']['state'] == 'done')


def test_dispatch_timeout_reports_unknown_until_actual_task_returns():
    queue = []
    started, release = threading.Event(), threading.Event()
    def run(op, args, checkpoint):
        started.set()
        assert release.wait(2)
        return {'success': True, 'result': 'committed'}
    threads = []
    def timed_out(task, **kwargs):
        t = threading.Thread(target=task)
        threads.append(t)
        t.start()
        assert started.wait(2)
        return {'success': False, 'error': 'deadline exceeded'}
    service = AssemblyService(timed_out, run)
    key = service.submit('move', {}, 'r')['job_id']
    eventually(lambda: service.status(key)['job']['state'] == 'outcome_unknown')
    release.set()
    eventually(lambda: service.status(key)['job']['state'] == 'done')
    for thread in threads:
        thread.join(2)
    assert service.status(key)['job']['result']['result'] == 'committed'


def test_queue_limit_and_completed_history_are_bounded():
    gate = threading.Event()
    service = AssemblyService(immediate, lambda *a: (gate.wait(2) or True) and {'success': True}, max_pending=1, history=1)
    key = service.submit('read', {}, 'r')['job_id']
    assert not service.submit('read', {}, 'other')['success']
    gate.set()
    eventually(lambda: service.status(key)['job']['state'] == 'done')
    new = service.submit('read', {}, 'new')['job_id']
    eventually(lambda: service.status(new)['job']['state'] == 'done')
    assert not service.status(key)['success']
    assert len(service.status()['jobs']) == 1


def test_revision_identity_changes_on_close_reopen_and_tracks_unrelated_changes():
    from types import SimpleNamespace
    observers = []
    tracker = RevisionTracker(SimpleNamespace(addDocumentObserver=observers.append))
    doc = SimpleNamespace(Name='Doc')
    obj = SimpleNamespace(Document=doc)
    before = tracker.snapshot(doc)
    tracker.slotChangedObject(obj, 'Placement')
    assert tracker.snapshot(doc)['revision'] == before['revision'] + 1
    tracker.slotDeletedDocument(doc)
    reopened = SimpleNamespace(Name='Doc')
    assert tracker.snapshot(reopened)['token'] != before['token']
