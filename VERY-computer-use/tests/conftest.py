"""Offline regression: prevent fake input backends from touching real D-Bus."""
import pytest
@pytest.fixture(autouse=True)
def no_real_input_method(monkeypatch):
    from semantic_cu import fast_input
    class Guard:
        def __init__(self,*a,**kw): pass
        def start(self): return {"status":"already_inactive"}
        def restore(self): return {"status":"not_needed"}
    monkeypatch.setattr(fast_input,"LiteralInputGuard",Guard)
