"""Pre-flight algorithm fitness check.

Detects "this algorithm + this data + this hardware = doesn't fit" before
kicking off a long-running operation. Returns concrete alternatives when
working set exceeds RAM ceiling.

Scope: ALGORITHM defects only (working set vs RAM, complexity vs scale,
default config inappropriateness). Infrastructure issues (disk topology,
network, JBOD state) are out of scope — handled by separate ops discipline.

Usage:
    from safety_rails import preflight

    result = preflight.hnsw_build(
        n_rows=165_000_000,
        dim=1024,
        bytes_per_dim=2,           # halfvec
        m=16,                      # hnsw graph degree
        ram_bytes=125 * 1024**3,   # mars 125 GB
    )
    if result.rejected:
        print(result.reason)
        print("alternatives:")
        for alt in result.alternatives:
            print(f"  - {alt}")
        sys.exit(2)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

GB = 1024 ** 3


@dataclass
class FitnessResult:
    operation: str
    rejected: bool
    peak_mem_gb: float
    ram_gb: float
    headroom_gb: float
    reason: str = ""
    alternatives: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def hnsw_build(
    n_rows: int,
    dim: int,
    bytes_per_dim: int = 2,
    m: int = 16,
    ef_construction: int = 64,
    ram_bytes: int = 125 * GB,
    parallel_workers: int = 4,
    safety_factor: float = 0.8,
) -> FitnessResult:
    """Predict whether a pgvector / similar HNSW build will fit RAM."""
    working_set = n_rows * dim * bytes_per_dim
    # graph overhead: m=16 default → ~50% extra for edges + meta
    graph_overhead_factor = 1.0 + (m / 32.0)
    peak_mem = working_set * graph_overhead_factor
    target = ram_bytes * safety_factor

    result = FitnessResult(
        operation=f"HNSW build (n={n_rows:,}, dim={dim}, m={m})",
        rejected=peak_mem > target,
        peak_mem_gb=peak_mem / GB,
        ram_gb=ram_bytes / GB,
        headroom_gb=(target - peak_mem) / GB,
    )

    if not result.rejected:
        result.notes.append(
            f"fits: peak {peak_mem/GB:.1f} GB <= {safety_factor*100:.0f}% of RAM "
            f"({target/GB:.1f} GB)"
        )
        return result

    over_factor = peak_mem / target
    result.reason = (
        f"HNSW peak mem {peak_mem/GB:.0f} GB > {safety_factor*100:.0f}% of RAM "
        f"({target/GB:.0f} GB), over by {over_factor:.2f}x"
    )

    # alternatives ranked by typical pragmatic value
    n_shards_for_fit = math.ceil(over_factor)
    quantized_peak_gb = peak_mem / 16 / GB  # int8 SBQ ~ 16x compression
    bigger_ram_gb = math.ceil(peak_mem / GB / 0.7)

    result.alternatives = [
        f"shard ×{n_shards_for_fit} via partial WHERE indexes "
        f"(each shard ~{result.peak_mem_gb/n_shards_for_fit:.0f} GB working set)",
        f"int8 SBQ quantization (pgvectorscale) → peak {quantized_peak_gb:.0f} GB, "
        f"recall -1~3pt recoverable via Phase-7 rerank",
        f"IVFFlat instead of HNSW (no graph overhead, peak "
        f"{working_set/GB:.0f} GB still > RAM but much less spill)",
        f"larger-RAM host (need {bigger_ram_gb} GB+ instance)",
        f"sparse-first + dense brute-force rerank (HNSW skip entirely)",
    ]
    return result


def ivfflat_build(
    n_rows: int,
    dim: int,
    bytes_per_dim: int = 2,
    nlist: int = 1000,
    ram_bytes: int = 125 * GB,
    safety_factor: float = 0.8,
) -> FitnessResult:
    """Predict IVFFlat build fitness. Less peak mem than HNSW (no graph)."""
    working_set = n_rows * dim * bytes_per_dim
    # ivfflat overhead: small (cluster centers + assignments)
    centers_mem = nlist * dim * bytes_per_dim
    peak_mem = working_set + centers_mem
    target = ram_bytes * safety_factor

    result = FitnessResult(
        operation=f"IVFFlat build (n={n_rows:,}, dim={dim}, nlist={nlist})",
        rejected=peak_mem > target,
        peak_mem_gb=peak_mem / GB,
        ram_gb=ram_bytes / GB,
        headroom_gb=(target - peak_mem) / GB,
    )
    if not result.rejected:
        result.notes.append(
            f"fits: peak {peak_mem/GB:.1f} GB <= {safety_factor*100:.0f}% of RAM"
        )
        return result

    over_factor = peak_mem / target
    result.reason = f"IVFFlat peak {peak_mem/GB:.0f} GB exceeds {target/GB:.0f} GB by {over_factor:.2f}x"
    result.alternatives = [
        f"shard ×{math.ceil(over_factor)} via partial WHERE",
        f"int8 SBQ quantization → peak {peak_mem/16/GB:.0f} GB",
        f"larger-RAM host",
    ]
    return result


def diskann_sbq_build(
    n_rows: int,
    dim: int,
    ram_bytes: int = 125 * GB,
    safety_factor: float = 0.8,
) -> FitnessResult:
    """pgvectorscale DiskANN with SBQ — disk-friendly + 16x compression."""
    # SBQ: 1 bit per dim ≈ dim/8 bytes per row
    working_set = n_rows * (dim // 8)
    # DiskANN graph overhead small (memory-mapped, designed for disk)
    peak_mem = working_set * 1.2
    target = ram_bytes * safety_factor

    result = FitnessResult(
        operation=f"DiskANN+SBQ build (n={n_rows:,}, dim={dim})",
        rejected=peak_mem > target,
        peak_mem_gb=peak_mem / GB,
        ram_gb=ram_bytes / GB,
        headroom_gb=(target - peak_mem) / GB,
    )
    result.notes.append(
        f"DiskANN+SBQ uses disk-friendly graph, designed for working-set > RAM"
    )
    if not result.rejected:
        result.notes.append(
            f"fits comfortably: peak {peak_mem/GB:.1f} GB"
        )
    return result
