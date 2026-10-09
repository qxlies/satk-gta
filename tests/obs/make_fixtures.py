"""Regenerate the golden container fixtures: ``python tests/obs/make_fixtures.py`` (run from the repository root).

``golden-plain.saerec`` is stored without compression, so its bytes are fully specified by the format and a test compares
them exactly. ``golden-zlib.saenet`` uses zlib: its bytes depend on the zlib build, so tests only read it.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from satk.obs import container, sample  # noqa: E402

FIXTURES = HERE / "fixtures"


def golden_plain_records():
    return sample.client_stream(seconds=0.5, fps=30)


def golden_zlib_records():
    return sample.net_stream(rows=30)


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    container.pack_records(golden_plain_records(), FIXTURES / "golden-plain.saerec", compress=False, chunk_records=8)
    container.pack_records(golden_zlib_records(), FIXTURES / "golden-zlib.saenet", compress=True, chunk_records=10)


if __name__ == "__main__":
    main()
