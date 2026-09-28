from fontTools import _Any

instancer = _Any
VarStoreInstancer = _Any


def __getattr__(name):
    return _Any
