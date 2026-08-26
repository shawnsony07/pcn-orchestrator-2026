import asyncio
import os
import logging
from dotenv import load_dotenv

load_dotenv(r"c:\dev\pcn-orchestrator-2026\.env")

# Mock the OIDC token verification before importing main
import main
main.verify_oidc_token = lambda headers: True

async def run():
    logging.info("Starting test execution...")
    
    class MockRequest:
        headers = {
            "Authorization": "Bearer fake",
            "ce-subject": "objects/1a03d7314bc1bcb5.pdf"
        }
        
    response = await main.receive_event(MockRequest())
    logging.info("Final Response: %s", response)

if __name__ == "__main__":
    asyncio.run(run())
