from fontTools import _Any

ClipBoxFormat = _Any
CompositeMode = _Any
Paint = _Any
PaintFormat = _Any
VarAffine2x3 = _Any
VarColorLine = _Any
VarColorStop = _Any


def __getattr__(name):
    return _Any
