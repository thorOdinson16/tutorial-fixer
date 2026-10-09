"""Defect repair: deterministic fixes verified by a (fake) sandbox re-run; suggestions via a fake LLM."""

import pytest
from pydantic import ValidationError

from test_diff_rewrite_assemble import FakeLLM
from trf.config import PipelineFlags
from trf.nodes.repair import Definition, make_repair, plan_repairs
from trf.state import ChangeLogEntry, CodeBlock, GraphState

ON = PipelineFlags(repair_defects=True)


def blk(i, src, status="pending", err=None, lang="python"):
    return CodeBlock.new(i, src, lang=lang).model_copy(update={"status": status, "last_error": err})


def failing_np_state():
    return GraphState(
        blocks=[
            blk("b1", "pip install numpy", lang="bash"),
            blk("b2", "import pandas as pd\nx = 1\n", "passing"),
            blk("b3", "print(np.arange(3))\n", "failing", "NameError: name 'np' is not defined"),
            blk(
                "b4",
                "print(np.zeros(2))\n",
                "failing",
                "NameError: name 'np' is not defined [after earlier failure in b3; may be a "
                "downstream effect]",
            ),
        ],
        libraries=["pandas"],
    )


def fake_sandbox(rules):
    """rules: list of (predicate(state, block) -> error or None); None = block passes."""

    def run(state):
        blocks = []
        for b in state.blocks:
            if b.lang != "python":
                blocks.append(b)
                continue
            err = next((e for rule in rules if (e := rule(state, b))), None)
            blocks.append(
                b.model_copy(update={"status": "failing" if err else "passing", "last_error": err})
            )
        return {"blocks": blocks, "run_results": []}

    return run


def needs_numpy_import(state, b):
    uses = "np." in b.source and "import numpy as np" not in "".join(x.source for x in state.blocks)
    return "NameError: name 'np' is not defined" if uses else None


def test_plan_repairs_finds_root_causes_only():
    plans = plan_repairs(failing_np_state())
    assert [(p.block_id, p.kind, p.text) for p in plans] == [("b3", "import", "import numpy as np")]


def test_plan_skips_local_modules_stdlib_and_already_imported():
    s = GraphState(
        blocks=[
            blk("b1", "import numpy as np\n", "passing"),
            blk("b2", "np.zeros(1)", "failing", "NameError: name 'np' is not defined"),
            blk("b3", "from app.schemas import X", "failing",
                "ModuleNotFoundError: No module named 'app.schemas'"),
            blk("b4", "import os2", "failing", "ModuleNotFoundError: No module named 'os'"),
            blk("b5", "import pymysql", "failing", "ModuleNotFoundError: No module named 'pymysql'"),
        ]
    )  # fmt: skip
    assert [(p.block_id, p.kind, p.text) for p in plan_repairs(s)] == [("b5", "package", "pymysql")]


def test_flag_off_is_a_no_op():
    def boom(state):
        raise AssertionError("sandbox must not run")

    assert make_repair(PipelineFlags(), sandbox_fn=boom)(failing_np_state()) == {}


def test_missing_import_is_added_verified_and_logged():
    out = make_repair(ON, sandbox_fn=fake_sandbox([needs_numpy_import]))(failing_np_state())
    blocks = {b.id: b for b in out["blocks"]}
    assert blocks["b3"].source.startswith("import numpy as np\n")
    assert blocks["b3"].status == "passing" and blocks["b4"].status == "passing"
    (entry,) = [e for e in out["change_log"] if e.kind == "defect_fix"]
    assert entry.block_id == "b3" and "np" in entry.description
    assert "NameError" in entry.error
    assert "repair" not in " ".join(out["warnings"])


def test_a_repair_that_does_not_remove_its_error_is_reverted():
    always_broken = fake_sandbox([lambda s, b: "NameError: name 'np' is not defined"])
    s = failing_np_state()
    out = make_repair(ON, sandbox_fn=always_broken)(s)
    assert next(b for b in out["blocks"] if b.id == "b3").source == "print(np.arange(3))\n"
    assert [e for e in out["change_log"] if e.kind == "defect_fix"] == []
    assert any("reverted import fix for b3" in w for w in out["warnings"])


