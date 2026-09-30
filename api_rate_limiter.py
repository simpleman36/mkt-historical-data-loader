"""
Sliding-window API rate limiting and call timing.

apply_rate_limits() wraps methods of a class or instance so that every call in a
shared group respects all configured windows (e.g. {1: 9, 60: 59} means at most
9 calls per second and 59 per minute), and records per-method timing stats.
"""
import time
import inspect
from types import MethodType
from dataclasses import dataclass
from functools import wraps
from threading import Lock
from collections import deque
from typing import Dict, Optional, Iterable, Any

# ---------- internal state ----------

@dataclass
class CallStats:
    calls: int = 0
    total_s: float = 0.0
    min_s: float = float("inf")
    max_s: float = 0.0
    last_s: float = 0.0
    def update(self, dt: float):
        self.calls += 1
        self.total_s += dt
        self.last_s = dt
        if dt < self.min_s: self.min_s = dt
        if dt > self.max_s: self.max_s = dt
    def snapshot(self) -> dict:
        avg = self.total_s / self.calls if self.calls else 0.0
        return {
            "calls": self.calls,
            "total_s": self.total_s,
            "avg_s": avg,
            "min_s": None if self.calls == 0 else self.min_s,
            "max_s": None if self.calls == 0 else self.max_s,
            "last_s": None if self.calls == 0 else self.last_s,
        }
    def reset(self):
        self.calls = 0; self.total_s = 0.0
        self.min_s = float("inf"); self.max_s = 0.0; self.last_s = 0.0

@dataclass
class FuncState:
    stats: CallStats
    lock: Lock
    times_1m: deque  # timestamps for CPM (last 60s)

@dataclass
class WindowLimit:
    window_s: int
    limit: int
    times: deque

@dataclass
class GroupState:
    lock: Lock
    windows: list  # list of WindowLimit objects sorted by window_s ascending
    lims: Dict[int, int]  # {window_s: limit}

# registries
_FUNC_STATE: Dict[str, FuncState] = {}
_GROUP_STATE: Dict[str, GroupState] = {}

def _func_label(fn, owner_name: str, method_name: str) -> str:
    mod = getattr(fn, "__module__", "unknown")
    qn  = getattr(fn, "__qualname__", method_name)
    # stable label: module.Class.method
    return f"{mod}.{owner_name}.{method_name}::{qn}"

