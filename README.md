# Synthetic Sudoku Conversation Data Generator

This project generates synthetic conversation datasets centered around Sudoku interactions.

## Features
- Single-turn and multi-turn conversation generation
- Configurable job management with progress tracking
- CSV outputs organized per job
- Centralized configuration for defaults and prompts

## Setup
1. Create and activate a virtual environment
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Add your API key to `.env`:
   ```dotenv
   OPENAI_API_KEY=your_api_key_here
   OPENAI_MODEL=gpt-4o-mini
   ```
4. Run the CLI:
   ```bash
   python -m src.generator.cli run --samples 5
   ```

If `OPENAI_API_KEY` is not set, the generator falls back to the built-in mock responses.
