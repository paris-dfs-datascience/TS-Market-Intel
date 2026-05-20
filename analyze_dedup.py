"""analyze_dedup.py — one-off dedup analysis on market_intel_export_<DATE>.csv.

Quantifies two levers from SUGGESTIONS.md Section 4 against the existing CSV:

  4a  — within (account, signal_type, source_url) rollup (Pattern A;
        legitimate multi-fact rows on a shared URL).
  4b  — within-account cross-signal-type near-duplicate clustering
        (Pattern C; same event ingested under multiple signal types).
        Run twice, side by side:
          • token Jaccard         (auto-merge >= 0.85, tag-and-keep 0.50-0.85)
          • embedding cosine via text-embedding-004
                                  (auto-merge >= 0.92, tag-and-keep 0.82-0.92)

Writes back to _export/ in the same sink:
  dedup_4a_rollup_<DATE>.csv
  dedup_4b_jaccard_<DATE>.csv
  dedup_4b_embedding_<DATE>.csv
  dedup_4b_pairs_<DATE>.csv
  dedup_analysis_<DATE>.md

Invocation (inside the Container App Job, which has Key Vault access via MI):
  python main.py --analyze-dedup 2026-05-19

Direct (e.g. local dev with GEMINI_API_KEY set):
  python analyze_dedup.py 2026-05-19
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache

from google import genai

from storage import BlobSink, Sink, get_sink

COLS = ["account", "Parent_ID", "signal_type", "account_vertical",
        "summary", "why_it_matters", "event_date", "source_url", "ingested_at"]

JACCARD_AUTO = 0.85
JACCARD_TAG = 0.50
COSINE_AUTO = 0.92
COSINE_TAG = 0.82

SUMMARY_CAP = 2000
EMBED_MODEL = "text-embedding-004"
EMBED_BATCH = 100

STOPWORDS = {"the", "a", "an", "of", "for", "in", "to", "and", "is", "on",
             "at", "by", "with", "from", "as", "or", "be", "are", "was",
             "were", "this", "that", "it", "its", "has", "have", "had"}


# ---------- API key + I/O helpers ----------

@lru_cache(maxsize=1)
def _resolve_api_key(api_key: str | None = None) -> str:
    if api_key:
        return api_key
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if vault_url:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        secret_name = os.environ.get("GEMINI_API_KEY_SECRET_NAME", "gemini-api-key")
        client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        return client.get_secret(secret_name).value
    print("ERROR: no Gemini API key — set GEMINI_API_KEY or AZURE_KEY_VAULT_URL", file=sys.stderr)
    sys.exit(1)


def read_csv_text(sink: Sink, key: str) -> str:
    if isinstance(sink, BlobSink):
        raw = sink.container_client.get_blob_client(key).download_blob().readall()
        return raw.decode("utf-8-sig")
    path = sink._path(key)  # LocalSink
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def rows_to_csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLS)
    writer.writeheader()
    writer.writerows(rows)
    return "﻿" + buf.getvalue()


# ---------- similarity primitives ----------

def tokenize(s: str) -> set[str]:
    toks = re.findall(r"\w+", (s or "").lower())
    return {t for t in toks if t not in STOPWORDS and len(t) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are L2-normalized upstream


def l2_normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


# ---------- union-find ----------

class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


# ---------- formatting helpers ----------

def earliest_date(dates: list[str]) -> str:
    valid = [d for d in dates if d]
    if not valid:
        return ""
    parsed: list[tuple[datetime, str]] = []
    for d in valid:
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
            try:
                parsed.append((datetime.strptime(d, fmt), d))
                break
            except ValueError:
                continue
        else:
            try:
                parsed.append((datetime.fromisoformat(d.replace("Z", "+00:00")), d))
            except ValueError:
                continue
    if parsed:
        parsed.sort(key=lambda x: x[0])
        return parsed[0][1]
    return sorted(valid)[0]


def cap_concat(parts: list[str], cap: int = SUMMARY_CAP) -> str:
    if not parts:
        return ""
    out: list[str] = []
    used = 0
    for i, p in enumerate(parts):
        chunk = f"• {p}"
        if used + len(chunk) + 1 > cap:
            remaining = len(parts) - i
            if remaining:
                out.append(f"...and {remaining} more")
            break
        out.append(chunk)
        used += len(chunk) + 1
    return "\n".join(out)


# ---------- 4a rollup ----------

def rollup_4a(rows: list[dict]) -> tuple[list[dict], dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["account"], r["signal_type"], r["source_url"])
        groups[key].append(r)

    output: list[dict] = []
    size_dist: dict[int, int] = defaultdict(int)
    multifact = 0
    for grp in groups.values():
        size_dist[len(grp)] += 1
        if len(grp) == 1:
            output.append(grp[0])
            continue
        multifact += 1
        first = grp[0]
        summaries = [g["summary"] for g in grp if g["summary"]]
        wims = [g["why_it_matters"] for g in grp if g["why_it_matters"]]
        output.append({
            "account":          first["account"],
            "Parent_ID":        first["Parent_ID"],
            "signal_type":      first["signal_type"],
            "account_vertical": first["account_vertical"],
            "summary":          f"[{len(grp)} facts]\n" + cap_concat(summaries),
            "why_it_matters":   cap_concat(wims),
            "event_date":       earliest_date([g["event_date"] for g in grp]),
            "source_url":       first["source_url"],
            "ingested_at":      first["ingested_at"],
        })

    return output, {
        "input_rows":        len(rows),
        "output_rows":       len(output),
        "groups_total":      len(groups),
        "multifact_groups":  multifact,
        "size_distribution": dict(sorted(size_dist.items())),
    }


# ---------- 4b clustering + collapsed CSV ----------

def build_4b_csv(rows: list[dict],
                 auto_edges: list[tuple[int, int, float]],
                 tag_edges: list[tuple[int, int, float]],
                 label: str) -> tuple[list[dict], int, int]:
    auto_uf = UnionFind(len(rows))
    for i, j, _ in auto_edges:
        auto_uf.union(i, j)
    auto_cid = {i: auto_uf.find(i) for i in range(len(rows))}

    tag_uf = UnionFind(len(rows))
    for i, j, _ in auto_edges:
        tag_uf.union(i, j)
    for i, j, _ in tag_edges:
        tag_uf.union(i, j)
    tag_cid = {i: tag_uf.find(i) for i in range(len(rows))}

    auto_clusters: dict[int, list[int]] = defaultdict(list)
    for i, cid in auto_cid.items():
        auto_clusters[cid].append(i)
    tag_clusters: dict[int, list[int]] = defaultdict(list)
    for i, cid in tag_cid.items():
        tag_clusters[cid].append(i)

    output: list[dict] = []
    seen: set[int] = set()
    merged_clusters = 0
    tag_only_rows = 0

    for i in range(len(rows)):
        cid = auto_cid[i]
        if cid in seen:
            continue
        seen.add(cid)
        members = auto_clusters[cid]
        tcid = tag_cid[i]
        tag_size = len(tag_clusters[tcid])

        if len(members) == 1:
            row = dict(rows[i])
            if tag_size > 1:
                # bridges other distinct auto clusters via tag edges
                eid = hashlib.md5(f"{label}:{tcid}".encode()).hexdigest()[:8]
                row["summary"] = f"[event:{eid}] {row['summary']}"
                tag_only_rows += 1
            output.append(row)
        else:
            merged_clusters += 1
            grp = [rows[m] for m in members]
            sigs = sorted({g["signal_type"] for g in grp if g["signal_type"]})
            urls = sorted({g["source_url"] for g in grp if g["source_url"]})
            summaries = [g["summary"] for g in grp if g["summary"]]
            wims = [g["why_it_matters"] for g in grp if g["why_it_matters"]]
            body = f"[merged: {len(members)} rows, {len(sigs)} signal_types]\n" + cap_concat(summaries)
            if tag_size > len(members):
                eid = hashlib.md5(f"{label}:{tcid}".encode()).hexdigest()[:8]
                body = f"[event:{eid}] " + body
            first = grp[0]
            output.append({
                "account":          first["account"],
                "Parent_ID":        first["Parent_ID"],
                "signal_type":      " | ".join(sigs),
                "account_vertical": first["account_vertical"],
                "summary":          body,
                "why_it_matters":   cap_concat(wims),
                "event_date":       earliest_date([g["event_date"] for g in grp]),
                "source_url":       " | ".join(urls),
                "ingested_at":      first["ingested_at"],
            })

    return output, merged_clusters, tag_only_rows


# ---------- embeddings ----------

def embed_summaries(client, texts: list[str]) -> tuple[dict[str, list[float]], dict]:
    unique = sorted({t for t in texts if t})
    print(f"Embedding {len(unique)} unique summaries via {EMBED_MODEL} "
          f"in batches of {EMBED_BATCH}...")
    out: dict[str, list[float]] = {}
    t0 = time.time()
    call_count = 0
    for i in range(0, len(unique), EMBED_BATCH):
        batch = unique[i:i + EMBED_BATCH]
        for attempt in range(3):
            try:
                resp = client.models.embed_content(model=EMBED_MODEL, contents=batch)
                call_count += 1
                break
            except Exception as e:
                if attempt == 2:
                    raise
                wait = 2 ** attempt
                print(f"  embed retry {attempt + 1}/3 after {wait}s: {e}")
                time.sleep(wait)
        for text, emb in zip(batch, resp.embeddings):
            out[text] = l2_normalize(list(emb.values))
        done = min(i + EMBED_BATCH, len(unique))
        print(f"  batch {(i // EMBED_BATCH) + 1}: {done}/{len(unique)} embedded")
    dt = time.time() - t0
    return out, {"count": len(unique), "calls": call_count, "seconds": dt}


# ---------- pairs CSV + report ----------

def tier_label(score: float, auto: float, tag: float) -> str:
    if score >= auto:
        return "auto"
    if score >= tag:
        return "tag"
    return "below"


def write_pairs_csv(rows: list[dict],
                    jacc_scores: list[tuple[int, int, float]],
                    cos_scores: list[tuple[int, int, float]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["account", "signal_type_a", "signal_type_b",
                "summary_a_preview", "summary_b_preview",
                "jaccard", "cosine", "jaccard_tier", "cosine_tier"])
    for (ia, ja, js), (ib, jb, cs) in zip(jacc_scores, cos_scores):
        assert (ia, ja) == (ib, jb)
        if js < JACCARD_TAG and cs < COSINE_TAG:
            continue
        w.writerow([
            rows[ia]["account"],
            rows[ia]["signal_type"], rows[ja]["signal_type"],
            rows[ia]["summary"][:160].replace("\n", " "),
            rows[ja]["summary"][:160].replace("\n", " "),
            f"{js:.3f}", f"{cs:.3f}",
            tier_label(js, JACCARD_AUTO, JACCARD_TAG),
            tier_label(cs, COSINE_AUTO, COSINE_TAG),
        ])
    return "﻿" + buf.getvalue()


def build_report(date_str: str,
                 rows: list[dict],
                 rollup_stats: dict,
                 jacc_scores: list[tuple[int, int, float]],
                 cos_scores: list[tuple[int, int, float]],
                 jacc_output_rows: int, jacc_merged_clusters: int, jacc_tagged_rows: int,
                 cos_output_rows: int, cos_merged_clusters: int, cos_tagged_rows: int,
                 embed_stats: dict) -> str:
    n = len(rows)
    jacc_auto = [(i, j, s) for i, j, s in jacc_scores if s >= JACCARD_AUTO]
    jacc_tag = [(i, j, s) for i, j, s in jacc_scores if JACCARD_TAG <= s < JACCARD_AUTO]
    cos_auto = [(i, j, s) for i, j, s in cos_scores if s >= COSINE_AUTO]
    cos_tag = [(i, j, s) for i, j, s in cos_scores if COSINE_TAG <= s < COSINE_AUTO]

    jacc_auto_set = {(i, j) for i, j, _ in jacc_auto}
    cos_auto_set = {(i, j) for i, j, _ in cos_auto}
    both = jacc_auto_set & cos_auto_set
    only_jacc = jacc_auto_set - cos_auto_set
    only_cos = cos_auto_set - jacc_auto_set

    jacc_lookup = {(i, j): s for i, j, s in jacc_scores}
    cos_lookup = {(i, j): s for i, j, s in cos_scores}

    def examples(pair_set, k=5):
        if not pair_set:
            return ["_(none)_"]
        sample = sorted(pair_set,
                        key=lambda p: -abs(jacc_lookup[p] - cos_lookup[p]))[:k]
        lines = []
        for (i, j) in sample:
            js = jacc_lookup[(i, j)]
            cs = cos_lookup[(i, j)]
            lines.append(
                f"- **{rows[i]['account']}** — {rows[i]['signal_type']} ↔ "
                f"{rows[j]['signal_type']} (jacc={js:.2f}, cos={cs:.2f})")
            lines.append(f"  - A: {rows[i]['summary'][:140].strip()}...")
            lines.append(f"  - B: {rows[j]['summary'][:140].strip()}...")
        return lines

    L: list[str] = []
    L.append(f"# Dedup Analysis — {date_str}")
    L.append("")
    L.append(f"Source: `_export/market_intel_export_{date_str}.csv`  ")
    L.append(f"Input rows: **{n}**  ")
    L.append(f"Embedding model: `{EMBED_MODEL}` "
             f"({embed_stats['count']} texts, {embed_stats['calls']} API calls, "
             f"{embed_stats['seconds']:.1f}s)  ")
    L.append("")

    L.append("## 4a — within-(account, signal_type, source_url) rollup")
    L.append("")
    L.append(f"- Multi-fact groups (size ≥ 2): **{rollup_stats['multifact_groups']}**")
    L.append(f"- Total distinct groups: {rollup_stats['groups_total']}")
    L.append(f"- Rows after rollup: **{rollup_stats['output_rows']}** "
             f"({n - rollup_stats['output_rows']} rows saved, "
             f"{(n - rollup_stats['output_rows']) / n * 100:.1f}% of input)")
    L.append(f"- Group size distribution (size: count): "
             f"{rollup_stats['size_distribution']}")
    L.append("")

    L.append("## 4b — within-account, cross-signal-type clustering")
    L.append("")
    L.append(f"- Pairs scored: **{len(jacc_scores)}**")
    L.append("")
    L.append("### Token Jaccard")
    L.append(f"- Auto-merge edges (≥ {JACCARD_AUTO}): **{len(jacc_auto)}**")
    L.append(f"- Tag-and-keep edges ({JACCARD_TAG}–{JACCARD_AUTO}): **{len(jacc_tag)}**")
    L.append(f"- Auto-merge clusters (size ≥ 2): {jacc_merged_clusters}")
    L.append(f"- Rows tagged-and-kept: {jacc_tagged_rows}")
    L.append(f"- Rows after Jaccard collapse: **{jacc_output_rows}** "
             f"({n - jacc_output_rows} rows saved, "
             f"{(n - jacc_output_rows) / n * 100:.1f}% of input)")
    L.append("")
    L.append("### Embedding cosine (text-embedding-004)")
    L.append(f"- Auto-merge edges (≥ {COSINE_AUTO}): **{len(cos_auto)}**")
    L.append(f"- Tag-and-keep edges ({COSINE_TAG}–{COSINE_AUTO}): **{len(cos_tag)}**")
    L.append(f"- Auto-merge clusters (size ≥ 2): {cos_merged_clusters}")
    L.append(f"- Rows tagged-and-kept: {cos_tagged_rows}")
    L.append(f"- Rows after embedding collapse: **{cos_output_rows}** "
             f"({n - cos_output_rows} rows saved, "
             f"{(n - cos_output_rows) / n * 100:.1f}% of input)")
    L.append("")

    L.append("### Method agreement on auto-merge tier")
    L.append("")
    L.append(f"- Both methods auto-merged: **{len(both)}** pairs")
    L.append(f"- Jaccard-only: **{len(only_jacc)}** pairs")
    L.append(f"- Embedding-only: **{len(only_cos)}** pairs "
             f"(paraphrase / reordered-fact pattern Jaccard misses)")
    L.append("")
    L.append("#### Jaccard-only auto-merges (top 5 by score divergence)")
    L.extend(examples(only_jacc))
    L.append("")
    L.append("#### Embedding-only auto-merges (top 5 by score divergence)")
    L.extend(examples(only_cos))
    L.append("")

    saved_4a = n - rollup_stats["output_rows"]
    saved_j = n - jacc_output_rows
    saved_c = n - cos_output_rows
    L.append("## Estimated row savings")
    L.append("")
    L.append("| Lever | Rows saved | % of input |")
    L.append("|---|---|---|")
    L.append(f"| 4a rollup | {saved_4a} | {saved_4a / n * 100:.1f}% |")
    L.append(f"| 4b Jaccard auto-merge | {saved_j} | {saved_j / n * 100:.1f}% |")
    L.append(f"| 4b Embedding auto-merge | {saved_c} | {saved_c / n * 100:.1f}% |")
    L.append(f"| 4a + 4b-embedding (additive upper bound) | "
             f"{saved_4a + saved_c} | {(saved_4a + saved_c) / n * 100:.1f}% |")
    L.append("")
    L.append("_4a + 4b combined savings is an upper bound — 4a-collapsed rows "
             "still share an account+signal_type, so the 4b within-account pairs are "
             "computed on the un-rolled corpus._")
    L.append("")

    L.append("## Recommendation")
    L.append("")
    if len(only_cos) > len(only_jacc) * 1.5:
        rec = (f"**Embedding cosine catches substantially more cross-type duplicates** "
               f"than token Jaccard on this dataset — {len(only_cos)} additional "
               f"auto-merge pairs vs. {len(only_jacc)} the other way — supporting "
               f"the SUGGESTIONS.md 4b preference for embeddings.")
    elif len(only_jacc) > len(only_cos) * 1.5:
        rec = (f"**Token Jaccard is sufficient on this dataset.** Embedding cosine "
               f"only adds {len(only_cos)} catches and Jaccard catches {len(only_jacc)} "
               f"that embeddings miss, so the simpler zero-dependency Jaccard path is "
               f"the better deal here.")
    else:
        rec = (f"**The methods agree on the obvious cases and diverge on roughly "
               f"equal-sized minorities** ({len(only_jacc)} Jaccard-only vs. "
               f"{len(only_cos)} embedding-only). Embedding catches paraphrases that "
               f"Jaccard misses; Jaccard catches some high-overlap cases embedding "
               f"scores below the auto-merge threshold. Either is defensible; embedding "
               f"is the safer choice if paraphrase detection matters.")
    L.append(rec)
    L.append("")
    L.append("**Tradeoff:** embedding cosine adds one Gemini API call per CSV build "
             f"(~{embed_stats['seconds']:.0f}s wall, ~$0.001 at current pricing). Token "
             "Jaccard is pure Python with no network dependency.")
    L.append("")
    L.append("## Spot-check artifacts")
    L.append("")
    L.append(f"- `_export/dedup_4a_rollup_{date_str}.csv` — 4a applied "
             f"(same 9-column schema as production export)")
    L.append(f"- `_export/dedup_4b_jaccard_{date_str}.csv` — 4b Jaccard collapsed")
    L.append(f"- `_export/dedup_4b_embedding_{date_str}.csv` — 4b embedding collapsed")
    L.append(f"- `_export/dedup_4b_pairs_{date_str}.csv` — every above-threshold "
             f"pair, both scores side-by-side")
    return "\n".join(L)


# ---------- main ----------

def run(date_str: str | None = None) -> None:
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sink = get_sink()

    src_key = f"_export/market_intel_export_{date_str}.csv"
    print(f"Reading {src_key} from {type(sink).__name__}...")
    csv_text = read_csv_text(sink, src_key)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    print(f"Loaded {len(rows)} rows.")

    print("\n=== 4a: (account, signal_type, source_url) rollup ===")
    rollup_rows, rollup_stats = rollup_4a(rows)
    sink.write_text(f"_export/dedup_4a_rollup_{date_str}.csv",
                    rows_to_csv_text(rollup_rows))
    print(f"  {rollup_stats['input_rows']} -> {rollup_stats['output_rows']} rows  "
          f"({rollup_stats['multifact_groups']} multi-fact groups)")
    print(f"  size dist: {rollup_stats['size_distribution']}")

    print("\n=== 4b: within-account, cross-signal-type pair scoring ===")
    token_sets = [tokenize(r["summary"]) for r in rows]

    by_account: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r["summary"]:
            by_account[r["account"]].append(i)

    pairs: list[tuple[int, int]] = []
    for idxs in by_account.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                ia, ib = idxs[a], idxs[b]
                if rows[ia]["signal_type"] != rows[ib]["signal_type"]:
                    pairs.append((ia, ib))
    print(f"  {len(pairs)} within-account cross-type pairs to score")

    print("\n  Initializing Gemini client + embedding...")
    client = genai.Client(api_key=_resolve_api_key())
    all_summaries = [r["summary"] for r in rows if r["summary"]]
    embeddings, embed_stats = embed_summaries(client, all_summaries)
    print(f"  embedded {embed_stats['count']} texts in "
          f"{embed_stats['seconds']:.1f}s ({embed_stats['calls']} batch calls)")

    print("  Scoring pairs...")
    jacc_scores: list[tuple[int, int, float]] = []
    cos_scores: list[tuple[int, int, float]] = []
    for i, j in pairs:
        jacc_scores.append((i, j, jaccard(token_sets[i], token_sets[j])))
        va = embeddings.get(rows[i]["summary"])
        vb = embeddings.get(rows[j]["summary"])
        cs = cosine(va, vb) if va and vb else 0.0
        cos_scores.append((i, j, cs))

    jacc_auto = [(i, j, s) for i, j, s in jacc_scores if s >= JACCARD_AUTO]
    jacc_tag = [(i, j, s) for i, j, s in jacc_scores if JACCARD_TAG <= s < JACCARD_AUTO]
    cos_auto = [(i, j, s) for i, j, s in cos_scores if s >= COSINE_AUTO]
    cos_tag = [(i, j, s) for i, j, s in cos_scores if COSINE_TAG <= s < COSINE_AUTO]

    print(f"  Jaccard: {len(jacc_auto)} auto-merge, {len(jacc_tag)} tag-and-keep")
    print(f"  Cosine:  {len(cos_auto)} auto-merge, {len(cos_tag)} tag-and-keep")

    print("\n  Building 4b CSVs...")
    jacc_out, jacc_clusters, jacc_tagged = build_4b_csv(rows, jacc_auto, jacc_tag, "jaccard")
    cos_out, cos_clusters, cos_tagged = build_4b_csv(rows, cos_auto, cos_tag, "embedding")
    sink.write_text(f"_export/dedup_4b_jaccard_{date_str}.csv",
                    rows_to_csv_text(jacc_out))
    sink.write_text(f"_export/dedup_4b_embedding_{date_str}.csv",
                    rows_to_csv_text(cos_out))
    sink.write_text(f"_export/dedup_4b_pairs_{date_str}.csv",
                    write_pairs_csv(rows, jacc_scores, cos_scores))
    print(f"  Jaccard:  {len(rows)} -> {len(jacc_out)} rows  "
          f"({jacc_clusters} merged clusters, {jacc_tagged} tagged rows)")
    print(f"  Embedding: {len(rows)} -> {len(cos_out)} rows  "
          f"({cos_clusters} merged clusters, {cos_tagged} tagged rows)")

    report = build_report(date_str, rows, rollup_stats,
                          jacc_scores, cos_scores,
                          len(jacc_out), jacc_clusters, jacc_tagged,
                          len(cos_out), cos_clusters, cos_tagged,
                          embed_stats)
    sink.write_text(f"_export/dedup_analysis_{date_str}.md", report)
    print(f"\nWrote _export/dedup_analysis_{date_str}.md")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
