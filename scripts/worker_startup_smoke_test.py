#!/usr/bin/env python3
"""
Worker startup smoke test for ll_ol_fallback inference chain.

Demonstrates:
1. InferenceGateway with ll_ol_fallback provider
2. Which backend is actually active (llama_cpp vs ollama)
3. Fallback behavior when primary is unavailable

Usage:
    python scripts/worker_startup_smoke_test.py
"""

import asyncio
import logging
import os
import sys

# Setup logging to match worker output
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s | %(name)s | %(message)s",
)

# Ensure we can import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    """Run startup smoke test."""
    from services.config import Settings
    from services.inference.gateway import InferenceGateway
    from services.inference.queue import RedisStreamsInferenceWorker
    from services.contracts import InferenceRequest

    logger = logging.getLogger(__name__)

    logger.info("=" * 80)
    logger.info("WORKER STARTUP SMOKE TEST")
    logger.info("=" * 80)

    # Initialize gateway with ll_ol_fallback as default
    logger.info(f"DEFAULT_LLM_PROVIDER: {Settings.DEFAULT_LLM_PROVIDER}")
    logger.info(f"LLAMA_CPP_BASE_URL: {Settings.LLAMA_CPP_BASE_URL}")
    logger.info(f"LLAMA_CPP_MODEL: {Settings.LLAMA_CPP_MODEL}")
    logger.info(f"OLLAMA_HOST: {Settings.OLLAMA_HOST}")
    logger.info(f"OLLAMA_PORT: {Settings.OLLAMA_PORT}")
    logger.info("")

    try:
        gateway = InferenceGateway()
        logger.info("✓ InferenceGateway initialized")
    except Exception as e:
        logger.error(f"✗ Failed to initialize gateway: {e}", exc_info=True)
        return 1

    # Create a minimal worker for smoke test
    try:
        worker = RedisStreamsInferenceWorker(
            inference_gateway=gateway,
            redis_url=Settings.REDIS_URL,
            request_stream=Settings.INFERENCE_QUEUE_REQUEST_STREAM,
            consumer_group="smoke-test-group",
            consumer_name="smoke-test-worker",
        )
        logger.info("✓ RedisStreamsInferenceWorker initialized")
    except Exception as e:
        logger.error(f"✗ Failed to initialize worker: {e}", exc_info=True)
        return 1

    # Run startup smoke test
    logger.info("")
    logger.info("Running inference stack smoke test...")
    logger.info("-" * 80)
    
    try:
        await worker.startup_smoke_test(verbose=True)
        logger.info("-" * 80)
        logger.info("✓ SMOKE TEST PASSED")
        logger.info("=" * 80)
        return 0
    except Exception as e:
        logger.error(f"✗ SMOKE TEST FAILED: {e}", exc_info=True)
        logger.info("=" * 80)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
