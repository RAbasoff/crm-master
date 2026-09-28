from fontTools import _Any


def __getattr__(name):
    return _Any
