import asyncio
import logging
from unittest.mock import patch, MagicMock
from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

import main

# Mock OIDC token verification to skip auth
main.verify_oidc_token = MagicMock(return_value=None)

# Mock Request
class MockRequest:
    def __init__(self, ce_subject):
        self.headers = {"ce-subject": ce_subject}
        self.json = MagicMock()

async def run_test():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("test_manual_retry")

    request = MockRequest("objects/1a03d7314bc1bcb5.pdf")

    logger.info("Starting test: POST / with mock event")
    response = await main.receive_event(request)
    logger.info("Response: %s", response)

if __name__ == "__main__":
    # We need to run it in asyncio
    asyncio.run(run_test())
