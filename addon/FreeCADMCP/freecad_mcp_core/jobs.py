"""Bounded, idempotent jobs; status/cancel never touch the GUI or wait for it."""
import copy
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable


class JobCancelled(Exception):
    code = "JOB_CANCELLED"


class AssemblyService:
    def __init__(self, dispatch: Callable, run: Callable, max_pending: int = 8, history: int = 32):
        self.dispatch, self.run = dispatch, run
        self.max_pending, self.history = max_pending, history
        self.lock = threading.Lock()
        self.jobs: OrderedDict[str, dict] = OrderedDict()
        self.requests: dict[str, tuple[str, str]] = {}

    def submit(self, operation: str, arguments: dict, request_id: str) -> dict:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return {"success": False, "error": "request_id must be 1..128 characters"}
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            return {"success": False, "error": "Expected operation string and arguments object"}
        try:
            signature = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
        with self.lock:
            if request_id in self.requests:
                previous, key = self.requests[request_id]
                if previous != signature:
                    return {"success": False, "error": "request_id already used with different arguments"}
                return {"success": True, "job_id": key, "replayed": True}
            if sum(j["state"] in ("queued", "running", "outcome_unknown") for j in self.jobs.values()) >= self.max_pending:
                return {"success": False, "error": "Assembly job queue is full; poll existing jobs"}
            key = uuid.uuid4().hex
            self.jobs[key] = {"id": key, "request_id": request_id, "operation": operation, "state": "queued",
                              "stage": "queued", "progress": 0, "created_at": time.time(),
                              "cancel_requested": False, "cancellable": True}
            self.requests[request_id] = (signature, key)
        threading.Thread(target=self._worker, args=(key, operation, copy.deepcopy(arguments)), daemon=True).start()
        return {"success": True, "job_id": key, "replayed": False}

    def _checkpoint(self, key: str, stage: str, progress: float) -> None:
        with self.lock:
            job = self.jobs[key]
            if job["cancel_requested"]:
                raise JobCancelled("Cancelled at safe checkpoint: " + stage)
            job.update(stage=stage, progress=progress)
            if stage == "committing":
                job["cancellable"] = False

    def _worker(self, key: str, operation: str, arguments: dict) -> None:
        def task() -> tuple:
            with self.lock:
                job = self.jobs[key]
                if job["state"] == "cancelled":
                    return (True,)
                job.update(state="running", started_at=time.time())
            try:
                self._checkpoint(key, "starting", 0.01)
                result = self.run(operation, arguments, lambda stage, progress: self._checkpoint(key, stage, progress))
            except BaseException as exc:
                result = {"success": False, "error": f"{type(exc).__name__}: {exc}",
                          "code": getattr(exc, "code", "ASSEMBLY_FAILED")}
            with self.lock:
                job = self.jobs[key]
                state = "done" if result.get("success") else "cancelled" if result.get("code") == "JOB_CANCELLED" else "failed"
                job.update(state=state, stage=state, progress=1, result=result,
                           cancellable=False, finished_at=time.time())
                self._trim()
            return (True,)
        try:
            response = self.dispatch(task, timeout=1800, queue_timeout=1800, operation_name="assembly:" + operation)
        except BaseException as exc:
            response = {"error": str(exc)}
        with self.lock:
            job = self.jobs.get(key)
            if job and job["state"] in ("queued", "running"):
                # A started GUI operation may finish after its dispatch deadline.
                # Its task owns the final result; do not falsely label it failed.
                if job["state"] == "running":
                    job.update(state="outcome_unknown", dispatch_error=str(response))
                else:
                    job.update(state="failed", result={"success": False, "error": str(response)}, finished_at=time.time())
                self._trim()

    def _trim(self) -> None:
        finished = [key for key, job in self.jobs.items() if job["state"] in ("done", "failed", "cancelled")]
        for key in finished[:-self.history]:
            self.requests.pop(self.jobs[key]["request_id"], None)
            del self.jobs[key]

    def status(self, job_id: str = "", include_result: bool = True) -> dict:
        with self.lock:
            if job_id:
                if job_id not in self.jobs:
                    return {"success": False, "error": "Unknown/expired assembly job"}
                job = copy.deepcopy(self.jobs[job_id])
                if not include_result:
                    job.pop("result", None)
                return {"success": True, "job": job}
            return {"success": True, "jobs": [{k: copy.deepcopy(v) for k, v in j.items() if k != "result"} for j in self.jobs.values()]}

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return {"success": False, "error": "Unknown/expired assembly job"}
            if job["state"] not in ("queued", "running", "outcome_unknown") or not job["cancellable"]:
                return {"success": True, "accepted": False, "state": job["state"]}
            job["cancel_requested"] = True
            if job["state"] == "queued":
                job.update(state="cancelled", stage="cancelled", cancellable=False, finished_at=time.time())
                self._trim()
            return {"success": True, "accepted": True, "state": job["state"],
                    "note": "Cancellation at next safe checkpoint; a running OCCT call cannot be interrupted"}
