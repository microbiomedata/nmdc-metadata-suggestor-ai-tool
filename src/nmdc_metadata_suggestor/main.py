"""Main entry point for the NMDC Metadata Suggestor application."""

import sys

from loguru import logger

from .config import settings
from .llm_client import LLMClient


def setup_logging() -> None:
    """Configure logging for the application."""
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level=settings.log_level,
    )


def main() -> None:
    """Main application entry point."""
    setup_logging()

    logger.info("Starting NMDC Metadata Suggestor")
    logger.info(f"Default model: {settings.default_model}")

    # Example usage of the LLM client
    try:
        client = LLMClient(provider="openai")

        # Example prompt for metadata suggestion
        example_prompt = """
        Given the following sample information:
        - Sample type: soil
        - Location: temperate forest
        - Depth: 10cm

        Suggest appropriate metadata fields and values for this environmental sample.
        """

        logger.info("Sending example prompt to LLM...")
        response = client.generate(example_prompt)

        logger.success("Received response from LLM:")
        print("\n" + "=" * 80)
        print(response)
        print("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Error: {e}")
        logger.info("Make sure to set your API keys in the .env file")
        sys.exit(1)

    logger.info("NMDC Metadata Suggestor completed successfully")


if __name__ == "__main__":
    main()
