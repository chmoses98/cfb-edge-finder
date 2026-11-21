"""CFB game-outcome challenger (research only; nothing in production imports this)."""
import os

# Multi-threaded OpenBLAS is pathologically slow on small solves in this container; one thread is ~100x faster.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
