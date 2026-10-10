# Real tutorials

Runs on real web pages and on the projects' own documentation. No hand-written references exist for these, so "passing" means the block ran; they are smoke tests, not scores.

## Twenty real tutorials

Web pages (pandas, NumPy, SQLAlchemy, FastAPI/pydantic) and eight chapters of the 2016 *Python Data
Science Handbook* (NumPy, Matplotlib, scikit-learn) were fetched, converted to Markdown with a small
stdlib script and run through `5_loop` with `scripts/demo.py`. No hand-written references and no
assertions, so "passing" only means the block ran; this is a smoke test, not a score.

| | python blocks | run as-is | run after the pipeline |
|---|---|---|---|
| 20 real tutorials | 624 | 467 (75%) | 545 (87%) with version fixing; 554 (89%) with repair and prose on |

Largest gains: a NumPy page 76 to 111 of 117 blocks, a Matplotlib chapter 1 to 15 of 15 and another
0 to 10 of 10 (the old `seaborn-whitegrid` style names), a scikit-learn chapter 20 to 25 of 32, a
pandas add-row page 0 to 2 of 3. Output comparison against the original in its old environment was
run on the three pandas pages whose 2021 environment could be built: 59 of 60 comparable blocks
printed the same output, and the one difference (an edit that turned every column into `object`
dtype) is what the change log's "Output compared with the original" section now flags. The other
pages were not compared.

