from fontTools import _Any

ttLib = _Any
TTFont = _Any
ttGlyphSet = _Any


def __getattr__(name):
    return _Any
