"""pkm — InterGenOS Package Manager

A natural-language CLI for installing, removing, querying, and managing
packages on a running InterGenOS system. Uses SQLite for fast queries
while maintaining human-readable text manifests for transparency.

Usage:
    pkm install <pkg>           Install a package from archive
    pkm remove <pkg>            Remove a package (checks dependencies)
    pkm list installed          List installed packages
    pkm search <term>           Search packages by name/description
    pkm info <pkg>              Show package details
    pkm files <pkg>             List files in a package
    pkm provides <file>         Find which package owns a file
    pkm verify <pkg>            Verify package integrity
    pkm depends <pkg>           Show dependency tree
    pkm history                 Show operation history
"""

__version__ = "0.1.0"
