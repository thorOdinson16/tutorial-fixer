"""Plumbing check: real retrieval + real Docker sandbox, with an ORACLE standing in for the LLM.

    uv run python scripts/oracle_check.py CASE [CONFIG] [forget]

`forget` makes the oracle find nothing on the first diff pass, so only the sandbox feedback loop
can recover. Needs Docker and the built corpus; no LLM or key.

The oracle answers diff prompts with the dataset's hand labels and rewrite prompts with the
hand-fixed reference block. It proves the wiring end to end; it says nothing about model quality.
"""

import re
import sys
import time

from trf.eval.loader import load_case, load_expected_verdicts, load_reference
from trf.eval.run_eval import CONFIGS
from trf.graph import run_graph
from trf.nodes.diff import DiffBatch, VerdictOut
from trf.nodes.ingest import ingest
from trf.nodes.rewrite import ChangeOut, RewriteOut
from trf.state import GraphState


class Oracle:
    def __init__(self, case):
        self.expected = load_expected_verdicts(case)
        orig = load_case(case)
        ref = load_reference(case)
        o = orig.model_copy(update=ingest(orig))
        r = ref.model_copy(update=ingest(ref))
        self.fix = {b.source: rb.source for b, rb in zip(o.blocks, r.blocks)}
        self.calls = 0

    def structured(self, prompt, schema, *, system=None, max_retries=2):
        self.calls += 1
        if schema is DiffBatch:
            out = []
            if FORGET and "OBSERVED FAILURES" not in prompt:
                return DiffBatch(verdicts=[])
            for m in re.finditer(r"^- (\S+) \(\w+\); candidate evidence: (.+)$", prompt, re.M):
                sym, ids = m[1], [i.strip() for i in m[2].split(",")]
                if sym in self.expected:
                    out.append(
                        VerdictOut(
                            symbol=sym,
                            verdict=self.expected[sym],
                            replacement=None,
                            evidence_chunk_ids=ids[:1],
                            confidence=0.95,
                        )
                    )
            return DiffBatch(verdicts=out)
        code = prompt.split("CODE:\n", 1)[1]
        new = self.fix.get(code, code)
        syms = re.findall(r"^- (\S+): \w+", prompt, re.M)
        return RewriteOut(
            code=new, changes=[ChangeOut(symbol=s, description="oracle") for s in syms]
        )


FORGET = len(sys.argv) > 3 and sys.argv[3] == "forget"
case = sys.argv[1]
cfg = sys.argv[2] if len(sys.argv) > 2 else "4_full"
oracle = Oracle(case)
state = load_case(case).model_copy(update={"max_loops": 3})
t = time.time()
final = run_graph(state, flags=CONFIGS[cfg], llm=oracle)
py = [b for b in final.blocks if b.lang == "python"]
print(f"{case} [{cfg}] {time.time() - t:.0f}s oracle_calls={oracle.calls} loops={final.loop_count}")
print("verdicts:", len(final.verdicts), "retrieved symbols:", len(final.evidence))
for b in py:
    print(
        f"  {b.id}: {b.status} {'edited' if b.source != b.original_source else ''} {(b.last_error or '')[:80]}"
    )
print("passing", sum(b.status == "passing" for b in py), "/", len(py))
print("warnings:", final.warnings[:5])
