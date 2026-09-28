# Minimal fontTools stubs so fpdf2 can be imported without the real fontTools.
# Real fontTools (if installed) is preferred — ensure_fpdf() only uses this
# vendor path when the system package is missing. TTF embedding (add_font)
# requires the real fontTools; core Helvetica PDFs work with these stubs.

_ATTR_CACHE = {}


class _AnyMeta(type):
    def __getattr__(cls, name):
        key = (cls, name)
        if key not in _ATTR_CACHE:
            _ATTR_CACHE[key] = _Any()
        return _ATTR_CACHE[key]


class _Any(metaclass=_AnyMeta):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Any()

    def __getattr__(self, name):
        key = (id(self), name)
        if key not in _ATTR_CACHE:
            _ATTR_CACHE[key] = _Any()
        return _ATTR_CACHE[key]


def __getattr__(name):
    key = ('mod', name)
    if key not in _ATTR_CACHE:
        _ATTR_CACHE[key] = _Any()
    return _ATTR_CACHE[key]
