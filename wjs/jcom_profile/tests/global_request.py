"""Test helpers for the request stored by Janeway's ``GlobalRequestMiddleware``."""

from contextlib import contextmanager
from typing import Iterator

from utils.middleware import context_request


@contextmanager
def isolated_global_request() -> Iterator[None]:
    """
    Run the block with no global request, then restore the previous one.

    ``GlobalRequestMiddleware`` keeps the current request in a ContextVar that is never reset. Under pytest all the
    tests of a worker share it, so a request set by one test leaks into the following ones (see
    wjs-profile-project#302).
    """
    token = context_request.set(None)
    try:
        yield
    finally:
        context_request.reset(token)
