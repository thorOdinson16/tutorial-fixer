# Spend cap

A hard budget on LLM spend.

`TRF_MAX_SPEND_USD` (with `TRF_PRICE_IN_PER_M` and `TRF_PRICE_OUT_PER_M`, USD per million tokens;
use prices at or above the real ones) puts a hard budget on the OpenAI backend. Before every call
the backend assumes the worst case (a prompt at 2 characters per token and every allowed completion
token generated), refuses to send it if the running total in `TRF_SPEND_LEDGER`
(default `.trf_cache/spend.json`, shared by all processes) plus that worst case would pass 90% of
the cap, and raises `BudgetExceeded`, which no stage catches. `run_eval` then stops without logging
the case as a crash. Billed tokens are counted per run (`tokens` in each eval log, printed by
`demo.py`). Cached responses cost nothing.
