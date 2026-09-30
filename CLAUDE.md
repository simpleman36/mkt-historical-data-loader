# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is intended to become a historical market data loader that pulls data from Interactive Brokers (IBKR) through its Python API. As of the initial commit it contains only `main.py`, which is the unmodified PyCharm "sample Python script" template (`print_hi`). There is no loader code, package layout, dependency manifest, test suite, linter config, or README yet.

## Commands

The only runnable entry point is:

```bash
python main.py
```

No build, lint, or test tooling is configured. When adding any (e.g. `requirements.txt`/`pyproject.toml`, pytest, a linter), update this section with the exact commands, including how to run a single test.

## Architecture

None established yet. Once real code lands, replace this section with the big-picture structure (how the IBKR connection is managed, how historical data requests are issued and paced, and where results are stored).
