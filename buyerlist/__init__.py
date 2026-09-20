"""buyerlist — SMB website -> shortlist of plausible PE acquirers.

Loading .env here means every entry point into the package picks up credentials,
regardless of which module gets imported first.
"""

from pathlib import Path

from dotenv import load_dotenv

# override=False: a real environment variable always wins over the file, so
# `ANTHROPIC_API_KEY=... python -m buyerlist` behaves as expected.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
