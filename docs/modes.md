# Beyond version fixing: the opt-in modes

Keep working blocks, repair defects, update prose, fix warnings. Each is a flag in `PipelineFlags`; all four are on by default in `scripts/demo.py` and the web service and off in eval configs 1-6.

Two opt-in modes on top of the version fixing (`PipelineFlags.repair_defects`, `update_prose`; on by
default in `scripts/demo.py` and the web service, off in the eval configs `1`-`6`).

**Defect repair** targets blocks that fail for reasons other than library versions.

| defect | what happens |
|---|---|
| a well-known name is used but never imported (`np`, `pd`, `plt`, `List`, ...) | `prerepair` finds it statically, right after ingest, and adds the import at the top of the first block that uses it. This has to come first: with `import pandas as pd` missing the analyzer sees no `pd.` calls, so no version fix would even be attempted |
| a missing package (`ModuleNotFoundError`, "Missing optional dependency") | `repair` adds it to the install line and the environment |
| a name nothing defines (`Alice`) | the LLM drafts a 1-4 line definition; it is only a **suggestion** in the change log unless `apply_placeholders` is on, when it is inserted with a `# added by TRF (assumed): ...` comment |
| a project-local module, a data file, a database, the network, an intentional error | not repairable: reported with a plain-language reason |

Every repair of a runtime error is verified by re-running the sandbox and **reverted** unless the
error it targeted is gone. Each repair is a `defect_fix` change-log entry that quotes the error it
removed (there is no release note to cite). A block that already runs is never touched.

**Prose updates** were never allowed before; they are now, under strict rules:

- only sentences that mention a library version or an API the code edits really changed (or a name
  the edits removed) are even shown to the model; a tutorial with nothing to fix costs no call;
- the model returns replacements for individual sentences, never a rewritten document;
- every replacement is validated: it must stay close to the original (similarity and length), keep
  links and backticks intact, introduce no number that is not a target version or part of the new
  code, and match exactly one place in the prose;
- edits are applied by exact string replacement, so headings, links, code fences and every
  untouched sentence stay byte-for-byte identical;
- each applied edit is listed in the change log and the UI with its reason and the symbol or
  version that justified it.

**Warning fixes** (`fix_warnings`) cover code that still runs but warns. The sandbox records every
warning a block emits (category, message, line). A deprecation warning is tied to the library
symbol the block really uses (`applymap` in "DataFrame.applymap has been deprecated"), and the block
is then treated as a soft failure: release notes are retrieved for that symbol (with the warning text
as a second query), diffed and rewritten like any other fix, so every edit is cited. The edit is
kept only if, in a verification run, (a) the block still runs, (b) it prints exactly what it printed
before, (c) that warning is gone, and (d) no other block regressed; otherwise the whole batch is
reverted. A block that runs but warns is not "protected" while this mode is on.

Security-flavoured warnings (insecure or unverified requests, certificates) are listed first in the
change log's "Warnings still emitted" section and are changed only if a cited fix verifies the same
way; the tool never guesses at a security fix. Deprecation warnings it cannot tie to a symbol are
reported, not touched.
