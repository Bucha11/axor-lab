"""`python -m lab_runner` — same entry point as the `axor-lab` console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
