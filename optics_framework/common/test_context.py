from contextvars import ContextVar

current_test_case: ContextVar[str] = ContextVar("current_test_case", default=None)
