"""Dependency-derived execution ordering — the ONE topological sorter.

This module is the single home of the Kahn topological sort used everywhere a
package set must execute in dependency order: pkm's upgrade/install
transaction planning (pkm/cli.py) and Forge's full-system install set
(installer/backend/packages.py). It exists so ordering is DERIVED from the
dependency graph in exactly one implementation — a second sorter, or a
hand-curated ordering list standing in for a derivation, is precisely the
drift class that let Forge install alphabetically for months while pkm
upgrades were correctly ordered (the intel-ucode rc=127 hook failure on
ge9b-10: `i` sorts before `r`, so the hook ran on a target that had bash but
not the readline it loads).

Semantics (identical to the sorter that shipped in pkm/cli.py at f7639cf1f):

- Edges are restricted to names inside the set — a dependency that is not
  itself being ordered imposes no constraint.
- Within a topological rank, ready nodes are emitted by ``ready_sort_key``
  (alphabetical by default) so the order is fully deterministic.
- A correct package graph is acyclic; a corrupt or hand-edited graph can
  declare a cycle, which cannot be topologically ordered. Cycle nodes are
  grouped by connected component, each group sorted alphabetically, and the
  groups appended (alphabetically by first member) after the acyclic prefix —
  execution still proceeds and the CALLER must loud-note each cycle group.
  Fail-open-with-loud-report is deliberate here: refusing to order at all
  would turn an index defect into an unbootable install.
"""


def topological_order(names, deps_by_name, ready_sort_key=None):
    """Order ``names`` so each name's in-set dependencies precede it.

    Args:
        names: iterable of package names to order.
        deps_by_name: mapping name -> iterable of names it depends on.
            Names not present in ``names`` are ignored (no constraint);
            self-edges are ignored.
        ready_sort_key: optional key function applied when choosing among
            ready (in-degree-zero) nodes. Default: alphabetical. Use this to
            express PREFERENCE inside what the graph permits — never to
            override the graph.

    Returns:
        (ordered, cycle_groups):
          ordered — list of names, every in-set dependency before its
            dependent, cycle groups appended at the end.
          cycle_groups — list[list[str]] of alphabetically-sorted name groups
            trapped in a dependency cycle (empty when the graph is acyclic).
            The caller MUST surface these loudly.
    """
    names = set(names)
    if ready_sort_key is None:
        def ready_sort_key(n):  # noqa: D401 — alphabetical default
            return n

    deps = {n: set() for n in names}
    dependents = {n: set() for n in names}
    for name in names:
        for d in (deps_by_name.get(name) or ()):
            if d in names and d != name:
                deps[name].add(d)
                dependents[d].add(name)

    indeg = {n: len(deps[n]) for n in names}
    ready = sorted((n for n in names if indeg[n] == 0), key=ready_sort_key)
    ordered = []
    while ready:
        n = ready.pop(0)
        ordered.append(n)
        newly = []
        for m in dependents[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                newly.append(m)
        if newly:
            ready = sorted(ready + newly, key=ready_sort_key)

    # Nodes never emitted are trapped in one or more dependency cycles.
    remaining = names - set(ordered)
    cycle_groups = []
    if remaining:
        seen = set()
        for start in sorted(remaining):
            if start in seen:
                continue
            comp = []
            stack = [start]
            seen.add(start)
            while stack:
                x = stack.pop()
                comp.append(x)
                for y in (deps[x] | dependents[x]):
                    if y in remaining and y not in seen:
                        seen.add(y)
                        stack.append(y)
            cycle_groups.append(sorted(comp))
        cycle_groups.sort(key=lambda g: g[0])
        for g in cycle_groups:
            ordered.extend(g)

    return ordered, cycle_groups
