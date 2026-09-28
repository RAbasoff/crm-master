from fontTools import _Any

BigGlyphMetrics = _Any
SmallGlyphMetrics = _Any


def __getattr__(name):
    return _Any
