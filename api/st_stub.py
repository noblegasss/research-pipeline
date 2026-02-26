"""Minimal Streamlit stub so app.py can be imported without streamlit installed.

Only mocks the subset of the st.* API that app.py touches at module-load time
and in the non-UI helper functions we call from the API backend.
"""
from __future__ import annotations
from typing import Any, Callable


# ── Exceptions ────────────────────────────────────────────────────────────────

class StreamlitSecretNotFoundError(KeyError):
    pass


class StreamlitAPIException(Exception):
    pass


# ── session_state ──────────────────────────────────────────────────────────────

class _SessionState(dict):
    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def __delattr__(self, key: str) -> None:
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key)


session_state = _SessionState()


# ── secrets ────────────────────────────────────────────────────────────────────

class _Secrets(dict):
    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError:
            raise StreamlitSecretNotFoundError(key)


secrets = _Secrets()


# ── cache decorators ───────────────────────────────────────────────────────────

def cache_data(func: Callable | None = None, *, ttl: Any = None, show_spinner: Any = True,
               max_entries: Any = None, hash_funcs: Any = None) -> Any:
    """No-op decorator – just return the function as-is."""
    if func is not None:
        return func
    def decorator(f: Callable) -> Callable:
        return f
    return decorator


def cache_resource(func: Callable | None = None, **kwargs: Any) -> Any:
    if func is not None:
        return func
    def decorator(f: Callable) -> Callable:
        return f
    return decorator


# ── No-op UI helpers ──────────────────────────────────────────────────────────

def _noop(*args: Any, **kwargs: Any) -> None:  # noqa: ANN001
    return None


class _NoopCtx:
    def __enter__(self) -> "_NoopCtx":
        return self
    def __exit__(self, *_: Any) -> None:
        pass
    def __call__(self, *args: Any, **kwargs: Any) -> "_NoopCtx":
        return self


def _noop_ctx(*args: Any, **kwargs: Any) -> _NoopCtx:
    return _NoopCtx()


# Commonly used st.* calls in app.py
write = _noop
markdown = _noop
text = _noop
title = _noop
header = _noop
subheader = _noop
caption = _noop
error = _noop
warning = _noop
info = _noop
success = _noop
toast = _noop
balloons = _noop
divider = _noop
spinner = _noop_ctx
empty = _NoopCtx
container = _noop_ctx
expander = _noop_ctx
sidebar = _NoopCtx()
columns = lambda n, **kw: [_NoopCtx() for _ in range(n if isinstance(n, int) else len(n))]  # noqa: E731
tabs = lambda labels, **kw: [_NoopCtx() for _ in labels]  # noqa: E731
button = lambda *a, **kw: False  # noqa: E731
checkbox = lambda *a, **kw: False  # noqa: E731
selectbox = lambda *a, **kw: None  # noqa: E731
text_input = lambda *a, **kw: ""  # noqa: E731
text_area = lambda *a, **kw: ""  # noqa: E731
number_input = lambda *a, **kw: 0  # noqa: E731
slider = lambda *a, **kw: None  # noqa: E731
multiselect = lambda *a, **kw: []  # noqa: E731
radio = lambda *a, **kw: None  # noqa: E731
progress = _noop
image = _noop
json = _noop
dataframe = _noop
table = _noop
metric = _noop
stop = _noop
rerun = _noop
set_page_config = _noop
