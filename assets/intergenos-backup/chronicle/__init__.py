# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Chronicle — the InterGenOS backup engine.

A Time-Machine-class, copy-on-write-free backup utility for an ext4-on-LUKS2
installation. One privileged engine, three data layers (config-state, user
data, restore points), two clients (the ``chronicle`` CLI + ``Chronicle`` GTK
GUI, and the automation client — ``chronicled`` and the pkm pre-transaction
hook).

The mechanism follows one ground truth: there is no filesystem-level snapshot
available on this stack, so capture is file-level, built from the two primitives
the distribution already relies on — ``rsync --link-dest`` hardlink rotation for
bulk user data, and a sha256 content-addressed store for the small, densely
shared config state and restore points. Every version is self-verifying: blobs
are named by their hash, and a version commits only when its root hash is
written last.

Module map:
    paths        on-disk layout constants (local store + target)
    config       chronicle.conf parsing + working-hours / off-peak windows
    cas          content-addressed blob store (put/get/verify/gc)
    manifest     the commit-last version manifest + root-hash hash tree
    userdata     hardlink-rotation user-data layer
    configstate  config-state layer (reuses the pkm config baseline)
    restorepoint restore-point layer (consumes the pkm pre-transaction footprint)
    enumerate    target-volume enumeration + honesty labels + setup options
    retention    graduated thinning + cap-aware volume-full pruning
    queue        durable off-peak capture queue
    engine       the orchestrator that owns all state + implements the verbs
    api          the local JSON IPC surface (Unix socket) both clients consume
"""

__version__ = "1.0"
