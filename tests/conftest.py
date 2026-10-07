import os
from pathlib import Path

import pytest

# Must be set before cv_server.server is imported: it loads the data at import time.
# The real data/ folder is gitignored, so tests always run against small fixtures.
os.environ["CV_SERVER_DATA_DIR"] = str(Path(__file__).parent / "fixtures")


@pytest.fixture
def anyio_backend():
    """Run @pytest.mark.anyio tests on asyncio (anyio ships with mcp)."""
    return "asyncio"
