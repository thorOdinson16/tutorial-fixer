"""Say, in plain words, why a block that still fails is probably not version rot.

Many real tutorials are incomplete: a snippet uses a name no earlier snippet defines, imports the
author's own project modules, or needs a data file or a network. Those blocks stay `unresolved`
(they are never reported as passing), but the change log should tell the reader what is going on
and, where it is obvious, what would fix it. Deterministic pattern matching on the error text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Names a tutorial commonly uses without importing them, and the import that would fix it.
COMMON_IMPORTS = {
    "List": "from typing import List", "Dict": "from typing import Dict",
    "Optional": "from typing import Optional", "Any": "from typing import Any",
    "Tuple": "from typing import Tuple", "Union": "from typing import Union",
    "Callable": "from typing import Callable", "Set": "from typing import Set",
    "Sequence": "from typing import Sequence", "Iterable": "from typing import Iterable",
    "np": "import numpy as np", "pd": "import pandas as pd",
    "plt": "import matplotlib.pyplot as plt", "sns": "import seaborn as sns",
    "math": "import math", "os": "import os", "sys": "import sys", "json": "import json",
    "re": "import re", "datetime": "import datetime", "random": "import random",
    "pandas": "import pandas", "numpy": "import numpy", "pydantic": "import pydantic",
    "BaseModel": "from pydantic import BaseModel", "Field": "from pydantic import Field",
    "datetime.date": "from datetime import date",
}  # fmt: skip

LOCAL_MODULES = {
    "app", "main", "utils", "config", "settings", "models", "schemas", "crud", "db", "database",
    "core", "api", "tests", "src", "helpers", "common", "lib", "deps", "routers", "services",
}  # fmt: skip


@dataclass(frozen=True)
class Diagnosis:
    kind: str  # cascade | missing_import | undefined_name | local_module | missing_dependency
    #          # data_file | network | timeout | output_mismatch
    text: str


def diagnose(error: str | None) -> Diagnosis | None:
    """A plain-language reading of one block's last error, or None if it looks like real rot."""
    if not error:
        return None
    cascade = re.search(r"\[after earlier failure in (\w+)", error)
    if cascade:
        return Diagnosis(
            "cascade",
            f"follows from the failure in {cascade.group(1)}; fixing that block may fix this one",
        )
    if "timed out" in error:
        return Diagnosis("timeout", "ran longer than the sandbox time limit")
    if error.startswith("stdout missing expected text") or "assertion failed" in error:
        return Diagnosis("output_mismatch", "runs, but prints something other than expected")

    m = re.search(r"NameError: name '([\w.]+)' is not defined", error)
    if m:
        name = m.group(1)
        fix = COMMON_IMPORTS.get(name)
        if fix:
            return Diagnosis(
                "missing_import",
                f"uses `{name}` but no earlier code imports it; adding `{fix}` would fix it. "
                "The tutorial is incomplete, this is not version rot",
            )
        return Diagnosis(
            "undefined_name",
            f"uses `{name}`, which no earlier block defines; the snippet is not self-contained",
        )

    m = re.search(r"No module named '([\w.]+)'", error)
    if m:
        root = m.group(1).split(".")[0]
        if root in LOCAL_MODULES:
            return Diagnosis(
                "local_module",
                f"imports `{m.group(1)}`, a module of the tutorial author's own project that is "
                "not part of the tutorial",
            )
        return Diagnosis(
            "missing_dependency", f"needs the package `{root}`, which could not be installed"
        )
    m = re.search(r"Missing optional dependency '([\w.-]+)'", error)
    if m:
        return Diagnosis(
            "missing_dependency", f"pandas needs the optional package `{m.group(1)}` for this call"
        )
    m = re.search(r"FileNotFoundError.*?'([^']+)'", error)
    if m:
        return Diagnosis(
            "data_file", f"reads the file `{m.group(1)}`, which is not part of the tutorial"
        )
    if re.search(r"ConnectionError|getaddrinfo|URLError|Temporary failure|Max retries", error):
        return Diagnosis("network", "needs network access, which the sandbox does not have")
    return None
