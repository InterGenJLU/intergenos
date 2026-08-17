#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# llama-cpp-cuda post-install — say what is still missing, at install time.
#
# This engine links NVIDIA's CUDA runtime libraries, which live at /opt/cuda
# and get there through the cuda-toolkit download helper. pkm will not run a
# download helper on a user's behalf, because doing so would accept a vendor
# licence for them, so `pkm install llama-cpp-cuda` puts the helper on the
# machine but leaves the toolkit unfetched. Without it these binaries fail to
# start with a missing-soname error — loud, but it names a library rather than
# the command that fixes it.
#
# This hook does not install anything and never fails the transaction. It
# reports the state of the machine and names the one command.

set -u

PREFIX=/opt/llama-cpp-cuda
CUDA_PREFIX=/opt/cuda

echo ""
echo "  llama-cpp-cuda installed to ${PREFIX}"
echo ""

missing=0

if [ ! -e "${CUDA_PREFIX}/lib64/libcudart.so" ]; then
    echo "  The CUDA runtime is NOT on this machine yet."
    echo "  This engine cannot start until it is. Run:"
    echo ""
    echo "      sudo pkm install cuda-toolkit"
    echo ""
    echo "  That downloads the toolkit from NVIDIA (about 4.1 GB) after showing"
    echo "  you the licence. InterGenOS does not redistribute it."
    echo ""
    missing=1
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "  The NVIDIA driver does not appear to be installed. This engine also"
    echo "  needs the CUDA driver library it provides. Run:"
    echo ""
    echo "      sudo pkm install nvidia"
    echo ""
    missing=1
fi

if [ "$missing" -eq 0 ]; then
    echo "  The CUDA toolkit and the NVIDIA driver are both present, so the"
    echo "  engine is ready to run:"
    echo ""
    echo "      ${PREFIX}/bin/llama-server --model <model.gguf> --n-gpu-layers 99"
    echo ""
    echo "  Before committing to it, measure it against the engine already"
    echo "  installed — on some cards and some models the Vulkan engine that"
    echo "  ships by default is the faster of the two:"
    echo ""
    echo "      ${PREFIX}/bin/llama-bench -m <model.gguf> -ngl 99"
    echo "      /usr/bin/llama-bench      -m <model.gguf> -ngl 99"
    echo ""
fi

echo "  Notes: /usr/share/doc/llama-cpp-cuda/CUDA-ENGINE.md"
echo ""

exit 0
