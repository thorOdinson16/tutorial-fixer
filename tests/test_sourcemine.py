"""Mining deprecation facts out of library source code."""

from trf.config import LIBRARIES, LibraryConfig
from trf.rag.corpus import build_corpus
from trf.rag.sourcemine import mine

SRC = '''
import warnings
from x import _api, deprecate_kwarg, deprecated

class Scale:
    @_api.rename_parameter("3.3", f"linthresh{axis_name}", "linthresh")
    def __init__(self, axis, *, linthresh=2):
        pass

    @_api.deprecated("3.7", alternative="colormaps[name]")
    def get(self, name):
        """Get one.

        .. deprecated:: 3.7
            Use colormaps instead of this.
        """

def reshape(a, shape=None, *, newshape=None):
    if newshape is not None:
        warnings.warn("`newshape` keyword argument is deprecated, use `shape=...` instead "
                      "(deprecated in NumPy 2.1)", DeprecationWarning, stacklevel=2)
    warnings.warn("this is fine and says nothing relevant", UserWarning)

@deprecate_kwarg(old_arg_name="inplace", new_arg_name=None)
def drop(self, labels, inplace=False):
    pass

class Model:
    @deprecated("The `dict` method is deprecated; use `model_dump` instead.")
    def dict(self):
        pass

get_cmap = _api.deprecated("3.7", name="get_cmap", alternative="colormaps")(_get_cmap)
'''
URL = "https://raw.githubusercontent.com/o/r/v1/pkg/mod/file.py"


def chunks():
    return mine(SRC, "lib", URL)


def test_decorators_warnings_directives_and_aliases_are_all_found():
    text = " | ".join(c.text for c in chunks())
    assert (
        "lib.Scale.__init__: decorator _api.rename_parameter('3.3', f'linthreshx', 'linthresh')"
        in text
    )
    assert "linthreshy" in text  # `{axis_name}` is spelled out for both axes
    assert "lib.Scale.get: decorator _api.deprecated('3.7'" in text
    assert "lib.Scale.get: deprecated 3.7: Use colormaps instead of this." in text
    assert "lib.reshape: warns: `newshape` keyword argument is deprecated" in text
    assert "lib.drop: decorator deprecate_kwarg(old_arg_name='inplace'" in text
    assert (
        "lib.Model.dict: decorator deprecated('The `dict` method is deprecated; use `model_dump`"
        in text
    )
    assert "lib.get_cmap: deprecated alias _api.deprecated('3.7'" in text
    assert "says nothing relevant" not in text  # a warning that is not about deprecation


def test_versions_symbols_and_ids():
    by = {c.text.split(":")[0].split(".")[-1]: c for c in chunks()}
    assert by["get_cmap"].version_introduced == "3.7.0"
    assert next(c for c in chunks() if "newshape" in c.text).version_introduced == "2.1.0"
    ids = [c.id for c in chunks()]
    assert len(set(ids)) == len(ids) and all(i.startswith("lib:src-mod-file:") for i in ids)
    assert all(c.source_url == URL and c.doc_type == "docs" for c in chunks())


def test_unparseable_source_yields_nothing():
    assert mine("def broken(:\n", "lib", URL) == []


def test_build_corpus_adds_mined_chunks_for_configured_source_files():
    cfg = LibraryConfig(name="lib", pypi_name="lib", source_files=[URL])
    got = build_corpus(cfg, lambda url: SRC)
    assert got and all(c.id.startswith("lib:src-") for c in got)


def test_every_registered_library_has_something_mined_or_a_reason():
    assert all(c.source_files for c in LIBRARIES.values())
