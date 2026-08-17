#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# training meta-package — no source, no build (gaming-meta precedent).
#
# Installs:
#   /usr/share/doc/training/README — user documentation for the
#   training stack this meta pulls in.
#
# Security-only-alignment filter notes:
#   - No SUID binaries, no daemons, no kernel modules, no udev rules,
#     no config drops. A README is the only payload.
#   - Every member of the set builds from sha256-pinned upstream
#     sources inside the InterGenOS build chroot; this meta only names
#     the set.

configure() {
    set -e
    : # no-op (meta-package, no source code)
}

build() {
    set -e
    : # no-op (meta-package, no source code)
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/doc/training"
    cat > "${DESTDIR}/usr/share/doc/training/README" <<'EOF'
training meta-package — InterGenOS
===================================

This package is a convenience meta-package that installs the complete
InterGenOS model-training stack in one step:

  unsloth / unsloth-zoo — memory-efficient LoRA/QLoRA fine-tuning on
                          top of the Hugging Face ecosystem

  transformers, trl, peft, datasets, accelerate, diffusers,
  huggingface-hub and their support libraries — the Hugging Face
  training and data pipeline

  pytorch (ROCm), torchvision, torchao, xformers, triton,
  bitsandbytes — the GPU compute layer, built from source against
  the InterGenOS ROCm platform for AMD GPUs

  tokenizers, safetensors, sentencepiece, pyarrow / arrow-cpp,
  pandas — tokenization, tensor serialization, and data handling

  the ROCm runtime closure (rocm-hip, rocblas, miopen, hipblaslt,
  rccl, and their siblings) — pulled in automatically as the
  runtime dependencies of the stack above

Everything in this set is mirror-hosted: it installs post-install over
the network (pkm install training) and is not on the install ISO,
which keeps the ISO lean — the GPU compute trees are multi-gigabyte.

Every package in the set builds from sha256-pinned upstream sources
inside the InterGenOS build chroot; nothing here is a prebuilt binary
download.

For the ROCm platform itself (compilers, profilers, debuggers, and
the full GPU SDK surface beyond what training needs at runtime), see
the companion meta-package: pkm install compute.
EOF
}
