"""Small pyfaidx-backed genome reader adapted from MarinDNA.

The remote-opening pattern is adapted from
``Open-Athena/marin-dna/src/marin_dna/data/genome.py`` (Apache-2.0). It keeps
the indexed FASTA remote and lets pyfaidx/fsspec satisfy sequence slices with
HTTP range requests.
"""

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pyfaidx import Fasta


class Genome:
    """Read 0-based, half-open intervals from a local or remote indexed FASTA."""

    def __init__(
        self,
        path: str | Path,
        *,
        subset_chroms: set[str] | None = None,
        storage_options: dict[str, Any] | None = None,
    ) -> None:
        self._path = str(path)
        self._is_remote = urlparse(self._path).scheme not in ("", "file")
        self._storage_options = dict(storage_options or {})
        self._subset_chroms = set(subset_chroms) if subset_chroms else None
        self._chrom_sizes: dict[str, int] | None = None
        self._fa: Fasta | None = None
        self._fa_pid = -1

    def _open_fasta(self) -> Fasta:
        if self._is_remote:
            import fsspec

            return Fasta(fsspec.open(self._path, **self._storage_options), as_raw=True)
        return Fasta(self._path, as_raw=True)

    def _fasta(self) -> Fasta:
        pid = os.getpid()
        if self._fa is None or self._fa_pid != pid:
            self.close()
            self._fa = self._open_fasta()
            self._fa_pid = pid
        return self._fa

    def _ensure_probed(self) -> None:
        if self._chrom_sizes is not None:
            return
        fasta = self._fasta()
        keys = [
            key
            for key in fasta.keys()
            if self._subset_chroms is None or key in self._subset_chroms
        ]
        self._chrom_sizes = {key: len(fasta[key]) for key in keys}

    @property
    def chroms(self) -> dict[str, int]:
        self._ensure_probed()
        assert self._chrom_sizes is not None
        return dict(self._chrom_sizes)

    def __call__(self, chrom: str, start: int, end: int) -> str:
        """Return a forward-strand sequence, padding out-of-bounds bases with N."""

        self._ensure_probed()
        assert self._chrom_sizes is not None
        if chrom not in self._chrom_sizes:
            raise ValueError(f"chromosome {chrom} not found in genome")
        if start > end:
            raise ValueError(f"start {start} must be less than or equal to end {end}")
        chrom_size = self._chrom_sizes[chrom]
        if end < 0 or start >= chrom_size:
            raise ValueError(f"interval {chrom}:{start}-{end} is outside the chromosome")

        sequence = self._fasta()[chrom][max(start, 0) : min(end, chrom_size)]
        if start < 0:
            sequence = "N" * (-start) + sequence
        if end > chrom_size:
            sequence += "N" * (end - chrom_size)
        return sequence

    def close(self) -> None:
        if self._fa is not None:
            self._fa.close()
        self._fa = None
        self._fa_pid = -1

    def __enter__(self) -> Genome:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["_fa"] = None
        state["_fa_pid"] = -1
        return state
