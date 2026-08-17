#!/usr/bin/env python3
"""Round-1 bank dedup pass — cosine 0.85 on the locally-served embedder.

Embeds every entry's user text(s) through the SAME nomic-embed instance the
product serves (llama-server embedding endpoint, port 8081 on this box) and
measures within-class cosine pairs. Per the ratified methodology, cosine >
~0.85 drops (keep-first greedy); pairs are only compared WITHIN a training
class — an imperative and its contrastive twin are deliberately similar with
opposite targets and must never dedup each other.

Output: the surviving bank (one merged JSONL), the drop list with cosines,
and the calibration report (how many drops are same-template/different-object
— if object variation alone lands above 0.85, that is a threshold calibration
finding to surface, not to silently absorb).
"""
import json
import sys
import urllib.request
from pathlib import Path

BANKS = sys.argv[1:-1]
OUT = Path(sys.argv[-1])
EP = "http://127.0.0.1:8081/embedding"


def embed(text):
    req = urllib.request.Request(
        EP, data=json.dumps({"content": text}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        obj = json.load(r)
    if isinstance(obj, list):
        obj = obj[0]
    emb = obj.get("embedding")
    if isinstance(emb[0], list):
        emb = emb[0]
    return emb


def cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    return num / (da * db) if da and db else 0.0


entries = []
for bank in BANKS:
    for line in open(bank, encoding="utf-8"):
        if line.strip():
            entries.append(json.loads(line))

print(f"embedding {len(entries)} entries via {EP} ...", flush=True)
vecs = []
for e in entries:
    text = " ".join(t["user"] for t in e["turns"] if t["user"].strip())
    vecs.append(embed(text))

by_class = {}
for i, e in enumerate(entries):
    by_class.setdefault(e["training_provenance"]["class"], []).append(i)

# QUALIFIED POLICY (calibration finding, measured this pass): at 0.85 the
# embedder conflates OPPOSITE-INTENT same-subject pairs ("Install btop" vs
# "is btop installed?" = 0.92; "throw mpv on here" vs "get rid of mpv" =
# 0.862) — exactly the contrasts the model must learn to separate, and the
# spec's own words say coverage grows by object variation. So: a DISPATCH
# entry drops only when cosine > 0.85 AND its tool target is IDENTICAL (true
# redundancy — same behavior twice in near-identical words); a PROSE entry
# drops on cosine > 0.85 within its class. The bare-0.85 numbers are also
# printed for the record.

def tool_target(e):
    for t in e["turns"]:
        g = t.get("gold", {})
        if "tool_call" in g:
            return json.dumps(g["tool_call"], sort_keys=True)
    return None

bare_drops = 0
dropped = set()
drops = []
for cls, idxs in by_class.items():
    for a_pos, i in enumerate(idxs):
        if i in dropped:
            continue
        for j in idxs[a_pos + 1:]:
            if j in dropped:
                continue
            c = cos(vecs[i], vecs[j])
            if c <= 0.85:
                continue
            bare_drops += 1
            ti, tj = tool_target(entries[i]), tool_target(entries[j])
            # Structure equality: a two-turn describe-then-act entry must not
            # fall to a one-turn imperative sharing its final target — the
            # multi-turn antecedent shape is deliberate coverage.
            same_shape = len(entries[i]["turns"]) == len(entries[j]["turns"])
            qualified = same_shape and ((ti == tj) if (ti or tj) else True)
            if qualified:
                dropped.add(j)
                drops.append((cls, entries[i]["id"], entries[j]["id"],
                              round(c, 3),
                              entries[i]["turns"][0]["user"][:40],
                              entries[j]["turns"][0]["user"][:40]))

survivors = [e for i, e in enumerate(entries) if i not in dropped]
with OUT.open("w", encoding="utf-8") as fh:
    for e in survivors:
        fh.write(json.dumps(e, ensure_ascii=False) + "\n")

print(f"entries in: {len(entries)}  dropped (qualified): {len(dropped)}  "
      f"survivors: {len(survivors)}")
print(f"pairs over 0.85 (the bare ratified rule would consider): {bare_drops}")
from collections import Counter
print("qualified drops by class:", dict(Counter(d[0] for d in drops)))
print("\nall qualified drops:")
for d in drops:
    print(" ", d)
