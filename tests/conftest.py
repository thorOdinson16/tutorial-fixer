"""Keep a developer's local .env (real keys, models) out of the test environment."""

import os

os.environ.setdefault("TRF_NO_DOTENV", "1")
