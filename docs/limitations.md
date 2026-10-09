# Known limitations

What it does not do, and what the numbers do not show.

- One model, one run per config, 33 evaluation tutorials, temperature 0: differences of a point or
  two are noise. A reasoning model on a small local GPU returned empty replies on every diff call,
  which is why the eval used a hosted model. Several fixes were found by running the dev and libs
  sets, so those numbers are optimistic; only the held-out set was not tuned on.
- A change can only be cited if it is in the release notes the corpus holds (the sources start at
  NumPy 1.17, SciPy 1.2, scikit-learn 0.18, Matplotlib 2.2, SQLAlchemy 1.3, pandas 1.0, pydantic 1.x), and the
  corpus covers each project's own notes, not Stack Overflow answers or third-party docs.
- Old environments cannot be built for very old pins (NumPy 1.11 on a Python that has wheels for
  it, Matplotlib 1.5): the original-vs-updated output comparison is then skipped and a warning says
  so.
- `query_rewrite` is pattern matching on common Python errors. Errors that carry no symbol (a
  changed default producing different output, cascades from an earlier block) leave the block
  `unresolved` rather than guessed at. The loop's gain is measured in Results.
- The web service runs jobs in a worker pool (`TRF_API_WORKERS`, default 1) and keeps the last 100
  in `.trf_cache/jobs`; there is no authentication, so run it locally.


- Fenced blocks indented more than three spaces (e.g. inside list items) are treated as prose.
- Method-call symbol extraction uses a light, flow-insensitive type tracker, not real type inference.
- Blocks share one namespace, so a failing early block can make later blocks fail too; those are
  tagged "may be a downstream effect" in `last_error`, which is a hint, not a diagnosis.
- Prose is edited only with `update_prose` on, one sentence at a time and only where it mentions a
  changed API or an old version; output blocks (printed results shown in the text) are not
  regenerated, and prose that describes behaviour rather than names a symbol is not found.
- Security warnings are reported, not repaired, unless a cited fix verifies; warnings the tool cannot
  tie to a symbol the block uses are reported only.
- Defect repair covers forgotten imports and packages. It does not invent data: an undefined
  variable stays a suggestion unless placeholders are explicitly enabled, and a tutorial that needs
  its author's project, a data file or a server stays unresolved.
- Install-block updates are deterministic and only handle simple `pip install` lines.
- With a reasoning model on a small local GPU each LLM call takes tens of seconds; the diff stage is
  batched per library for this reason.
