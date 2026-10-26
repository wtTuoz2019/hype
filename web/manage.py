#!/usr/bin/env python3
import os
import sys
from pathlib import Path

HYPE_ROOT = Path(__file__).resolve().parents[1]
if str(HYPE_ROOT) not in sys.path:
    sys.path.insert(0, str(HYPE_ROOT))

def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
