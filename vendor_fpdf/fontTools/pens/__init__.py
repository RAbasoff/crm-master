from fontTools import _Any

BasePen = _Any
TTGlyphPen = _Any


def __getattr__(name):
    return _Any