What does not improve is mostly not version rot, and the change log says so per block ("Likely
cause: ..."): a snippet that uses `List` or `Alice` without defining it, imports of the author's own
project (`app.schemas`, `yourapplication`), a MySQL server, an intentional broadcasting error. Real
misses remain too: a FastAPI/pydantic docs page (`name = 'Jane Doe'` without an annotation) stays
at 0 of 7 because the error names no API the corpus can match.

With repair and prose on (the demo and service default), 9 more blocks run (forgotten imports and
a missing package on four pages) and 25 prose edits were applied on five pages, all of the kind
"`append` -> `concat`" with the sentences that describe it; nothing was edited on the pages where
no code changed. I read the prose edits of three pages and they agreed with the code, but there are
no hand references for real pages, so this is a read-through, not a score.

The first six pages (pandas x4, FastAPI x2), with every mode on, `scripts/demo.py` defaults:

| tutorial | blocks | untouched | after |
|---|---|---|---|
| pandas add-row snippets | 3 | 0 | 2 |
| pandas add-row guide | 12 | 8 | 11 |
| pandas tutorial (notebook) | 161 | 149 | 155 |
| pandas for beginners | 52 | 45 | 47 |
| FastAPI + pydantic schemas | 8 | 3 | 5 |
| FastAPI project tutorial | 36 | 21 | 26 |
| **total** | **272** | **226** | **246** |

Two things this run taught: a static import guess can be wrong (the FastAPI page's `Path` is
`fastapi.Path`; the import is now taken from the tutorial's own text first, anywhere, including
prose, and a guess is labelled "assumed" in the change log), and several "undefined names" in the
notebook-style pages (`Index([...])`, `array([...])`) are printed output pasted into code cells, which
the change log now says.

## Official documentation pages (25 links)

Version-pinned pages from the projects' own docs (pandas 1.3/1.5, pydantic 1.10, NumPy 1.17/1.19,
SQLAlchemy 1.3/1.4, scikit-learn 0.23/0.24, Matplotlib 2.2.5/3.3) were fetched with
`scripts/web_to_md.py` and run with every mode on by `scripts/real_eval.py`. Two things differ from
the pages above: the docs are written as interactive sessions (`>>> code`, output lines), so the
converter extracts the code and drops prompts and output; and a docs URL names the library version
it describes, so the converter puts a `pip install lib==X` line at the top (information the tool
added, not the author's).

| group | pages that ran | python blocks | run as-is | after |
|---|---|---|---|---|
| SQLAlchemy 1.3 / 1.4 and pydantic 1.10 | 5 | 300 | 124 | 201 |
| NumPy 1.17 / 1.19 | 5 | 272 | 171 | 206 |
| Matplotlib 2.2.5 | 4 | 49 | 39 | 44 |
| scikit-learn 0.23 / 0.24 | 3 | 33 | 12 | 14 |
| **total** | **17** | **654** | **346** | **465** |

Seven of the 25 links had nothing to run (landing pages, an example page with output only, two
pydantic pages whose code is drawn by JavaScript so the download contains none) and one URL (pandas
1.3 tutorials index) is a 404. 33 prose edits were applied and 124 cited API edits, 17 version pins
and 4 defect fixes were made. Largest gains: the SQLAlchemy 1.3 core tutorial 35 to 98 of 105 blocks,
the NumPy 1.17 release notes page 2 to 33 of 42, the NumPy beginners guide runs 122 of 124 blocks (115 before the sandbox could write
files, which is a limit of my sandbox, not of NumPy).

Why the rest still fails: about 160 of the ~190 failing blocks are downstream of just 13 root blocks
(a block fails, everything that needs its names fails after it). The roots are: an object defined
only in an earlier doctest the page never ran (`our_user`, `actual`), a sample image from `scipy`
(`scipy.misc.face`, scipy is not a supported library: one page, 34 blocks), a download from the
network (20 newsgroups), a table that only exists in a database the page never built, and two real
API changes the release notes the tool holds do not mention in a way it can find (`np.reshape(newshape=)`
and Matplotlib's `linthreshy`): a limit of the corpus, not of the pipeline.

Bugs these pages found, all fixed: the Matplotlib 2.2.5 old environment asked for Python 3.6, which
the in-container runner cannot run (any old-environment failure is now only a warning, and the
floor is 3.7); blocks could not write a file or build a Matplotlib cache on the read-only
filesystem (they now get a writable scratch directory inside the tmpfs).

## Fresh hold-out (run once)

After the system was finished, 19 pages the tool had never seen were converted and run **once**,
every mode on, with no changes made in response to the numbers before they were written down. The
pages: 6 SciPy (1.10 tutorials for optimize, stats, signal and linalg; the 0.19 sparse and 1.6
`misc.derivative` reference pages), and fresh pages for pandas 1.5.3 (groupby, merging), pydantic
1.10 (settings, types, model config), NumPy 1.19 (indexing, creation), SQLAlchemy 1.3 (session
basics, loading relationships), scikit-learn 0.24 (preprocessing, tree) and Matplotlib 3.3.0 (legend
guide, text intro). I chose the pages myself; four more pages sent by the user were dropped because
they had nothing runnable (a list of function names, a table of methods, and a page whose code has no
indentation in its own HTML).

| | pages | python blocks | run as-is | after |
|---|---|---|---|---|
| SciPy | 6 | 246 | 233 | 242 |
| pandas 1.5.3 | 2 | 132 | 0 | 97 |
| NumPy 1.19 | 2 | 37 | 0 | 35 |
| SQLAlchemy 1.3 | 2 | 80 | 7 | 9 |
| pydantic 1.10 | 3 | 37 | 30 | 35 |
| scikit-learn 0.24 | 2 | 43 | 40 | 42 |
| Matplotlib 3.3.0 | 2 | 31 | 28 | 30 |
| **total** | **19** | **606** | **338 (56%)** | **490 (81%)** |

How to read it: the 0 "as-is" for pandas and NumPy is mostly because the documentation snippets
leave out `import pandas as pd` and `import numpy as np`; most of the lift there is the defect-repair
step adding imports, not version fixes. The 1.10 SciPy tutorials mostly already ran on SciPy 1.18, so
they show the tool leaving working code alone (71 of 71 and 106 of 106 untouched); the real SciPy
rot is on the old pages (`misc.derivative` was removed in SciPy 1.12; `signal` 34 to 40 of 44).
Of the 116 blocks still failing, 104 are downstream of one earlier failure; the 13 root failures are
a name the page defines only in its prose (`get_letter_type`, `Base`, `ax`, `objs`), an optional
package (`h5py`), a Postgres driver, two dataset downloads (the sandbox has no network), two
examples that raise an error on purpose, and one example using `min` and `max` before defining them.
None of the 13 is a version change the tool missed.
