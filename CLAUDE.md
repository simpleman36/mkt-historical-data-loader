# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is intended to become a historical market data loader that pulls data from Interactive Brokers (IBKR) through its Python API. As of the initial commit, no loader code exists yet: the only file is `main.py`, which is the unmodified PyCharm "sample Python script" template (`print_hi`). Treat it as a placeholder to be replaced, not as an established pattern.

There is currently no dependency manifest (`requirements.txt` / `pyproject.toml`), no test suite, no linter or formatter config, and no CI. When adding any of these, update this file with the actual commands.

## Commands

- Run: `python main.py`

## Architecture

Not yet established. Update this section once the IBKR connection, request pacing, and storage layers are in place.