def test_environment_build_failure_reverts_a_package_repair():
    s = GraphState(
        blocks=[
            blk("b1", "pip install sqlalchemy", lang="bash"),
            blk("b2", "import pymysql", "failing", "ModuleNotFoundError: No module named 'pymysql'"),
        ]
    )  # fmt: skip

    def env_fails(state):
        return {
            "blocks": [
                b.model_copy(update={"status": "failing",
                                     "last_error": "environment build failed: no such package"})
                if b.lang == "python" else b
                for b in state.blocks
            ],
            "run_results": [],
        }  # fmt: skip

    out = make_repair(ON, sandbox_fn=env_fails)(s)
    assert "pymysql" not in out["extra_packages"]
    assert next(b for b in out["blocks"] if b.id == "b1").source == "pip install sqlalchemy"
    assert any("reverted package fix" in w for w in out["warnings"])


def test_missing_package_goes_into_the_install_line_and_the_environment():
    s = GraphState(
        blocks=[
            blk("b1", "pip install sqlalchemy", lang="bash"),
            blk("b2", "import pymysql", "failing", "ModuleNotFoundError: No module named 'pymysql'"),
        ]
    )  # fmt: skip

    def sandbox(state):
        ok = "pymysql" in state.extra_packages
        return {
            "blocks": [
                b.model_copy(update={"status": "passing" if ok else "failing",
                                     "last_error": None if ok else b.last_error})
                if b.lang == "python" else b
                for b in state.blocks
            ],
            "run_results": [],
        }  # fmt: skip

    out = make_repair(ON, sandbox_fn=sandbox)(s)
    assert "pymysql" in out["extra_packages"]
    assert "pymysql" in next(b for b in out["blocks"] if b.id == "b1").source
    assert next(b for b in out["blocks"] if b.id == "b2").status == "passing"


UNDEFINED = GraphState(
    blocks=[
        blk("b1", "x = 1\n", "passing"),
        blk("b2", "print(Alice.upper())\n", "failing", "NameError: name 'Alice' is not defined"),
    ]
)
DRAFT = Definition(definition='Alice = "alice"', assumption="Alice is a string")


def defined_alice(state, b):
    ok = any("Alice =" in x.source for x in state.blocks)
    return None if ok or "Alice" not in b.source else "NameError: name 'Alice' is not defined"


def test_undefined_names_are_only_suggested_by_default():
    out = make_repair(ON, llm=FakeLLM(DRAFT), sandbox_fn=fake_sandbox([defined_alice]))(UNDEFINED)
    assert out["suggestions"]["b2"].startswith('Alice = "alice"')
    assert "blocks" not in out  # nothing was changed or re-run


def test_placeholders_are_inserted_labelled_and_verified_when_enabled():
    flags = PipelineFlags(repair_defects=True, apply_placeholders=True)
    out = make_repair(flags, llm=FakeLLM(DRAFT), sandbox_fn=fake_sandbox([defined_alice]))(
        UNDEFINED
    )
    b2 = next(b for b in out["blocks"] if b.id == "b2")
    assert b2.source.startswith("# added by TRF (assumed): Alice is a string\nAlice = ")
    assert b2.status == "passing"
    assert any(e.kind == "defect_fix" and "placeholder" in e.description for e in out["change_log"])


def test_unparseable_drafts_are_dropped():
    bad = Definition(definition="Alice = (", assumption="x")
    out = make_repair(ON, llm=FakeLLM(bad), sandbox_fn=fake_sandbox([defined_alice]))(UNDEFINED)
    assert out == {}


def test_defect_fix_entries_must_quote_the_error():
    with pytest.raises(ValidationError):
        ChangeLogEntry(block_id="b", kind="defect_fix", description="x")
    ChangeLogEntry(block_id="b", kind="defect_fix", description="x", error="NameError: ...")


def test_more_stdlib_names_and_a_requires_package_error_are_repairable():
    s = GraphState(
        blocks=[
            blk("b2", "p = Path('a')\n", "failing", "NameError: name 'Path' is not defined"),
            blk("b3", "from starlette.testclient import TestClient", "failing",
                "RuntimeError: The starlette.testclient module requires the httpx package to be installed."),
        ]
    )  # fmt: skip
    got = [(p.block_id, p.kind, p.text) for p in plan_repairs(s)]
    assert ("b2", "import", "from pathlib import Path") in got
    assert ("b3", "package", "httpx") in got