def _make_sync_decorator(*, label: str, group: str, lims: Dict[int, int]):
    """
    Return a sync-only decorator bound to supplied label+group.

    Args:
        label: Unique label for function tracking
        group: Shared rate limit group name
        lims: Dict[window_s, limit] - e.g., {1: 10, 600: 59} means 10/sec and 59/10min
    """
    # per-function state
    fstate = _FUNC_STATE.get(label)
    if fstate is None:
        fstate = FuncState(stats=CallStats(), lock=Lock(), times_1m=deque())
        _FUNC_STATE[label] = fstate

    # group state (shared quota)
    gstate = _GROUP_STATE.get(group)
    if gstate is None:
        windows = [
            WindowLimit(window_s=w, limit=lims[w], times=deque())
            for w in sorted(lims.keys())
        ]
        gstate = GroupState(lock=Lock(), windows=windows, lims=lims)
        _GROUP_STATE[group] = gstate

    def decorator(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            # ---- rate-limit: enforce ALL configured windows ----
            while True:
                with gstate.lock:
                    now = time.time()

                    # purge old entries and collect violations
                    max_wait = 0.0
                    all_ok = True

                    for window in gstate.windows:
                        while window.times and now - window.times[0] >= window.window_s:
                            window.times.popleft()

                        count = len(window.times)
                        if count >= window.limit:
                            all_ok = False
                            # oldest entry in window
                            wait = max(0.0, window.window_s - (now - window.times[0]))
                            max_wait = max(max_wait, wait)

                    if all_ok:
                        # add to all windows and proceed
                        for window in gstate.windows:
                            window.times.append(now)
                        break

                # sleep outside the lock
                if max_wait > 0:
                    time.sleep(max_wait)

            # ---- run & time ----
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                dt = time.perf_counter() - t0
                with fstate.lock:
                    fstate.stats.update(dt)
                    now = time.time()
                    fstate.times_1m.append(now)
                    # keep only last 60s for CPM
                    while fstate.times_1m and now - fstate.times_1m[0] > 60.0:
                        fstate.times_1m.popleft()

        # helper accessors (available on function object)
        def get_stats():
            with fstate.lock, gstate.lock:
                now = time.time()
                while fstate.times_1m and now - fstate.times_1m[0] > 60.0:
                    fstate.times_1m.popleft()
                cpm = len(fstate.times_1m)

                # purge and collect counts for all windows
                window_counts = {}
                next_available_in = 0.0
                for window in gstate.windows:
                    while window.times and now - window.times[0] >= window.window_s:
                        window.times.popleft()
                    count = len(window.times)
                    window_counts[window.window_s] = count
                    if count >= window.limit:
                        wait = max(0.0, window.window_s - (now - window.times[0]))
                        next_available_in = max(next_available_in, wait)

                snap = fstate.stats.snapshot()
                snap.update({
                    "calls_per_minute": cpm,
                    "group": group,
                    "window_counts": window_counts,
                    "lims": gstate.lims,
                    "next_available_in_s": round(next_available_in, 3),
                })
                return snap

        def reset_stats():
            with fstate.lock, gstate.lock:
                fstate.stats.reset()
                fstate.times_1m.clear()
                for window in gstate.windows:
                    window.times.clear()

        wrapper.get_stats = get_stats          # type: ignore[attr-defined]
        wrapper.reset_stats = reset_stats      # type: ignore[attr-defined]
        wrapper.__perf_label__ = label         # type: ignore[attr-defined]
        wrapper.__perf_group__ = group         # type: ignore[attr-defined]
        return wrapper
    return decorator

# ---------- public API ----------

def apply_rate_limits(
    target: Any,
    method_names: Iterable[str],
    *,
    group: str = "methods",
    limits: Dict[int, int] = None,
) -> None:
    """
    Instrument a class *or* a single instance's methods (sync only).
    Adds timing, calls-per-minute, and shared group rate limits.

    Args:
        target: Class or instance to patch
        method_names: Method names to wrap
        group: Shared rate limit group name
        limits: Dict[window_s, limit] - e.g., {1: 10, 600: 59} for 10/sec and 59/10min
              If None, defaults to {600: 59, 1: 5} (59 calls per 10min, 5 per second)

    Works for instance methods, @classmethod, and @staticmethod when patching the class.
    For instance patching, only regular instance methods are supported.
    """
    lims = limits if limits is not None else {600: 59, 1: 5}

    is_class = isinstance(target, type)

    for name in method_names:
        if is_class:
            # Get raw descriptor from class (not bound)
            desc = inspect.getattr_static(target, name)
            if isinstance(desc, staticmethod):
                func = desc.__func__
                label = _func_label(func, target.__name__, name)
                deco = _make_sync_decorator(label=label, group=group, lims=lims)
                wrapped = staticmethod(deco(func))
                setattr(target, name, wrapped)
            elif isinstance(desc, classmethod):
                func = desc.__func__
                label = _func_label(func, target.__name__, name)
                deco = _make_sync_decorator(label=label, group=group, lims=lims)
                wrapped = classmethod(deco(func))
                setattr(target, name, wrapped)
            else:
                # regular instance method (function descriptor)
                func = desc
                label = _func_label(func, target.__name__, name)
                deco = _make_sync_decorator(label=label, group=group, lims=lims)
                setattr(target, name, deco(func))
        else:
            # patching a single instance (regular instance methods only)
            bound = getattr(target, name)
            if not hasattr(bound, "__func__"):
                raise TypeError(f"{name!r} on instance {target!r} is not a regular method")
            func = bound.__func__
            cls = target.__class__
            label = _func_label(func, cls.__name__, name)
            deco = _make_sync_decorator(label=label, group=group, lims=lims)
            wrapped_func = deco(func)
            setattr(target, name, MethodType(wrapped_func, target))

def get_group_stats(group: str) -> dict:
    gs = _GROUP_STATE.get(group)
    if gs is None:
        return {"group": group, "window_counts": {}, "lims": {}, "wait_s": 0.0}

    with gs.lock:
        now = time.time()
        window_counts = {}
        next_available_in = 0.0

        for window in gs.windows:
            while window.times and now - window.times[0] >= window.window_s:
                window.times.popleft()
            count = len(window.times)
            window_counts[window.window_s] = count
            if count >= window.limit and window.times:
                wait = max(0.0, window.window_s - (now - window.times[0]))
                next_available_in = max(next_available_in, wait)

        return {
            "group": group,
            "window_counts": window_counts,
            "lims": gs.lims,
            "wait_s": round(next_available_in, 3),
        }