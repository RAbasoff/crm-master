from .. import _Any  # noqa


def __getattr__(name):
    return _Any()
