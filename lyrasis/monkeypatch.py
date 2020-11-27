import sys

def monkeypatch_method(cls):
    def decorator(func):
        setattr(cls, func.__name__, func)
        return func
    return decorator

def monkeypatch_classmethod(cls):
    def decorator(func):
        setattr(cls, func.__name__, classmethod(func))
        return func
    return decorator
