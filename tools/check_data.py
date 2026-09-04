#!/usr/bin/env python3
"""Check that the built data satisfies the contract app.js relies on.

The client reads short field names out of places.json, data/details/*.json and
data/digest.json. Nothing enforces that build_data.py still emits them, so a
rename on either side would fail silently and only show up as a blank sheet or a
sort that does nothing. Run this after every build.

Exits non-zero and prints every problem found.
"""
import json
import os
import re
import sys

DATA = "data/places.json"
SHARD_DIR = "data/details"
DIGEST = "data/digest.json"
CLIENT = "app.js"

# Fields the client reads, with the minimum share of records that must carry
# them. Anything below its floor means the extraction or derivation regressed.
PLACE_FIELDS = {
    "n": 0.99,   # name
    "y": 0.99,   # latitude
    "x": 0.99,   # longitude
    "t": 0.99,   # date added
    "k": 0.99,   # coarse kind
    "by": 0.99,  # added by
    "g": 0.90,   # Google place id, for the Maps deep link
    "c": 0.85,   # country
    "ct": 0.80,  # city
    "s": 0.60,   # region
    "ci": 0.50,  # category index
    "rt": 0.50,  # rating
    "ro": 0.40,  # romaji aliases
}
TOP_LEVEL = ["places", "categories", "name_order", "shard_size", "count"]
SHARD_FIELDS = {"a": 0.85, "cat": 0.60, "rc": 0.60, "hist": 0.60,
                "hw": 0.40, "rv": 0.50, "ph": 0.40, "w": 0.40}
DIGEST_FIELDS = {"hw": 0.40, "ed": 0.40}


def main():
    problems = []

    if not os.path.exists(DATA):
        sys.exit("%s missing — run tools/build_data.py first" % DATA)
    data = json.load(open(DATA))
    places = data["places"]
    n = len(places)

    for key in TOP_LEVEL:
        if key not in data:
            problems.append("places.json is missing top-level %r" % key)

    # name_order must be a permutation of every place index, or the A-Z sort
    # silently collapses rows onto rank 0.
    order = data.get("name_order") or []
    if sorted(order) != list(range(n)):
        problems.append("name_order is not a permutation of 0..%d (len %d)"
                        % (n - 1, len(order)))

    # Category indices must resolve.
    cats = data.get("categories") or []
    bad = [p["ci"] for p in places if p.get("ci") is not None
           and not 0 <= p["ci"] < len(cats)]
    if bad:
        problems.append("%d places have a ci outside categories[0..%d]"
                        % (len(bad), len(cats) - 1))

    for field, floor in PLACE_FIELDS.items():
        have = sum(1 for p in places if p.get(field) not in (None, "", []))
        if have / n < floor:
            problems.append("places.%s present on %.0f%% (floor %.0f%%)"
                            % (field, 100 * have / n, 100 * floor))

    internal = sorted({k for p in places for k in p if k.startswith("_")})
    if internal:
        problems.append("internal fields leaked into places.json: %s" % internal)

    # Shards: keys are place indices as strings, bucketed by shard_size.
    size = data.get("shard_size") or 250
    seen, shard_recs = set(), []
    for name in sorted(os.listdir(SHARD_DIR)):
        shard_no = int(name.split(".")[0])
        for key, rec in json.load(open(os.path.join(SHARD_DIR, name))).items():
            index = int(key)
            if index // size != shard_no:
                problems.append("place %d is in shard %03d but belongs in %03d"
                                % (index, shard_no, index // size))
            if not 0 <= index < n:
                problems.append("shard %03d references place %d, out of range"
                                % (shard_no, index))
            seen.add(index)
            shard_recs.append(rec)

    if shard_recs:
        for field, floor in SHARD_FIELDS.items():
            have = sum(1 for r in shard_recs if r.get(field) not in (None, "", []))
            if have / len(shard_recs) < floor:
                problems.append("details.%s present on %.0f%% of %d shard records "
                                "(floor %.0f%%)"
                                % (field, 100 * have / len(shard_recs),
                                   len(shard_recs), 100 * floor))

    if os.path.exists(DIGEST):
        digest = json.load(open(DIGEST))
        out_of_range = [k for k in digest if not 0 <= int(k) < n]
        if out_of_range:
            problems.append("digest references %d out-of-range place indices"
                            % len(out_of_range))
        if digest:
            for field, floor in DIGEST_FIELDS.items():
                have = sum(1 for r in digest.values() if r.get(field))
                if have / len(digest) < floor:
                    problems.append("digest.%s present on %.0f%% (floor %.0f%%)"
                                    % (field, 100 * have / len(digest), 100 * floor))
    else:
        problems.append("%s missing" % DIGEST)

    # Every p.<field> the client reads should be something the build emits.
    client = open(CLIENT).read()
    read = set(re.findall(r'\bp\.([a-z]{1,4})\b', client))
    emitted = {k for p in places for k in p} | {"_i", "_n", "_rest", "_key", "_score"}
    unknown = sorted(read - emitted - {"length"})
    if unknown:
        problems.append("app.js reads place fields the build never emits: %s" % unknown)

    print("places: %d | categories: %d | shards: %d covering %d places | digest: %s"
          % (n, len(cats), len(os.listdir(SHARD_DIR)), len(seen),
             len(json.load(open(DIGEST))) if os.path.exists(DIGEST) else "missing"))
    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("contract OK")


if __name__ == "__main__":
    main()
