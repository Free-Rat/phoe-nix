#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    lines = path.read_text(encoding="utf-8").splitlines()

    filtered: list[str] = []
    skip_block = False
    brace_depth = 0
    for line in lines:
        stripped = line.strip()
        if not skip_block and stripped.startswith('fileSystems."/nix/store" ='):
            skip_block = True
            brace_depth = line.count("{") - line.count("}")
            continue
        if skip_block:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0 and stripped.endswith(";"):
                skip_block = False
            continue
        filtered.append(line)

    path.write_text("\n".join(filtered) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
