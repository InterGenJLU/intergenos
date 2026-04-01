#!/bin/bash
# InterGenOS Kernel Config Merger
#
# Merges all fragment configs into a single .config file,
# then runs olddefconfig to resolve dependencies.
#
# Usage:
#   ./scripts/merge-kernel-config.sh /path/to/linux-6.18.10
#
# Fragments are read from config/kernel/fragments/ in numeric order.

set -e

KERNEL_DIR="${1:?Usage: $0 /path/to/kernel-source}"
FRAGMENT_DIR="$(dirname "$0")/../config/kernel/fragments"

if [ ! -d "$KERNEL_DIR" ]; then
    echo "ERROR: Kernel source directory not found: $KERNEL_DIR"
    exit 1
fi

if [ ! -d "$FRAGMENT_DIR" ]; then
    echo "ERROR: Fragment directory not found: $FRAGMENT_DIR"
    exit 1
fi

echo "InterGenOS Kernel Config Merger"
echo "================================"
echo "Kernel source: $KERNEL_DIR"
echo "Fragments:     $FRAGMENT_DIR"
echo ""

# List fragments
FRAGMENTS=$(ls "$FRAGMENT_DIR"/*.config 2>/dev/null | sort)
if [ -z "$FRAGMENTS" ]; then
    echo "ERROR: No config fragments found in $FRAGMENT_DIR"
    exit 1
fi

echo "Merging fragments:"
for f in $FRAGMENTS; do
    count=$(grep -c '^CONFIG_' "$f" || echo 0)
    echo "  $(basename "$f") ($count options)"
done
echo ""

# Merge using kernel's merge_config.sh
cd "$KERNEL_DIR"

if [ ! -f "scripts/kconfig/merge_config.sh" ]; then
    echo "ERROR: merge_config.sh not found in kernel source"
    echo "       Make sure you're pointing at an extracted kernel tree"
    exit 1
fi

scripts/kconfig/merge_config.sh -m $FRAGMENTS

echo ""
echo "Running olddefconfig to resolve dependencies..."
make olddefconfig

echo ""
echo "Config written to: $KERNEL_DIR/.config"

# Show summary
total=$(grep -c '^CONFIG_' .config || echo 0)
builtin=$(grep -c '=y$' .config || echo 0)
modules=$(grep -c '=m$' .config || echo 0)
echo ""
echo "Summary:"
echo "  Total options: $total"
echo "  Built-in (=y): $builtin"
echo "  Modules (=m):  $modules"
echo ""
echo "Review with: make menuconfig"
echo "Build with:  make -j\$(nproc)"
