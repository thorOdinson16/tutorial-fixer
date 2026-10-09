# Adding a library

One registration plus a corpus.

A library is one `register_library(LibraryConfig(...))` call in `src/trf/config.py`:

```python
register_library(LibraryConfig(
    name="numpy",                 # the import name
    pypi_name="numpy",            # what pip installs
    target_major=2,               # latest release within this major is the target
    bundled_imports=[],           # modules pip installs with it (not extra packages)
    changelog_sources=[...],      # raw .rst/.md release notes on GitHub, one per minor version
    migration_sources=[...],      # raw migration guides
    source_files=[...],           # raw library .py files to mine for deprecations (optional)
))
```

Then: add the file-name pattern that carries each source's version to `_URL_VERSIONS` in
`rag/corpus.py`, the wheel/Python limits of old releases to `OLD_PYTHON_FOR` in `nodes/precheck.py`,
build the corpus (`uv run python -m trf.rag.corpus build NAME`), and write a few tutorials for
`eval/` (see `scripts/check_case.py`). `source_files` feeds `rag/sourcemine.py`, which finds deprecations the release notes never spell out
(deprecation decorators, `warnings.warn` messages, alias assignments, `.. deprecated::` directives)
and adds them to the corpus as `{lib}:src-...` chunks; every registered library has some. Everything else (symbol extraction, retrieval, diff,
rewrite, sandbox, loop) is library-independent. The corpus is only as old as the sources you list:
a change that predates them (Matplotlib before 2.2, say) cannot be cited.
