"""Expose the canonical ``src`` package from a source checkout."""

from pathlib import Path


_PACKAGE_ROOT = Path(__file__).resolve().parent / "src" / "research_dashboard"
_INIT_FILE = _PACKAGE_ROOT / "__init__.py"

__file__ = str(_INIT_FILE)
__package__ = __name__
__path__ = [str(_PACKAGE_ROOT)]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

exec(compile(_INIT_FILE.read_bytes(), __file__, "exec"), globals())
