"""Windows Job Object wrapper for process-tree containment (F4 Prima).

Usage in runner.py:

    from .jobobj import JobObject

    async def execute_job(...):
        with JobObject() as job:
            proc = await asyncio.create_subprocess_exec(...)
            job.assign(proc.pid)
            # ... wait / timeout ...
            # On timeout: job.terminate() kills the entire tree atomically.
            # On normal exit: context manager closes the handle, which kills
            # any orphan children (KILL_ON_JOB_CLOSE flag).

Why Job Objects over taskkill /T /F:
- taskkill enumerates the process tree at call time. A child spawned between
  enumeration and kill survives. With trees of 25+ processes (claude -p with
  MCP servers), the race window is real.
- Job Objects contain ALL descendants atomically. TerminateJobObject is a
  single syscall — no enumeration, no race.
- KILL_ON_JOB_CLOSE also catches stragglers on normal exit (e.g., an MCP
  server that outlives the claude process).

Tested 2026-09-01 against:
- Simulated tree (python → child → grandchild): PASS
- Real claude -p with haiku (25-process tree including MCP servers): PASS
"""

from __future__ import annotations

import ctypes
import os

if os.name != "nt":
    raise ImportError("jobobj is Windows-only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── ctypes structures ────────────────────────────────────────────────────────

class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]

class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]

class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

# ── Public API ───────────────────────────────────────────────────────────────

class JobObject:
    """Context manager that creates a Windows Job Object with KILL_ON_JOB_CLOSE.

    All processes assigned via .assign(pid) are killed when:
    - .terminate() is called explicitly (timeout / cancel), OR
    - the context manager exits (normal completion — catches orphan children).
    """

    def __init__(self):
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            self._handle, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(self._handle)
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid: int) -> None:
        """Add a process to this Job Object. Call immediately after spawn."""
        h = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        ok = kernel32.AssignProcessToJobObject(self._handle, h)
        err = ctypes.get_last_error() if not ok else 0
        kernel32.CloseHandle(h)
        if not ok:
            raise ctypes.WinError(err)

    def terminate(self, exit_code: int = 1) -> None:
        """Kill every process in the job. Idempotent."""
        if self._handle:
            kernel32.TerminateJobObject(self._handle, exit_code)

    def close(self) -> None:
        """Close the handle. KILL_ON_JOB_CLOSE kills any remaining members."""
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
