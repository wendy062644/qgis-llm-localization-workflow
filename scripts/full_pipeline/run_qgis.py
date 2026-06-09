#!/usr/bin/env python3
"""
Run QGIS/Qt .ts ablation workflow for C0-C4.

This script is designed for the experiment design:

  C0 = no mask + no ODS + 1 candidate      (baseline)
  C1 = mask    + ODS    + 3 candidates     (full system)
  C2 = no mask + ODS    + 3 candidates     (no-mask ablation)
  C3 = mask    + no ODS + 3 candidates     (no-glossary ablation)
  C4 = mask    + ODS    + 1 candidate      (no-multi-candidate ablation)

Recommended use:
  - Run this on a fixed stratified subset, e.g. 3000 messages, for ablation.
  - Run only C1 full system on the entire corpus for the main model comparison.

For masked conditions, the script calls your existing api_grok_structure100.py or
api_gemini_structure100.py.  For unmasked conditions, it uses a simple direct
full-string translator implemented here so the model must preserve format tokens
without Python masking.

Install dependencies:
  Grok/xAI:   pip install -U openai httpx pandas odfpy openpyxl
  Gemini:    pip install -U google-genai pandas odfpy openpyxl

API key:
  Grok/xAI:   set XAI_API_KEY=...
  Gemini:     set GEMINI_API_KEY=...

Example, Grok 3000-message stratified subset:
  python run_qgis_ablation_workflow.py \
    --input qgis_en.ts \
    --provider grok \
    --model-id grok-4.3 \
    --glossary 1.ods 2.ods \
    --workdir ablation_grok_3000 \
    --sample-size 3000 \
    --api-parallelism 4 \
    --rpm-limit 60 \
    --run-eval

Example, Gemini:
  python run_qgis_ablation_workflow.py \
    --input qgis_en.ts \
    --provider gemini \
    --model-id gemini-3.1-flash-lite \
    --glossary 1.ods 2.ods \
    --workdir ablation_gemini_3000 \
    --sample-size 3000 \
    --api-parallelism 4 \
    --rpm-limit 60 \
    --run-eval
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None

# -----------------------------------------------------------------------------
# Regexes aligned with the evaluator / translator format checks.
# -----------------------------------------------------------------------------
RE_QT_PLACEHOLDER = re.compile(r"%(?:L)?\d+|%n")
RE_BRACE_PLACEHOLDER = re.compile(r"\{[^{}\n]*\}")
RE_PRINTF_PLACEHOLDER = re.compile(r"%(?:[+#0\-]*)?(?:\d+|\*)?(?:\.\d+)?[hlL]?[diouxXeEfFgGcs]")
RE_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|hellip|[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);")
RE_TAG = re.compile(r"</?\s*([A-Za-z][\w:.-]*)(?:\s+[^<>]*)?/?>")
RE_HTML_XML_TAG = re.compile(r"</?[^<>]+>")
RE_NUMBER = re.compile(r"(?<![%\w])[-+]?\d+(?:[.,]\d+)?(?![%\w])")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")
RE_LATIN = re.compile(r"[A-Za-z]")
RE_TERM_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[+#._:-]+[A-Za-z0-9]+)*[+#]*")
RE_ESCAPED_CONTROL = re.compile(r"\\[nrt]")
RE_ACTUAL_CONTROL = re.compile(r"[\n\t\r]")
RE_ESCAPED_AMPERSAND = re.compile(r"&&")
RE_ACCELERATOR_MARKER = re.compile(
    r"&(?!&)(?!(?:amp|lt|gt|quot|apos|nbsp|hellip|[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);)"
)
COPY_OK = re.compile(r"^(?:[A-Z0-9_+\-./:%#(){}\[\]<>*|\\ ]+|[A-Za-z0-9_+\-./:%#(){}\[\]<>*|\\ ]{1,12})$")
HTML_TAGS = {
    "html", "head", "body", "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "span", "div", "b", "i", "u", "strong", "em", "code", "pre", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "td", "th", "font", "style", "img", "blockquote",
}

CONDITIONS: List[Dict[str, Any]] = [
    {
        "id": "C0",
        "slug": "C0_baseline_nomask_noods_1cand",
        "description": "baseline: no mask + no ODS + 1 candidate",
        "mask": False,
        "ods": False,
        "num_candidates": 1,
    },
    {
        "id": "C1",
        "slug": "C1_full_mask_ods_3cand",
        "description": "full system: mask + ODS + 3 candidates",
        "mask": True,
        "ods": True,
        "num_candidates": 3,
    },
    {
        "id": "C2",
        "slug": "C2_nomask_ods_3cand",
        "description": "no-mask ablation: no mask + ODS + 3 candidates",
        "mask": False,
        "ods": True,
        "num_candidates": 3,
    },
    {
        "id": "C3",
        "slug": "C3_mask_noods_3cand",
        "description": "no-glossary ablation: mask + no ODS + 3 candidates",
        "mask": True,
        "ods": False,
        "num_candidates": 3,
    },
    {
        "id": "C4",
        "slug": "C4_mask_ods_1cand",
        "description": "no-multi-candidate ablation: mask + ODS + 1 candidate",
        "mask": True,
        "ods": True,
        "num_candidates": 1,
    },
]



# -----------------------------------------------------------------------------
# Optional in-file API key variables
# -----------------------------------------------------------------------------
# Priority order used by this workflow:
#   1. --api-key command-line argument
#   2. the in-file variables below
#   3. environment variables such as XAI_API_KEY / GEMINI_API_KEY
#
# Keep these empty if you prefer environment variables.  Do not commit real keys
# to GitHub, paper supplements, or shared folders.
HARDCODED_XAI_API_KEY = ""       # e.g. "xai-..." for Grok/xAI
HARDCODED_GROK_API_KEY = ""      # optional alias for Grok/xAI
HARDCODED_GEMINI_API_KEY = ""    # e.g. "AIza..." for Gemini
HARDCODED_GOOGLE_API_KEY = ""    # optional alias for Gemini


def effective_api_key(provider: str, explicit_key: Optional[str] = None) -> str:
    """Return the API key for the selected provider without printing it."""
    provider = (provider or "").lower().strip()
    if provider == "grok":
        candidates = [
            explicit_key,
            HARDCODED_XAI_API_KEY,
            HARDCODED_GROK_API_KEY,
            os.environ.get("XAI_API_KEY", ""),
            os.environ.get("GROK_API_KEY", ""),
        ]
    elif provider == "gemini":
        candidates = [
            explicit_key,
            HARDCODED_GEMINI_API_KEY,
            HARDCODED_GOOGLE_API_KEY,
            os.environ.get("GEMINI_API_KEY", ""),
            os.environ.get("GOOGLE_API_KEY", ""),
        ]
    else:
        candidates = [explicit_key]
    for key in candidates:
        key = (key or "").strip()
        if key and key not in {
            "PASTE_YOUR_API_KEY_HERE",
            "PASTE_YOUR_XAI_API_KEY_HERE",
            "PASTE_YOUR_GROK_API_KEY_HERE",
            "PASTE_YOUR_GEMINI_API_KEY_HERE",
            "PASTE_YOUR_GOOGLE_API_KEY_HERE",
        }:
            return key
    return ""

DEFAULT_STYLE_GUIDE = """
Target language: Traditional Chinese used in Taiwan (zh-Hant / zh-TW).
Context: QGIS / GIS / scientific software UI localization.
Requirements:
- Use natural, concise Taiwanese Traditional Chinese suitable for UI strings.
- Preserve technical names and safe tokens: QGIS, GIS, GPS, CRS, EPSG, GDAL, OGR, SQL, API, URL, JSON, XML, HTML, file extensions, layer names, field names, and code-like identifiers.
- Do not add explanations. Return JSON only.
""".strip()


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------
@dataclass
class SegmentInfo:
    original_index: int
    context: str
    source: str
    numerus: bool
    translation_type: str
    comment: str = ""
    extracomment: str = ""
    locations: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)


@dataclass
class RawSegment:
    id: str
    index: int
    context: str
    source: str
    translation: str
    translation_type: str
    numerus: bool
    locations: List[str]
    comment: str = ""
    extracomment: str = ""


@dataclass
class GlossaryEntry:
    source_term: str
    target_terms: List[str]
    forbidden_terms: List[str] = field(default_factory=list)
    priority: str = "medium"
    domain: str = ""
    note: str = ""
    source_file: str = ""
    sheet: str = ""


@dataclass
class TranslationIssue:
    segment_id: str
    index: int
    context: str
    issue_type: str
    severity: str
    detail: str
    source: str
    translation: str
    locations: str


# -----------------------------------------------------------------------------
# General helpers
# -----------------------------------------------------------------------------
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def stable_id(context: str, source: str, index: int) -> str:
    digest = hashlib.sha1(f"{context}\u241f{source}\u241f{index}".encode("utf-8")).hexdigest()[:12]
    return f"seg_{index:06d}_{digest}"


def text_of(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext())


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def counter_diff(src_tokens: Iterable[str], trg_tokens: Iterable[str]) -> Tuple[List[str], List[str]]:
    src_c = Counter(src_tokens)
    trg_c = Counter(trg_tokens)
    return list((src_c - trg_c).elements()), list((trg_c - src_c).elements())


def qt_tokens(text: str) -> List[str]:
    return RE_QT_PLACEHOLDER.findall(text or "")


def brace_tokens(text: str) -> List[str]:
    return RE_BRACE_PLACEHOLDER.findall(text or "")


def printf_tokens(text: str) -> List[str]:
    return [tok for tok in RE_PRINTF_PLACEHOLDER.findall(text or "") if not RE_QT_PLACEHOLDER.fullmatch(tok)]


def entity_tokens(text: str) -> List[str]:
    # Match evaluator behavior: entities that can change XML/HTML rendering are critical.
    critical = {"&lt;", "&gt;", "&amp;", "&quot;", "&apos;", "&nbsp;"}
    return [tok for tok in RE_ENTITY.findall(text or "") if tok in critical]


def html_tag_names(text: str) -> List[str]:
    names = []
    for m in RE_TAG.finditer(text or ""):
        name = m.group(1).lower()
        if name in HTML_TAGS:
            names.append(name)
    return names


def html_tag_tokens(text: str) -> List[str]:
    return RE_HTML_XML_TAG.findall(text or "")


def number_tokens(text: str) -> List[str]:
    return RE_NUMBER.findall(text or "")


def accelerator_tokens(text: str) -> List[str]:
    """Return full Qt accelerator tokens, e.g. &Save -> ['&S'].

    Escaped literal ampersands (&&) and XML/HTML entities such as &amp;
    are ignored.  This is stricter than a count-only check and matches the
    evaluator used for structure scoring.
    """
    s = text or ""
    tokens: List[str] = []
    i = 0
    while i < len(s):
        if s[i] != "&":
            i += 1
            continue
        if i + 1 < len(s) and s[i + 1] == "&":
            i += 2
            continue
        m = RE_ENTITY.match(s, i)
        if m:
            i = m.end()
            continue
        if i + 1 < len(s) and not s[i + 1].isspace():
            tokens.append("&" + s[i + 1])
            i += 2
        else:
            tokens.append("&")
            i += 1
    return tokens


def accelerator_count(text: str) -> int:
    return len(accelerator_tokens(text))


def should_skip_translation(source: str) -> bool:
    s = normalize_text(source)
    if not s:
        return True
    if not RE_LATIN.search(s):
        return True
    has_lowercase = bool(re.search(r"[a-z]", s))
    if not has_lowercase and COPY_OK.fullmatch(s):
        return True
    if len(s) <= 2 and not has_lowercase:
        return True
    return False


def xml_indent(elem: ET.Element, level: int = 0) -> None:
    i = "\n" + level * "    "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "    "
        for child in elem:
            xml_indent(child, level + 1)
        if not child.tail or not child.tail.strip():  # type: ignore[name-defined]
            child.tail = i  # type: ignore[name-defined]
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


# -----------------------------------------------------------------------------
# Glossary handling
# -----------------------------------------------------------------------------
HEADER_ALIASES = {
    "source": ["source", "source_term", "english", "en", "term", "原文", "英文", "英語", "英文名稱", "來源", "source term"],
    "target": ["target", "target_term", "translation", "traditional chinese", "zh", "zh-tw", "zh_tw", "中文", "繁中", "繁體中文", "中文名稱", "譯文", "翻譯", "target term"],
    "forbidden": ["forbidden", "forbidden_terms", "禁止", "禁用", "錯誤譯法"],
    "priority": ["priority", "severity", "重要性", "優先", "等級"],
    "domain": ["domain", "領域", "分類", "category"],
    "note": ["note", "備註", "說明", "comment"],
    "aliases": ["alias", "aliases", "variant", "variants", "同義詞", "別名", "變體", "詞形"],
}


def _norm_header(x: Any) -> str:
    return str(x or "").strip().lower().replace("\n", " ")


def infer_columns(df: Any) -> Dict[str, Optional[str]]:
    headers = list(df.columns)
    normalized = {_norm_header(c): c for c in headers}
    out: Dict[str, Optional[str]] = {k: None for k in HEADER_ALIASES}
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                out[key] = normalized[alias.lower()]
                break
        if out[key] is None:
            for hnorm, original in normalized.items():
                if any(alias.lower() in hnorm for alias in aliases if len(alias) >= 3):
                    out[key] = original
                    break
    if out["source"] is None or out["target"] is None:
        non_empty_cols = []
        for col in headers:
            if df[col].astype(str).str.strip().replace({"nan": ""}).ne("").sum() > 0:
                non_empty_cols.append(col)
        if out["source"] is None and len(non_empty_cols) >= 1:
            out["source"] = non_empty_cols[0]
        if out["target"] is None and len(non_empty_cols) >= 2:
            out["target"] = non_empty_cols[1]
    return out


def split_terms(value: Any) -> List[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    parts = [p.strip() for p in re.split(r"\s*[|;；]\s*", text) if p.strip()]
    return parts or [text]


def term_tokens(text: str) -> List[str]:
    text = html.unescape(text or "").casefold()
    return [m.group(0).strip("_.-") for m in RE_TERM_TOKEN.finditer(text) if m.group(0).strip("_.-")]


def singularize_token(token: str) -> str:
    t = token.casefold()
    if len(t) > 5 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 4 and t.endswith("es") and (t.endswith("ches") or t.endswith("shes") or t.endswith("xes") or t.endswith("zes")):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not (t.endswith("ss") or t.endswith("us") or t.endswith("is")):
        return t[:-1]
    return t


def canonical_term_key(text: str) -> Tuple[str, ...]:
    return tuple(singularize_token(t) for t in term_tokens(text))


def load_glossary_file(path: Path) -> Tuple[List[GlossaryEntry], List[str]]:
    warnings: List[str] = []
    entries: List[GlossaryEntry] = []
    if not path.exists():
        return [], [f"Glossary file not found: {path}"]
    if pd is None:
        return [], ["pandas is required for glossary ODS/XLSX/CSV loading. Install pandas odfpy openpyxl."]
    suffix = path.suffix.lower()
    if suffix == ".csv":
        tables = {"csv": pd.read_csv(path, dtype=str, keep_default_na=False)}
    elif suffix in {".ods", ".xlsx", ".xls"}:
        engine = "odf" if suffix == ".ods" else None
        tables = pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False, engine=engine)
    else:
        return [], [f"Unsupported glossary format: {path}"]

    for sheet_name, df in tables.items():
        if df is None or df.empty:
            continue
        df = df.dropna(how="all")
        df = df.loc[:, [c for c in df.columns if df[c].astype(str).str.strip().replace({"nan": ""}).ne("").any()]]
        if df.empty:
            continue
        cols = infer_columns(df)
        if not cols.get("source") or not cols.get("target"):
            warnings.append(f"{path.name}:{sheet_name}: could not infer source/target columns; skipped")
            continue
        for _, row in df.iterrows():
            src = str(row.get(cols["source"], "")).strip()
            tgts = split_terms(row.get(cols["target"], ""))
            if not src or src.lower() == "nan" or not tgts:
                continue
            forbidden = split_terms(row.get(cols["forbidden"], "")) if cols.get("forbidden") else []
            aliases = split_terms(row.get(cols["aliases"], "")) if cols.get("aliases") else []
            priority = str(row.get(cols["priority"], "medium")).strip().lower() if cols.get("priority") else "medium"
            if priority not in {"high", "medium", "low"}:
                priority = "medium"
            domain = str(row.get(cols["domain"], "")).strip() if cols.get("domain") else ""
            note = str(row.get(cols["note"], "")).strip() if cols.get("note") else ""
            entries.append(GlossaryEntry(src, tgts, forbidden, priority, domain, note, path.name, str(sheet_name)))
            for alias in aliases:
                if alias.casefold() != src.casefold():
                    entries.append(GlossaryEntry(alias, tgts, forbidden, priority, domain, f"alias of {src}. {note}".strip(), path.name, str(sheet_name)))
    return entries, warnings


def load_glossaries(paths: Sequence[Path]) -> Tuple[List[GlossaryEntry], List[str]]:
    entries: List[GlossaryEntry] = []
    warnings: List[str] = []
    for p in paths:
        got, warn = load_glossary_file(p)
        entries.extend(got)
        warnings.extend(warn)
    seen, deduped = set(), []
    for e in entries:
        key = (e.source_term.casefold(), tuple(e.target_terms), tuple(e.forbidden_terms))
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped, warnings


@dataclass
class GlossaryMatcher:
    entries: Sequence[GlossaryEntry]
    max_ngram: int = 8
    index: Dict[int, Dict[Tuple[str, ...], List[GlossaryEntry]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))

    def __post_init__(self) -> None:
        for entry in self.entries:
            key = canonical_term_key(entry.source_term)
            if key and len(key) <= self.max_ngram:
                self.index[len(key)][key].append(entry)

    def relevant_entries(self, source: str, max_entries: int = 8) -> List[GlossaryEntry]:
        raw = term_tokens(source)
        if not raw:
            return []
        tokens = [singularize_token(t) for t in raw]
        found: Dict[int, GlossaryEntry] = {}
        limit = min(self.max_ngram, len(tokens))
        for n in range(1, limit + 1):
            table = self.index.get(n)
            if not table:
                continue
            for start in range(0, len(tokens) - n + 1):
                key = tuple(tokens[start:start + n])
                for entry in table.get(key, []):
                    found[id(entry)] = entry
        return sorted(found.values(), key=lambda e: len(canonical_term_key(e.source_term)), reverse=True)[:max_entries]

    def has_hit(self, source: str) -> bool:
        return bool(self.relevant_entries(source, max_entries=1))

    def stats(self) -> Dict[str, Any]:
        return {
            "entry_count": len(self.entries),
            "bucket_sizes_by_ngram_length": {str(n): sum(len(v) for v in b.values()) for n, b in self.index.items()},
            "max_ngram": self.max_ngram,
        }


# -----------------------------------------------------------------------------
# TS parsing and subset creation
# -----------------------------------------------------------------------------
def iter_segment_infos(root: ET.Element, glossary_matcher: Optional[GlossaryMatcher] = None) -> List[SegmentInfo]:
    infos: List[SegmentInfo] = []
    idx = 0
    for context in root.findall("context"):
        ctx_name = context.findtext("name") or ""
        for msg in context.findall("message"):
            idx += 1
            source = msg.findtext("source") or ""
            tr_el = msg.find("translation")
            tr_type = "missing_translation_element" if tr_el is None else tr_el.attrib.get("type", "")
            numerus = msg.attrib.get("numerus") == "yes"
            locs = []
            for loc in msg.findall("location"):
                fn = loc.attrib.get("filename", "")
                line = loc.attrib.get("line", "")
                locs.append(f"{fn}:{line}" if line else fn)
            info = SegmentInfo(
                original_index=idx,
                context=ctx_name,
                source=source,
                numerus=numerus,
                translation_type=tr_type,
                comment=msg.findtext("comment") or "",
                extracomment=msg.findtext("extracomment") or "",
                locations=locs,
            )
            info.categories = categorize_source(info, glossary_matcher)
            infos.append(info)
    return infos


def categorize_source(info: SegmentInfo, glossary_matcher: Optional[GlossaryMatcher] = None) -> List[str]:
    s = info.source or ""
    cats = set()
    if info.numerus or "%n" in s:
        cats.add("numerus")
    if RE_QT_PLACEHOLDER.search(s):
        cats.add("qt_placeholder")
    if RE_BRACE_PLACEHOLDER.search(s) or RE_PRINTF_PLACEHOLDER.search(s):
        cats.add("other_placeholder")
    if RE_ENTITY.search(s) or RE_HTML_XML_TAG.search(s) or RE_TAG.search(s):
        cats.add("html_xml")
    if accelerator_count(s) > 0:
        cats.add("accelerator")
    if RE_NUMBER.search(s) or RE_ESCAPED_CONTROL.search(s) or RE_ACTUAL_CONTROL.search(s):
        cats.add("number_code_newline")
    if len(normalize_text(s)) >= 120:
        cats.add("long")
    if glossary_matcher is not None and glossary_matcher.has_hit(s):
        cats.add("glossary")
    if not cats:
        cats.add("ordinary")
    return sorted(cats)


def select_stratified_subset(
    infos: Sequence[SegmentInfo],
    sample_size: int,
    seed: int,
    subset_mode: str,
) -> List[int]:
    """Return original indices selected for the subset."""
    if sample_size <= 0 or sample_size >= len(infos):
        return [x.original_index for x in infos]

    rng = random.Random(seed)
    if subset_mode == "first":
        return [x.original_index for x in list(infos)[:sample_size]]
    if subset_mode == "random":
        pool = list(infos)
        rng.shuffle(pool)
        return sorted(x.original_index for x in pool[:sample_size])

    # Stratified mode.  Categories overlap, so select each stratum while avoiding duplicates,
    # then fill any remaining slots with random unselected messages.
    quotas = {
        "numerus": 0.10,
        "qt_placeholder": 0.14,
        "accelerator": 0.12,
        "html_xml": 0.10,
        "glossary": 0.18,
        "number_code_newline": 0.16,
        "long": 0.10,
        "ordinary": 0.10,
    }
    by_cat: Dict[str, List[SegmentInfo]] = defaultdict(list)
    for info in infos:
        for cat in info.categories:
            by_cat[cat].append(info)

    selected: Dict[int, SegmentInfo] = {}
    for cat, weight in quotas.items():
        quota = max(1, round(sample_size * weight))
        candidates = [x for x in by_cat.get(cat, []) if x.original_index not in selected]
        rng.shuffle(candidates)
        for x in candidates[:quota]:
            selected[x.original_index] = x

    if len(selected) < sample_size:
        remaining = [x for x in infos if x.original_index not in selected]
        rng.shuffle(remaining)
        for x in remaining[: sample_size - len(selected)]:
            selected[x.original_index] = x

    # If overlap/low availability somehow selected too many, downsample reproducibly.
    chosen = list(selected.values())
    rng.shuffle(chosen)
    return sorted(x.original_index for x in chosen[:sample_size])


def make_subset_ts(input_ts: Path, output_ts: Path, selected_indices: Sequence[int], target_language: str = "zh-Hant") -> None:
    tree = ET.parse(input_ts)
    root = tree.getroot()
    root = copy.deepcopy(root)
    selected = set(selected_indices)
    idx = 0
    for context in list(root.findall("context")):
        for msg in list(context.findall("message")):
            idx += 1
            if idx not in selected:
                context.remove(msg)
        if not context.findall("message"):
            root.remove(context)
    if target_language:
        root.attrib["language"] = target_language
    xml_indent(root)
    output_ts.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_ts, encoding="utf-8", xml_declaration=True)


def write_subset_report(path: Path, selected_infos: Sequence[SegmentInfo]) -> None:
    fieldnames = ["original_index", "context", "source", "numerus", "categories", "translation_type", "locations"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for x in selected_infos:
            writer.writerow({
                "original_index": x.original_index,
                "context": x.context,
                "source": x.source,
                "numerus": x.numerus,
                "categories": ";".join(x.categories),
                "translation_type": x.translation_type,
                "locations": "; ".join(x.locations),
            })


# -----------------------------------------------------------------------------
# Raw no-mask direct translator
# -----------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, rpm: Optional[int] = None):
        self.rpm = int(rpm) if rpm else 0
        self.lock = threading.Lock()
        self.timestamps: deque[float] = deque()

    def acquire(self) -> None:
        if self.rpm <= 0:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                while self.timestamps and now - self.timestamps[0] >= 60.0:
                    self.timestamps.popleft()
                if len(self.timestamps) < self.rpm:
                    self.timestamps.append(now)
                    return
                wait_for = max(0.05, 60.0 - (now - self.timestamps[0]) + 0.05)
            time.sleep(wait_for)


def parse_raw_ts_tree(path: Path) -> Tuple[ET.ElementTree, ET.Element, List[Tuple[RawSegment, ET.Element]]]:
    tree = ET.parse(path)
    root = tree.getroot()
    pairs: List[Tuple[RawSegment, ET.Element]] = []
    idx = 0
    for context in root.findall("context"):
        ctx_name = context.findtext("name") or ""
        for msg in context.findall("message"):
            idx += 1
            source = msg.findtext("source") or ""
            tr_el = msg.find("translation")
            tr_type = "missing_translation_element" if tr_el is None else tr_el.attrib.get("type", "")
            numerus = msg.attrib.get("numerus") == "yes"
            translation = ""
            if tr_el is not None:
                forms = tr_el.findall("numerusform")
                translation = " ||| ".join(text_of(f) for f in forms) if forms else text_of(tr_el)
            locs = []
            for loc in msg.findall("location"):
                fn = loc.attrib.get("filename", "")
                line = loc.attrib.get("line", "")
                locs.append(f"{fn}:{line}" if line else fn)
            seg = RawSegment(
                stable_id(ctx_name, source, idx), idx, ctx_name, source, translation,
                tr_type, numerus, locs, msg.findtext("comment") or "", msg.findtext("extracomment") or ""
            )
            pairs.append((seg, msg))
    return tree, root, pairs


def ensure_translation_element(msg: ET.Element, numerus: bool) -> ET.Element:
    tr_el = msg.find("translation")
    if tr_el is None:
        tr_el = ET.SubElement(msg, "translation")
    if numerus and not tr_el.findall("numerusform"):
        tr_el.text = None
        # Traditional Chinese can use a single surface form; preserving existing forms is preferred.
        ET.SubElement(tr_el, "numerusform")
    return tr_el


def set_translation(msg: ET.Element, translated: str, numerus: bool, unfinished: bool = False) -> None:
    tr_el = ensure_translation_element(msg, numerus)
    if unfinished:
        tr_el.attrib["type"] = "unfinished"
    else:
        tr_el.attrib.pop("type", None)
    if numerus:
        forms = tr_el.findall("numerusform") or [ET.SubElement(tr_el, "numerusform")]
        for form in forms:
            form.text = translated
    else:
        for child in list(tr_el):
            tr_el.remove(child)
        tr_el.text = translated


def validate_structure(source: str, translation: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    checks = [
        ("qt_placeholder", qt_tokens, "critical"),
        ("brace_placeholder", brace_tokens, "critical"),
        ("printf_placeholder", printf_tokens, "critical"),
        ("html_xml_entity", entity_tokens, "major"),
        ("html_xml_tag", html_tag_names, "major"),
        ("escaped_control", lambda s: RE_ESCAPED_CONTROL.findall(s or ""), "major"),
    ]
    for name, extractor, severity in checks:
        missing, extra = counter_diff(extractor(source), extractor(translation))
        if missing:
            issues.append({"issue_type": f"missing_{name}", "severity": severity, "detail": f"Missing {name}: {missing}"})
        if extra:
            issues.append({"issue_type": f"extra_{name}", "severity": severity, "detail": f"Extra {name}: {extra}"})
    missing_numbers, extra_numbers = counter_diff(number_tokens(source), number_tokens(translation))
    if missing_numbers:
        issues.append({"issue_type": "missing_number", "severity": "major", "detail": f"Missing numeric token(s): {missing_numbers}"})
    if extra_numbers:
        issues.append({"issue_type": "extra_number", "severity": "minor", "detail": f"Extra numeric token(s): {extra_numbers}"})
    if source.count("\n") != translation.count("\n"):
        issues.append({
            "issue_type": "newline_count_mismatch",
            "severity": "minor",
            "detail": f"source_newlines={source.count(chr(10))}, target_newlines={translation.count(chr(10))}",
        })
    src_acc = accelerator_tokens(source)
    trg_acc = accelerator_tokens(translation)
    missing_acc, extra_acc = counter_diff(src_acc, trg_acc)
    if missing_acc or extra_acc:
        issues.append({
            "issue_type": "accelerator_count_mismatch",
            "severity": "minor",
            "detail": f"source_accelerators={src_acc}, target_accelerators={trg_acc}, missing={missing_acc}, extra={extra_acc}",
        })
    if not normalize_text(translation):
        issues.append({"issue_type": "empty_translation", "severity": "critical", "detail": "Translation is empty"})
    return issues


def issue_score(issues: Sequence[Dict[str, Any]]) -> float:
    weights = {"critical": 100.0, "major": 10.0, "minor": 1.0, "info": 0.0}
    return sum(weights.get(str(i.get("severity", "minor")).lower(), 1.0) for i in issues)


def latin_residue_score(text: str) -> int:
    allowed = {
        "QGIS", "GIS", "GPS", "CRS", "EPSG", "GDAL", "OGR", "SQL", "API", "URL", "URI", "UUID",
        "WMS", "WFS", "WMTS", "WCS", "XYZ", "HTTP", "HTTPS", "JSON", "XML", "HTML", "CSV", "SVG",
        "PDF", "PNG", "JPEG", "TIFF", "GeoTIFF", "PostGIS", "Python", "Qt", "LAS", "LiDAR",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9_+.#-]{2,}", text or "")
    return sum(1 for w in words if w not in allowed and not w.isupper())


def length_ratio_penalty(source: str, translation: str) -> float:
    s = max(len(normalize_text(source)), 1)
    t = len(normalize_text(translation))
    ratio = t / s
    if 0.25 <= ratio <= 2.8:
        return 0.0
    return abs(ratio - 1.0)


def candidate_quality_score(source: str, translation: str, issues: Sequence[Dict[str, Any]]) -> float:
    return issue_score(issues) + latin_residue_score(translation) * 2.0 + length_ratio_penalty(source, translation) * 3.0


def glossary_prompt_lines(hints: Sequence[GlossaryEntry]) -> str:
    if not hints:
        return "- No glossary hints."
    lines: List[str] = []
    for h in hints:
        forbidden = f"; avoid: {', '.join(h.forbidden_terms)}" if h.forbidden_terms else ""
        note = f"; note: {h.note}" if h.note else ""
        lines.append(f"- {h.source_term} => {' / '.join(h.target_terms)}{forbidden}{note}")
    return "\n".join(lines)


def raw_candidate_schema(num_candidates: int) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": int(num_candidates),
                "maxItems": int(num_candidates),
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_no": {"type": "integer"},
                        "translation": {"type": "string"},
                    },
                    "required": ["candidate_no", "translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }


def build_raw_prompt(seg: RawSegment, hints: Sequence[GlossaryEntry], num_candidates: int) -> str:
    example = {
        "candidates": [
            {"candidate_no": i, "translation": "完整翻譯後的字串"}
            for i in range(1, int(num_candidates) + 1)
        ]
    }
    return f"""
You are translating a QGIS / Qt Linguist .ts UI string into Traditional Chinese used in Taiwan.

This is the NO-MASK ablation condition.  You will see the full source string directly.
Translate the complete source string, but preserve all software-format elements exactly:
- Qt placeholders: %1, %2, %n, %L1
- brace placeholders: {{0}}, {{name}}
- printf placeholders: %s, %d, %.2f
- HTML/XML tags and entities: <b>, </b>, <br>, &amp;, &lt;, &gt;
- numbers, file extensions, code-like tokens, escape sequences, line breaks, and Qt keyboard accelerators such as &Save or E&xit

For Qt keyboard accelerators, preserve the mnemonic marker.  If necessary in Chinese UI text, use a style like 儲存(&S).  Do not drop the & key.
For numerus/plural messages, preserve %n in the translated form.

Style guide:
{DEFAULT_STYLE_GUIDE}

Glossary hints:
{glossary_prompt_lines(hints)}

Qt context: {seg.context}
Translator comment: {seg.comment}
Extra comment: {seg.extracomment}
Locations: {'; '.join(seg.locations)}
Numerus: {seg.numerus}

Source:
{seg.source}

Return JSON only.  Generate exactly {num_candidates} candidate translation(s).  Use this exact shape:
{json.dumps(example, ensure_ascii=False, indent=2)}
""".strip()


def strip_wrapping(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json|text|zh|zh-tw|markdown)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t).strip()
    for q1, q2 in [("「", "」"), ('"', '"'), ("'", "'")]:
        if len(t) >= 2 and t.startswith(q1) and t.endswith(q2):
            t = t[1:-1].strip()
    for p in ["譯文：", "翻譯：", "Translation:", "Translated text:", "答案："]:
        if t.startswith(p):
            t = t[len(p):].strip()
    return t


def extract_json_object(raw: str) -> Any:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        return json.loads(m.group(0))
    m = re.search(r"\[.*\]", text, flags=re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("Could not parse model output as JSON")


def parse_raw_candidates(raw: str, num_candidates: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    issues: List[Dict[str, Any]] = []
    try:
        obj = extract_json_object(raw)
    except Exception as e:
        return [], [{"issue_type": "model_json_parse_failed", "severity": "critical", "detail": repr(e)}]

    if isinstance(obj, dict) and isinstance(obj.get("candidates"), list):
        items = obj["candidates"]
    elif isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict) and isinstance(obj.get("translation"), str):
        items = [obj]
    else:
        return [], [{"issue_type": "model_json_unexpected_shape", "severity": "critical", "detail": f"type={type(obj).__name__}"}]

    out: List[Dict[str, Any]] = []
    for i, item in enumerate(items[: int(num_candidates)], start=1):
        if not isinstance(item, dict):
            continue
        try:
            cno = int(item.get("candidate_no", i))
        except Exception:
            cno = i
        translation = strip_wrapping(str(item.get("translation", "")))
        out.append({"candidate_no": cno, "translation": translation})
    if len(out) < int(num_candidates):
        issues.append({"issue_type": "missing_candidate_count", "severity": "minor", "detail": f"Expected {num_candidates}, got {len(out)}"})
    return out, issues


def create_raw_client(provider: str, api_key: Optional[str] = None):
    provider = provider.lower()
    if provider == "grok":
        import httpx  # type: ignore
        from openai import OpenAI  # type: ignore
        key = effective_api_key("grok", api_key)
        if not key:
            raise RuntimeError(
                "Grok/xAI API key is required. Pass --api-key, fill HARDCODED_XAI_API_KEY "
                "inside this script, or set XAI_API_KEY / GROK_API_KEY."
            )
        return OpenAI(api_key=key, base_url="https://api.x.ai/v1", timeout=httpx.Timeout(3600.0))
    if provider == "gemini":
        from google import genai  # type: ignore
        key = effective_api_key("gemini", api_key)
        if not key:
            raise RuntimeError(
                "Gemini API key is required. Pass --api-key, fill HARDCODED_GEMINI_API_KEY "
                "inside this script, or set GEMINI_API_KEY / GOOGLE_API_KEY."
            )
        return genai.Client(api_key=key)
    raise ValueError(f"Unsupported provider: {provider}")


def raw_generate_json(
    client: Any,
    provider: str,
    model_id: str,
    prompt: str,
    schema: Optional[Dict[str, Any]],
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    use_response_schema: bool = True,
) -> str:
    provider = provider.lower()
    if provider == "grok":
        response_format: Dict[str, Any]
        if use_response_schema and schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "qgis_raw_translation_candidates",
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        common_kwargs = dict(
            model=model_id,
            messages=[
                {"role": "system", "content": "You are a precise QGIS Traditional Chinese localization engine. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            top_p=top_p,
            response_format=response_format,
        )
        try:
            completion = client.chat.completions.create(**common_kwargs, max_tokens=max_output_tokens)
        except Exception as e:
            if "max_tokens" not in repr(e) and "max_completion_tokens" not in repr(e):
                raise
            completion = client.chat.completions.create(**common_kwargs, max_completion_tokens=max_output_tokens)
        return (getattr(completion.choices[0].message, "content", None) or "").strip()

    if provider == "gemini":
        config: Dict[str, Any] = {
            "temperature": temperature,
            "top_p": top_p,
            "max_output_tokens": max_output_tokens,
            "response_mime_type": "application/json",
        }
        if use_response_schema and schema:
            config["response_json_schema"] = schema
        response = client.models.generate_content(model=model_id, contents=prompt, config=config)
        return (getattr(response, "text", None) or "").strip()
    raise ValueError(f"Unsupported provider: {provider}")


def raw_generate_with_retries(
    client: Any,
    provider: str,
    model_id: str,
    prompt: str,
    schema: Optional[Dict[str, Any]],
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    use_response_schema: bool,
    rate_limiter: RateLimiter,
    max_retries: int,
    retry_base_sleep: float,
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            rate_limiter.acquire()
            return raw_generate_json(
                client=client,
                provider=provider,
                model_id=model_id,
                prompt=prompt,
                schema=schema,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                top_p=top_p,
                use_response_schema=use_response_schema,
            )
        except Exception as e:
            last_error = e
            msg = repr(e)
            retryable = any(code in msg for code in [
                "429", "rate_limit", "RateLimit", "RESOURCE_EXHAUSTED", "503", "500", "504",
                "UNAVAILABLE", "DEADLINE_EXCEEDED", "Timeout", "timeout", "Connection", "APIConnectionError",
            ])
            if attempt >= max_retries or not retryable:
                raise
            sleep_s = retry_base_sleep * (2 ** attempt) + min(1.0, 0.1 * attempt)
            time.sleep(sleep_s)
    raise RuntimeError(f"Raw generation failed: {last_error!r}")


def translate_raw_segment(
    seg: RawSegment,
    matcher: GlossaryMatcher,
    use_glossary: bool,
    client: Any,
    provider: str,
    model_id: str,
    num_candidates: int,
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    use_response_schema: bool,
    rate_limiter: RateLimiter,
    max_retries: int,
    retry_base_sleep: float,
    raw_candidate_selection: str,
    hard_lock_final: bool,
) -> Tuple[RawSegment, str, List[Dict[str, Any]], Dict[str, Any]]:
    if should_skip_translation(seg.source):
        return seg, seg.source, [], {"status": "copied_language_neutral", "api_call_count": 0, "candidates": []}

    hints = matcher.relevant_entries(seg.source, max_entries=8) if use_glossary else []
    prompt = build_raw_prompt(seg, hints, num_candidates=num_candidates)
    schema = raw_candidate_schema(num_candidates)

    try:
        raw = raw_generate_with_retries(
            client=client,
            provider=provider,
            model_id=model_id,
            prompt=prompt,
            schema=schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            use_response_schema=use_response_schema,
            rate_limiter=rate_limiter,
            max_retries=max_retries,
            retry_base_sleep=retry_base_sleep,
        )
        parsed, parse_issues = parse_raw_candidates(raw, num_candidates=num_candidates)
        candidates: List[Dict[str, Any]] = []
        for c in parsed:
            translation = str(c.get("translation", ""))
            validation_issues = validate_structure(seg.source, translation)
            issues = list(parse_issues) + validation_issues
            candidates.append({
                "candidate_no": int(c.get("candidate_no", len(candidates) + 1)),
                "translation": translation,
                "issues": issues,
                "issue_score": issue_score(issues),
                "quality_score": candidate_quality_score(seg.source, translation, issues),
                "raw": raw,
            })
        if not candidates:
            candidates = [{
                "candidate_no": 1,
                "translation": seg.source,
                "issues": parse_issues or [{"issue_type": "no_candidate_returned", "severity": "critical", "detail": "No parseable candidate."}],
                "issue_score": 100.0,
                "quality_score": 100.0,
                "raw": raw,
            }]
    except Exception as e:
        tb = traceback.format_exc()
        translated = seg.source if hard_lock_final else (seg.translation or seg.source)
        issues = [{"issue_type": "raw_api_exception", "severity": "critical", "detail": repr(e)}]
        return seg, translated, issues, {
            "status": "exception_fallback",
            "exception_repr": repr(e),
            "traceback": tb,
            "api_call_count": 1,
            "candidates": [],
        }

    if raw_candidate_selection == "first" or len(candidates) == 1:
        selected = candidates[0]
        selection_method = "first_candidate"
    else:
        selected = min(candidates, key=lambda x: (float(x.get("quality_score", 9999)), int(x.get("candidate_no", 999))))
        selection_method = "lowest_format_quality_score"

    translated = str(selected.get("translation", ""))
    issues = list(selected.get("issues", []) or [])
    status = "raw_selected"

    if hard_lock_final and issues:
        # Use source fallback only when requested.  For ablation, keep this off
        # so structure failures remain visible.
        translated = seg.source
        issues = validate_structure(seg.source, translated)
        issues.insert(0, {"issue_type": "hard_lock_source_fallback", "severity": "major", "detail": "Selected raw candidate had structure issues."})
        status = "hard_lock_source_fallback"

    return seg, translated, issues, {
        "status": status,
        "selection_method": selection_method,
        "selected_candidate_no": selected.get("candidate_no"),
        "candidates": candidates,
        "glossary_hints": [asdict(x) for x in hints],
        "api_call_count": 1,
        "mask": False,
        "ods": bool(use_glossary),
    }


def run_raw_condition(
    input_ts: Path,
    output_ts: Path,
    outdir: Path,
    provider: str,
    model_id: str,
    glossary_entries: Sequence[GlossaryEntry],
    use_glossary: bool,
    num_candidates: int,
    api_key: Optional[str],
    api_parallelism: int,
    rpm_limit: Optional[int],
    max_retries: int,
    retry_base_sleep: float,
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    no_resume: bool,
    raw_candidate_selection: str,
    hard_lock_final: bool,
    use_response_schema: bool,
) -> Dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = outdir / "translation_log.jsonl"
    issues_csv_path = outdir / "translation_issues.csv"
    summary_path = outdir / "translation_summary.json"

    tree, root, pairs = parse_raw_ts_tree(input_ts)
    matcher = GlossaryMatcher(glossary_entries)
    client = create_raw_client(provider, api_key=api_key)
    rate_limiter = RateLimiter(rpm_limit)

    done_by_id: Dict[str, Dict[str, Any]] = {}
    if not no_resume:
        for r in read_jsonl(checkpoint_path):
            if r.get("segment_id") and r.get("translation"):
                done_by_id[str(r["segment_id"])] = r

    all_issues: List[TranslationIssue] = []
    counters = Counter()
    api_call_count = 0
    batch: List[Tuple[RawSegment, ET.Element]] = []
    results_by_id: Dict[str, Tuple[RawSegment, str, List[Dict[str, Any]], Dict[str, Any]]] = {}

    def consume(seg: RawSegment, msg: ET.Element, translated: str, issues: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
        nonlocal api_call_count
        set_translation(msg, translated, seg.numerus, unfinished=False)
        counters[str(meta.get("status", "unknown"))] += 1
        api_call_count += int(meta.get("api_call_count", 0) or 0)
        if issues:
            counters["segments_with_issues"] += 1
        for issue in issues:
            all_issues.append(TranslationIssue(
                segment_id=seg.id,
                index=seg.index,
                context=seg.context,
                issue_type=str(issue.get("issue_type", "unknown")),
                severity=str(issue.get("severity", "minor")),
                detail=str(issue.get("detail", "")),
                source=seg.source,
                translation=translated,
                locations="; ".join(seg.locations),
            ))

    def run_batch(to_run: List[Tuple[RawSegment, ET.Element]]) -> None:
        nonlocal results_by_id
        if not to_run:
            return
        with ThreadPoolExecutor(max_workers=max(1, int(api_parallelism))) as executor:
            futures = {
                executor.submit(
                    translate_raw_segment,
                    seg,
                    matcher,
                    use_glossary,
                    client,
                    provider,
                    model_id,
                    num_candidates,
                    max_output_tokens,
                    temperature,
                    top_p,
                    use_response_schema,
                    rate_limiter,
                    max_retries,
                    retry_base_sleep,
                    raw_candidate_selection,
                    hard_lock_final,
                ): (seg, msg)
                for seg, msg in to_run
            }
            for fut in as_completed(futures):
                seg, _msg = futures[fut]
                try:
                    results_by_id[seg.id] = fut.result()
                except Exception as e:
                    tb = traceback.format_exc()
                    translated = seg.source if hard_lock_final else (seg.translation or seg.source)
                    issues = [{"issue_type": "thread_exception", "severity": "critical", "detail": repr(e)}]
                    meta = {"status": "thread_exception", "traceback": tb, "api_call_count": 1, "candidates": []}
                    results_by_id[seg.id] = (seg, translated, issues, meta)

        with checkpoint_path.open("a", encoding="utf-8") as f:
            for seg, _msg in to_run:
                got = results_by_id[seg.id]
                _seg, translated, issues, meta = got
                row = {
                    "segment_id": seg.id,
                    "index": seg.index,
                    "context": seg.context,
                    "source": seg.source,
                    "translation": translated,
                    "issues": issues,
                    "status": meta.get("status"),
                    "meta": meta,
                    "model_id": model_id,
                    "mode": "raw_no_mask",
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    for seg, msg in pairs:
        if seg.id in done_by_id:
            row = done_by_id[seg.id]
            translated = str(row.get("translation", ""))
            issues = list(row.get("issues", []) or [])
            meta = dict(row.get("meta", {}) or {"status": "reused_from_checkpoint", "api_call_count": 0})
            consume(seg, msg, translated, issues, meta)
            continue
        batch.append((seg, msg))
        if len(batch) >= 20:
            print(f"[raw] translating batch ending at segment {seg.index} ({len(batch)} segments)", file=sys.stderr)
            run_batch(batch)
            for bseg, bmsg in batch:
                _seg, translated, issues, meta = results_by_id[bseg.id]
                consume(bseg, bmsg, translated, issues, meta)
            batch = []

    if batch:
        print(f"[raw] translating final batch ({len(batch)} segments)", file=sys.stderr)
        run_batch(batch)
        for bseg, bmsg in batch:
            _seg, translated, issues, meta = results_by_id[bseg.id]
            consume(bseg, bmsg, translated, issues, meta)

    xml_indent(root)
    output_ts.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_ts, encoding="utf-8", xml_declaration=True)

    xml_well_formed = True
    xml_parse_error = ""
    try:
        ET.parse(output_ts)
    except Exception as e:
        xml_well_formed = False
        xml_parse_error = repr(e)
        all_issues.append(TranslationIssue("__output__", -1, "__output__", "output_xml_parse_failed", "critical", xml_parse_error, str(input_ts), str(output_ts), ""))

    with issues_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["segment_id", "index", "context", "issue_type", "severity", "detail", "source", "translation", "locations"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for issue in all_issues:
            writer.writerow(asdict(issue))

    summary = {
        "input_ts": str(input_ts),
        "output_ts": str(output_ts),
        "provider": provider,
        "model_id": model_id,
        "workflow": "raw_no_mask_ablation",
        "total_segments": len(pairs),
        "num_candidates": int(num_candidates),
        "ods_enabled": bool(use_glossary),
        "hard_lock_final": bool(hard_lock_final),
        "raw_candidate_selection": raw_candidate_selection,
        "api_call_count": api_call_count,
        "status_counts": dict(counters),
        "issue_count": len(all_issues),
        "issue_counts": dict(Counter(i.issue_type for i in all_issues)),
        "severity_counts": dict(Counter(i.severity for i in all_issues)),
        "xml_well_formed": xml_well_formed,
        "xml_parse_error": xml_parse_error,
        "glossary_entry_count": len(glossary_entries) if use_glossary else 0,
        "glossary_matcher": matcher.stats() if use_glossary else {},
        "reports": {"translation_log": str(checkpoint_path), "translation_issues_csv": str(issues_csv_path)},
    }
    write_json(summary_path, summary)
    return summary




# -----------------------------------------------------------------------------
# Built-in masked translator (standalone: no api_grok.py/api_gemini.py required)
# -----------------------------------------------------------------------------
@dataclass
class TemplatePart:
    kind: str  # "text" or "token"
    value: str
    slot: str = ""
    text_id: str = ""
    translatable: bool = False


MASK_TOKEN_PATTERNS = [
    ("qt_placeholder", RE_QT_PLACEHOLDER),
    ("brace_placeholder", RE_BRACE_PLACEHOLDER),
    ("printf_placeholder", RE_PRINTF_PLACEHOLDER),
    ("html_xml_entity", RE_ENTITY),
    ("html_xml_tag", RE_HTML_XML_TAG),
    ("escaped_ampersand", RE_ESCAPED_AMPERSAND),
    ("escaped_control", RE_ESCAPED_CONTROL),
    ("actual_control", RE_ACTUAL_CONTROL),
    ("number", RE_NUMBER),
]


def strip_accelerators_for_translation(text: str) -> Tuple[str, List[str]]:
    """Remove Qt mnemonic markers for model translation and remember keys.

    Example:
        &Save        -> ("Save", ["&S"])
        E&xit        -> ("Exit", ["&x"])
        Save &As...  -> ("Save As...", ["&A"])

    The final masked output appends the original tokens using the zh-TW UI style
    "翻譯(&S)", which preserves the exact mnemonic key without asking the model
    to reproduce it.
    """
    s = text or ""
    out: List[str] = []
    tokens: List[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        if i + 1 < len(s) and s[i + 1] == "&":
            out.append("&&")
            i += 2
            continue
        m = RE_ENTITY.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        if i + 1 < len(s) and not s[i + 1].isspace():
            key_char = s[i + 1]
            tokens.append("&" + key_char)
            out.append(key_char)
            i += 2
        else:
            tokens.append("&")
            i += 1
    return "".join(out), tokens


def reapply_accelerators_zh_tw(translated: str, accel_tokens: Sequence[str]) -> str:
    if not accel_tokens:
        return translated
    out = translated or ""
    trailing_ws = ""
    m = re.search(r"\s+$", out)
    if m:
        trailing_ws = m.group(0)
        out = out[: -len(trailing_ws)]
    for tok in accel_tokens:
        if tok and tok not in accelerator_tokens(out):
            out += f"({tok})"
    return out + trailing_ws


def token_spans_masked(text: str) -> List[Tuple[int, int, str, str]]:
    matches: List[Tuple[int, int, str, str]] = []
    for kind, pat in MASK_TOKEN_PATTERNS:
        for m in pat.finditer(text or ""):
            token = m.group(0)
            if kind == "printf_placeholder" and RE_QT_PLACEHOLDER.fullmatch(token):
                continue
            matches.append((m.start(), m.end(), token, kind))
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    selected: List[Tuple[int, int, str, str]] = []
    last_end = -1
    for s, e, tok, kind in matches:
        if s >= last_end:
            selected.append((s, e, tok, kind))
            last_end = e
    return selected


def should_translate_text_part(text: str) -> bool:
    s = normalize_text(text)
    if not s:
        return False
    if not RE_LATIN.search(s):
        return False
    has_lowercase = bool(re.search(r"[a-z]", s))
    if not has_lowercase and COPY_OK.fullmatch(s):
        return False
    if len(s) <= 2 and not has_lowercase:
        return False
    return True


def split_template_masked(text: str) -> List[TemplatePart]:
    spans = token_spans_masked(text)
    parts: List[TemplatePart] = []
    pos = 0
    text_no = 0
    tok_no = 0
    for s, e, tok, _kind in spans:
        if s > pos:
            raw = text[pos:s]
            translatable = should_translate_text_part(raw)
            tid = f"T{text_no}" if translatable else ""
            if translatable:
                text_no += 1
            parts.append(TemplatePart("text", raw, text_id=tid, translatable=translatable))
        slot = f"TOK_{tok_no}"
        parts.append(TemplatePart("token", tok, slot=slot, translatable=False))
        tok_no += 1
        pos = e
    if pos < len(text or ""):
        raw = text[pos:]
        translatable = should_translate_text_part(raw)
        tid = f"T{text_no}" if translatable else ""
        if translatable:
            text_no += 1
        parts.append(TemplatePart("text", raw, text_id=tid, translatable=translatable))
    if not parts and text:
        translatable = should_translate_text_part(text)
        parts.append(TemplatePart("text", text, text_id="T0" if translatable else "", translatable=translatable))
    return parts


def template_skeleton(parts: Sequence[TemplatePart]) -> str:
    out: List[str] = []
    for part in parts:
        if part.kind == "token":
            out.append(f"⟦{part.slot}⟧")
        else:
            out.append(part.value)
    return "".join(out)


def text_part_requests(parts: Sequence[TemplatePart]) -> List[Dict[str, str]]:
    requests: List[Dict[str, str]] = []
    for i, part in enumerate(parts):
        if part.kind == "text" and part.translatable and part.text_id:
            left = parts[i - 1].slot if i > 0 and parts[i - 1].kind == "token" else ""
            right = parts[i + 1].slot if i + 1 < len(parts) and parts[i + 1].kind == "token" else ""
            requests.append({
                "id": part.text_id,
                "text": part.value,
                "left_token_slot": left,
                "right_token_slot": right,
            })
    return requests


def assemble_template_masked(parts: Sequence[TemplatePart], translations: Dict[str, str]) -> str:
    out: List[str] = []
    for part in parts:
        if part.kind == "token":
            out.append(part.value)
        elif part.translatable and part.text_id:
            out.append(translations.get(part.text_id, part.value))
        else:
            out.append(part.value)
    return "".join(out)


def sanitize_text_part_translation(value: Any, forbidden_tokens: Sequence[str], slot_names: Sequence[str]) -> str:
    t = strip_wrapping(str(value or ""))
    for slot in slot_names:
        t = t.replace(f"⟦{slot}⟧", "")
        t = t.replace(slot, "")
        t = t.replace(f"__{slot}__", "")
    for tok in sorted(set(forbidden_tokens), key=len, reverse=True):
        if tok:
            t = t.replace(tok, "")
    return t


def masked_candidate_schema(requests: Sequence[Dict[str, str]], num_candidates: int) -> Dict[str, Any]:
    text_props = {r["id"]: {"type": "string"} for r in requests}
    required_ids = [r["id"] for r in requests]
    candidate_schema = {
        "type": "object",
        "properties": {
            "candidate_no": {"type": "integer"},
            "translations": {
                "type": "object",
                "properties": text_props,
                "required": required_ids,
                "additionalProperties": False,
            },
        },
        "required": ["candidate_no", "translations"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": int(num_candidates),
                "maxItems": int(num_candidates),
                "items": candidate_schema,
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }


def build_masked_prompt(
    seg: RawSegment,
    clean_source: str,
    skeleton: str,
    requests: Sequence[Dict[str, str]],
    hints: Sequence[GlossaryEntry],
    num_candidates: int,
) -> str:
    request_json = json.dumps(list(requests), ensure_ascii=False, indent=2)
    example = {
        "candidates": [
            {"candidate_no": i, "translations": {r["id"]: "翻譯後的文字" for r in requests}}
            for i in range(1, int(num_candidates) + 1)
        ]
    }
    return f"""
You are translating QGIS / Qt Linguist UI text into Traditional Chinese used in Taiwan.

This is the MASKED condition.  Translate ONLY the TEXT_PARTS below.
Do not output protected slots such as ⟦TOK_0⟧, TOK_0, or any original placeholders/tags/entities/numbers.
Python will reinsert all protected tokens exactly after your JSON is parsed.

Qt keyboard accelerators such as &Save have already been removed from the text parts and will be rebuilt deterministically by Python as zh-TW suffixes like 儲存(&S).  Do not add ampersand shortcut markers yourself.

Style guide:
{DEFAULT_STYLE_GUIDE}

Glossary hints:
{glossary_prompt_lines(hints)}

Qt context: {seg.context}
Translator comment: {seg.comment}
Extra comment: {seg.extracomment}
Locations: {'; '.join(seg.locations)}
Numerus: {seg.numerus}

Original source for context only:
{seg.source}

Accelerator-stripped source for context:
{clean_source}

Protected skeleton for context:
{skeleton}

TEXT_PARTS to translate:
{request_json}

Return JSON only. Generate exactly {num_candidates} candidate translation(s). Use this exact shape:
{json.dumps(example, ensure_ascii=False, indent=2)}
""".strip()


def parse_masked_candidates(
    raw: str,
    requests: Sequence[Dict[str, str]],
    forbidden_tokens: Sequence[str],
    slot_names: Sequence[str],
    num_candidates: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    expected_ids = [r["id"] for r in requests]
    parse_issues: List[Dict[str, Any]] = []
    try:
        obj = extract_json_object(raw)
    except Exception as e:
        return [], [{"issue_type": "model_json_parse_failed", "severity": "critical", "detail": repr(e)}]

    if isinstance(obj, dict) and isinstance(obj.get("candidates"), list):
        items = obj["candidates"]
    elif isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict) and isinstance(obj.get("translations"), dict):
        items = [obj]
    else:
        return [], [{"issue_type": "model_json_unexpected_shape", "severity": "critical", "detail": f"type={type(obj).__name__}"}]

    parsed: List[Dict[str, Any]] = []
    for i, item in enumerate(items[: int(num_candidates)], start=1):
        if not isinstance(item, dict):
            continue
        try:
            cno = int(item.get("candidate_no", i))
        except Exception:
            cno = i
        translations_obj = item.get("translations", item)
        translations: Dict[str, str] = {}
        issues: List[Dict[str, Any]] = []
        if not isinstance(translations_obj, dict):
            issues.append({"issue_type": "candidate_translations_not_object", "severity": "critical", "detail": type(translations_obj).__name__})
        else:
            for tid in expected_ids:
                if tid in translations_obj:
                    translations[tid] = sanitize_text_part_translation(translations_obj[tid], forbidden_tokens, slot_names)
            missing = [tid for tid in expected_ids if tid not in translations or translations[tid] == ""]
            extra = [str(k) for k in translations_obj.keys() if str(k) not in expected_ids]
            if missing:
                issues.append({"issue_type": "missing_text_part_translation", "severity": "critical", "detail": f"Missing text part id(s): {missing}"})
            if extra:
                issues.append({"issue_type": "extra_text_part_translation", "severity": "minor", "detail": f"Unexpected text part id(s): {extra}"})
        parsed.append({"candidate_no": cno, "translations": translations, "issues": issues})
    if len(parsed) < int(num_candidates):
        parse_issues.append({"issue_type": "missing_candidate_count", "severity": "minor", "detail": f"Expected {num_candidates}, got {len(parsed)}"})
    return parsed, parse_issues


def translate_masked_segment(
    seg: RawSegment,
    matcher: GlossaryMatcher,
    use_glossary: bool,
    client: Any,
    provider: str,
    model_id: str,
    num_candidates: int,
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    use_response_schema: bool,
    rate_limiter: RateLimiter,
    max_retries: int,
    retry_base_sleep: float,
) -> Tuple[RawSegment, str, List[Dict[str, Any]], Dict[str, Any]]:
    clean_source, accel_tokens = strip_accelerators_for_translation(seg.source)
    if should_skip_translation(clean_source):
        # Keep source unchanged for language-neutral strings; this is structurally safe.
        return seg, seg.source, [], {"status": "copied_language_neutral", "api_call_count": 0, "candidates": [], "mask": True}

    parts = split_template_masked(clean_source)
    requests = text_part_requests(parts)
    if not requests:
        translated = reapply_accelerators_zh_tw(assemble_template_masked(parts, {}) or clean_source, accel_tokens)
        issues = validate_structure(seg.source, translated)
        if issues:
            translated = seg.source
            issues = []
            status = "source_fallback_no_translatable_text"
        else:
            status = "copied_no_translatable_text"
        return seg, translated, issues, {"status": status, "api_call_count": 0, "candidates": [], "mask": True}

    hints = matcher.relevant_entries(seg.source, max_entries=8) if use_glossary else []
    forbidden_tokens = [p.value for p in parts if p.kind == "token"] + list(accel_tokens)
    slot_names = [p.slot for p in parts if p.kind == "token"]
    schema = masked_candidate_schema(requests, num_candidates)
    skeleton = template_skeleton(parts)
    prompt = build_masked_prompt(seg, clean_source, skeleton, requests, hints, num_candidates)

    try:
        raw = raw_generate_with_retries(
            client=client,
            provider=provider,
            model_id=model_id,
            prompt=prompt,
            schema=schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            use_response_schema=use_response_schema,
            rate_limiter=rate_limiter,
            max_retries=max_retries,
            retry_base_sleep=retry_base_sleep,
        )
        parsed, parse_issues = parse_masked_candidates(raw, requests, forbidden_tokens, slot_names, num_candidates)
        candidates: List[Dict[str, Any]] = []
        for c in parsed:
            assembled_clean = assemble_template_masked(parts, c.get("translations", {}))
            translated = reapply_accelerators_zh_tw(assembled_clean, accel_tokens)
            validation_issues = validate_structure(seg.source, translated)
            issues = list(parse_issues) + list(c.get("issues", [])) + validation_issues
            candidates.append({
                "candidate_no": int(c.get("candidate_no", len(candidates) + 1)),
                "translation": translated,
                "span_translations": c.get("translations", {}),
                "issues": issues,
                "issue_score": issue_score(issues),
                "blocking": bool(issues),
                "quality_score": candidate_quality_score(seg.source, translated, issues),
                "raw": raw,
            })
        if not candidates:
            candidates = [{
                "candidate_no": 1,
                "translation": seg.source,
                "issues": parse_issues or [{"issue_type": "no_candidate_returned", "severity": "critical", "detail": "No parseable candidate."}],
                "issue_score": 100.0,
                "blocking": True,
                "quality_score": 100.0,
                "raw": raw,
            }]
    except Exception as e:
        tb = traceback.format_exc()
        return seg, seg.source, [{"issue_type": "masked_api_exception", "severity": "critical", "detail": repr(e)}], {
            "status": "exception_source_fallback",
            "exception_repr": repr(e),
            "traceback": tb,
            "api_call_count": 1,
            "candidates": [],
            "mask": True,
        }

    valid = [c for c in candidates if not c.get("issues")]
    if valid:
        selected = min(valid, key=lambda x: (float(x.get("quality_score", 9999)), int(x.get("candidate_no", 999))))
        translated = str(selected.get("translation", ""))
        issues = []
        status = "ok_format_safe_template"
    else:
        selected = min(candidates, key=lambda x: (float(x.get("quality_score", 9999)), int(x.get("candidate_no", 999))))
        translated = seg.source
        issues = []  # Source fallback is structurally safe; log source fallback in meta instead.
        status = "structure_hard_lock_source_fallback"

    valid_count = len(valid)
    return seg, translated, issues, {
        "status": status,
        "selected_index": selected.get("candidate_no"),
        "selection": {
            "method": "deterministic_lowest_quality_score_among_strict_valid_candidates" if valid else "source_fallback_no_valid_candidate",
            "selected": selected.get("candidate_no") if valid else None,
            "valid_candidate_count": valid_count,
        },
        "candidates": candidates,
        "glossary_hints": [asdict(x) for x in hints],
        "api_call_count": 1,
        "mask": True,
        "ods": bool(use_glossary),
        "accelerator_tokens": list(accel_tokens),
        "template_skeleton": skeleton,
        "text_part_requests": requests,
    }


def run_internal_masked_condition(
    input_ts: Path,
    output_ts: Path,
    outdir: Path,
    provider: str,
    model_id: str,
    glossary_entries: Sequence[GlossaryEntry],
    use_glossary: bool,
    num_candidates: int,
    api_key: Optional[str],
    api_parallelism: int,
    rpm_limit: Optional[int],
    max_retries: int,
    retry_base_sleep: float,
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    no_resume: bool,
    use_response_schema: bool,
) -> Dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = outdir / "translation_log.jsonl"
    issues_csv_path = outdir / "translation_issues.csv"
    summary_path = outdir / "translation_summary.json"

    tree, root, pairs = parse_raw_ts_tree(input_ts)
    matcher = GlossaryMatcher(glossary_entries)
    client = create_raw_client(provider, api_key=api_key)
    rate_limiter = RateLimiter(rpm_limit)

    done_by_id: Dict[str, Dict[str, Any]] = {}
    if not no_resume:
        for r in read_jsonl(checkpoint_path):
            if r.get("segment_id") and r.get("translation"):
                # Reuse only structurally safe checkpoint rows.
                if not validate_structure(str(r.get("source", "")), str(r.get("translation", ""))):
                    done_by_id[str(r["segment_id"])] = r

    all_issues: List[TranslationIssue] = []
    counters = Counter()
    api_call_count = 0
    safe_fallback_count = 0
    format_safe_accept_count = 0
    candidate_count_total = 0
    batch: List[Tuple[RawSegment, ET.Element]] = []
    results_by_id: Dict[str, Tuple[RawSegment, str, List[Dict[str, Any]], Dict[str, Any]]] = {}

    def consume(seg: RawSegment, msg: ET.Element, translated: str, issues: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
        nonlocal api_call_count, safe_fallback_count, format_safe_accept_count, candidate_count_total
        set_translation(msg, translated, seg.numerus, unfinished=False)
        status = str(meta.get("status", "unknown"))
        counters[status] += 1
        api_call_count += int(meta.get("api_call_count", 0) or 0)
        candidate_count_total += len(meta.get("candidates", []) or [])
        if "fallback" in status.lower():
            safe_fallback_count += 1
        if status == "ok_format_safe_template":
            format_safe_accept_count += 1
        for issue in issues:
            all_issues.append(TranslationIssue(
                segment_id=seg.id,
                index=seg.index,
                context=seg.context,
                issue_type=str(issue.get("issue_type", "unknown")),
                severity=str(issue.get("severity", "minor")),
                detail=str(issue.get("detail", "")),
                source=seg.source,
                translation=translated,
                locations="; ".join(seg.locations),
            ))

    def run_batch(to_run: List[Tuple[RawSegment, ET.Element]]) -> None:
        if not to_run:
            return
        with ThreadPoolExecutor(max_workers=max(1, int(api_parallelism))) as executor:
            futures = {
                executor.submit(
                    translate_masked_segment,
                    seg,
                    matcher,
                    use_glossary,
                    client,
                    provider,
                    model_id,
                    num_candidates,
                    max_output_tokens,
                    temperature,
                    top_p,
                    use_response_schema,
                    rate_limiter,
                    max_retries,
                    retry_base_sleep,
                ): (seg, msg)
                for seg, msg in to_run
            }
            for fut in as_completed(futures):
                seg, _msg = futures[fut]
                try:
                    results_by_id[seg.id] = fut.result()
                except Exception as e:
                    tb = traceback.format_exc()
                    results_by_id[seg.id] = (seg, seg.source, [], {"status": "thread_exception_source_fallback", "exception_repr": repr(e), "traceback": tb, "api_call_count": 1, "candidates": []})

        with checkpoint_path.open("a", encoding="utf-8") as f:
            for seg, _msg in to_run:
                _seg, translated, issues, meta = results_by_id[seg.id]
                row = {
                    "segment_id": seg.id,
                    "index": seg.index,
                    "context": seg.context,
                    "source": seg.source,
                    "translation": translated,
                    "issues": issues,
                    "status": meta.get("status"),
                    "meta": meta,
                    "model_id": model_id,
                    "mode": "built_in_masked_template",
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    for seg, msg in pairs:
        if seg.id in done_by_id:
            row = done_by_id[seg.id]
            translated = str(row.get("translation", ""))
            issues = list(row.get("issues", []) or [])
            meta = dict(row.get("meta", {}) or {"status": "reused_from_checkpoint", "api_call_count": 0, "candidates": []})
            consume(seg, msg, translated, issues, meta)
            continue
        batch.append((seg, msg))
        if len(batch) >= 20:
            print(f"[mask-internal] translating batch ending at segment {seg.index} ({len(batch)} segments)", file=sys.stderr)
            run_batch(batch)
            for bseg, bmsg in batch:
                _seg, translated, issues, meta = results_by_id[bseg.id]
                consume(bseg, bmsg, translated, issues, meta)
            batch = []
    if batch:
        print(f"[mask-internal] translating final batch ({len(batch)} segments)", file=sys.stderr)
        run_batch(batch)
        for bseg, bmsg in batch:
            _seg, translated, issues, meta = results_by_id[bseg.id]
            consume(bseg, bmsg, translated, issues, meta)

    xml_indent(root)
    output_ts.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_ts, encoding="utf-8", xml_declaration=True)

    xml_well_formed = True
    xml_parse_error = ""
    try:
        ET.parse(output_ts)
    except Exception as e:
        xml_well_formed = False
        xml_parse_error = repr(e)
        all_issues.append(TranslationIssue("__output__", -1, "__output__", "output_xml_parse_failed", "critical", xml_parse_error, str(input_ts), str(output_ts), ""))

    with issues_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["segment_id", "index", "context", "issue_type", "severity", "detail", "source", "translation", "locations"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for issue in all_issues:
            writer.writerow(asdict(issue))

    summary = {
        "input_ts": str(input_ts),
        "output_ts": str(output_ts),
        "provider": provider,
        "model_id": model_id,
        "workflow": "built_in_masked_template_ablation",
        "total_segments": len(pairs),
        "num_candidates": int(num_candidates),
        "ods_enabled": bool(use_glossary),
        "api_call_count": api_call_count,
        "status_counts": dict(counters),
        "translated_ok": int(counters.get("ok_format_safe_template", 0)),
        "format_safe_accept_count": format_safe_accept_count,
        "safe_fallback_count": safe_fallback_count,
        "candidate_count_total": candidate_count_total,
        "issue_count": len(all_issues),
        "issue_counts": dict(Counter(i.issue_type for i in all_issues)),
        "severity_counts": dict(Counter(i.severity for i in all_issues)),
        "xml_well_formed": xml_well_formed,
        "xml_parse_error": xml_parse_error,
        "glossary_entry_count": len(glossary_entries) if use_glossary else 0,
        "glossary_matcher": matcher.stats() if use_glossary else {},
        "reports": {"translation_log": str(checkpoint_path), "translation_issues_csv": str(issues_csv_path)},
    }
    write_json(summary_path, summary)
    return summary

# -----------------------------------------------------------------------------
# Masked condition runner via existing scripts
# -----------------------------------------------------------------------------
def provider_default_mask_script(provider: str, script_dir: Path) -> Path:
    names = [
        f"api_{provider}_structure100.py",
        f"api_{provider}.py",
    ]
    for name in names:
        p = script_dir / name
        if p.exists():
            return p
    for name in names:
        p = Path.cwd() / name
        if p.exists():
            return p
    # Return expected name so error message is useful.
    return script_dir / names[0]


def run_masked_condition(
    input_ts: Path,
    output_ts: Path,
    outdir: Path,
    provider: str,
    model_id: str,
    glossary_paths: Sequence[Path],
    use_glossary: bool,
    num_candidates: int,
    api_key: Optional[str],
    api_parallelism: int,
    rpm_limit: Optional[int],
    max_retries: int,
    retry_base_sleep: float,
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    no_resume: bool,
    use_response_schema: bool,
    mask_script: Optional[Path],
    target_language: str,
) -> Dict[str, Any]:
    """Run a masked condition.

    Standalone default: use the built-in masked translator in this file.
    Optional external mode: pass --mask-script to call api_grok_structure100.py or
    api_gemini_structure100.py.  This keeps backward compatibility, but it is no
    longer required.
    """
    if mask_script is not None:
        outdir.mkdir(parents=True, exist_ok=True)
        script = mask_script
        if not script.exists():
            raise FileNotFoundError(f"Masked translator script not found: {script}")
        cmd: List[str] = [
            sys.executable,
            str(script),
            "--input", str(input_ts),
            "--output", str(output_ts),
            "--outdir", str(outdir),
            "--target-language", target_language,
            "--model-id", model_id,
            "--num-candidates", str(int(num_candidates)),
            "--api-parallelism", str(int(api_parallelism)),
            "--max-retries", str(int(max_retries)),
            "--retry-base-sleep", str(float(retry_base_sleep)),
            "--max-output-tokens", str(int(max_output_tokens)),
            "--temperature", str(float(temperature)),
            "--top-p", str(float(top_p)),
            "--segment-batch-size", "20",
        ]
        if rpm_limit:
            cmd += ["--rpm-limit", str(int(rpm_limit))]
        if api_key:
            cmd += ["--api-key", str(api_key)]
        if no_resume:
            cmd += ["--no-resume"]
        if not use_response_schema:
            cmd += ["--no-response-schema"]
        if use_glossary:
            cmd += ["--glossary", *[str(p) for p in glossary_paths]]
        else:
            cmd += ["--glossary"]
        print("[mask-external] running:", " ".join(cmd), file=sys.stderr)
        started = time.time()
        proc = subprocess.run(cmd, text=True, capture_output=True)
        duration = time.time() - started
        (outdir / "subprocess_stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
        (outdir / "subprocess_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(
                f"Masked translator failed for {output_ts.name} with exit code {proc.returncode}. "
                f"See {outdir / 'subprocess_stderr.txt'}"
            )
        summary_path = outdir / "translation_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        summary.update({
            "mask_subprocess_duration_seconds": round(duration, 3),
            "mask_script": str(script),
            "mask": True,
            "ods_enabled": bool(use_glossary),
            "standalone_builtin_mask_used": False,
        })
        write_json(outdir / "translation_summary_with_ablation_meta.json", summary)
        return summary

    glossary_entries, glossary_warnings = load_glossaries(glossary_paths) if use_glossary else ([], [])
    summary = run_internal_masked_condition(
        input_ts=input_ts,
        output_ts=output_ts,
        outdir=outdir,
        provider=provider,
        model_id=model_id,
        glossary_entries=glossary_entries,
        use_glossary=use_glossary,
        num_candidates=num_candidates,
        api_key=api_key,
        api_parallelism=api_parallelism,
        rpm_limit=rpm_limit,
        max_retries=max_retries,
        retry_base_sleep=retry_base_sleep,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        top_p=top_p,
        no_resume=no_resume,
        use_response_schema=use_response_schema,
    )
    summary.update({
        "mask": True,
        "ods_enabled": bool(use_glossary),
        "standalone_builtin_mask_used": True,
        "glossary_warnings": glossary_warnings[:50],
    })
    write_json(outdir / "translation_summary_with_ablation_meta.json", summary)
    return summary


# -----------------------------------------------------------------------------
# Evaluation runner
# -----------------------------------------------------------------------------
def find_eval_script(explicit: Optional[Path]) -> Optional[Path]:
    if explicit:
        return explicit
    for name in ["evaluate_all_structure100.py", "evaluate_all.py"]:
        p = Path(__file__).resolve().parent / name
        if p.exists():
            return p
        p = Path.cwd() / name
        if p.exists():
            return p
    return None


def run_evaluator(outputs_dir: Path, eval_outdir: Path, eval_script: Path, structure_score_limit: int) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(eval_script),
        "--all-ts",
        "--ts-dir", str(outputs_dir),
        "--outdir", str(eval_outdir),
        "--script-check", "none",
        "--sample-size", "0",
        "--repeats", "1",
        "--structure-score-limit", str(int(structure_score_limit)),
    ]
    print("[eval] running:", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, text=True, capture_output=True)
    eval_outdir.mkdir(parents=True, exist_ok=True)
    (eval_outdir / "subprocess_stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (eval_outdir / "subprocess_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"Evaluator failed with exit code {proc.returncode}. See {eval_outdir / 'subprocess_stderr.txt'}")
    summary_json = eval_outdir / "all_ts_mqm_scores.json"
    if summary_json.exists():
        return json.loads(summary_json.read_text(encoding="utf-8"))
    return {"stdout": proc.stdout[-4000:] if proc.stdout else ""}


# -----------------------------------------------------------------------------
# Main orchestration
# -----------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run QGIS .ts C0-C4 ablation workflow on a fixed subset.")
    p.add_argument("--input", type=Path, default=Path("qgis_en.ts"), help="Input English/source Qt .ts file.")
    p.add_argument("--workdir", type=Path, default=Path("qgis_ablation_workflow"), help="Workflow output directory.")
    p.add_argument("--provider", choices=["grok", "gemini"], default="grok")
    p.add_argument("--model-id", default="grok-4.3", help="Model ID, e.g. grok-4.3 or gemini-3.1-flash-lite.")
    p.add_argument("--api-key", default=None, help="Optional API key. Prefer environment variables.")
    p.add_argument("--glossary", nargs="*", type=Path, default=[Path("1.ods"), Path("2.ods")], help="ODS/CSV/XLSX glossary files.")
    p.add_argument("--target-language", default="zh-Hant")
    p.add_argument("--sample-size", type=int, default=3000, help="Subset size. Use 0 for full corpus.")
    p.add_argument("--subset-mode", choices=["stratified", "random", "first"], default="stratified")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--conditions", default="C0,C1,C2,C3,C4", help="Comma-separated condition IDs to run.")
    p.add_argument("--mask-script", type=Path, default=None, help="Path to api_grok_structure100.py or api_gemini_structure100.py.")
    p.add_argument("--eval-script", type=Path, default=None, help="Path to evaluate_all_structure100.py.")
    p.add_argument("--run-eval", action="store_true", help="Evaluate all generated C0-C4 .ts files after translation.")
    p.add_argument("--api-parallelism", type=int, default=4)
    p.add_argument("--rpm-limit", type=int, default=None)
    p.add_argument("--max-retries", type=int, default=5)
    p.add_argument("--retry-base-sleep", type=float, default=2.0)
    p.add_argument("--max-output-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.15)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--no-response-schema", action="store_true")
    p.add_argument("--no-resume", action="store_true", help="Do not reuse per-condition checkpoints.")
    p.add_argument("--raw-candidate-selection", choices=["first", "best_format"], default="best_format", help="How C2 no-mask 3-candidate output is selected.")
    p.add_argument("--hard-lock-raw", action="store_true", help="For no-mask conditions, fallback to source when selected candidate has structure issues. Usually OFF for ablation.")
    p.add_argument("--skip-subset-if-exists", action="store_true", help="Reuse an existing subset TS and selected_segments.csv.")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        raise FileNotFoundError(f"Input TS not found: {args.input}")

    workflow_api_key = effective_api_key(args.provider, args.api_key)
    if not workflow_api_key:
        if args.provider == "grok":
            raise RuntimeError(
                "Grok/xAI API key is required. Pass --api-key, fill HARDCODED_XAI_API_KEY "
                "inside this script, or set XAI_API_KEY / GROK_API_KEY."
            )
        if args.provider == "gemini":
            raise RuntimeError(
                "Gemini API key is required. Pass --api-key, fill HARDCODED_GEMINI_API_KEY "
                "inside this script, or set GEMINI_API_KEY / GOOGLE_API_KEY."
            )

    args.workdir.mkdir(parents=True, exist_ok=True)
    subset_dir = args.workdir / "subset"
    conditions_dir = args.workdir / "conditions"
    outputs_dir = args.workdir / "outputs_ts"
    eval_outdir = args.workdir / "evaluation"
    subset_dir.mkdir(parents=True, exist_ok=True)
    conditions_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[load] loading glossary files: {', '.join(str(p) for p in args.glossary) if args.glossary else '(none)'}", file=sys.stderr)
    glossary_entries, glossary_warnings = load_glossaries(args.glossary)
    glossary_matcher = GlossaryMatcher(glossary_entries)
    write_json(args.workdir / "glossary_summary.json", {
        "glossary_files": [str(p) for p in args.glossary],
        "entry_count": len(glossary_entries),
        "warnings": glossary_warnings,
        "matcher": glossary_matcher.stats(),
    })

    subset_ts = subset_dir / f"{args.input.stem}_subset_{args.sample_size if args.sample_size > 0 else 'full'}.ts"
    selected_csv = subset_dir / "selected_segments.csv"

    tree = ET.parse(args.input)
    source_root = tree.getroot()
    infos = iter_segment_infos(source_root, glossary_matcher=glossary_matcher)

    if args.skip_subset_if_exists and subset_ts.exists() and selected_csv.exists():
        print(f"[subset] reusing existing subset: {subset_ts}", file=sys.stderr)
        selected_indices: List[int] = []
        with selected_csv.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                selected_indices.append(int(row["original_index"]))
        selected_set = set(selected_indices)
        selected_infos = [x for x in infos if x.original_index in selected_set]
    else:
        selected_indices = select_stratified_subset(infos, args.sample_size, seed=args.seed, subset_mode=args.subset_mode)
        selected_set = set(selected_indices)
        selected_infos = [x for x in infos if x.original_index in selected_set]
        make_subset_ts(args.input, subset_ts, selected_indices, target_language=args.target_language)
        write_subset_report(selected_csv, selected_infos)

    category_counts = Counter(cat for x in selected_infos for cat in x.categories)
    subset_summary = {
        "input_ts": str(args.input),
        "subset_ts": str(subset_ts),
        "total_messages": len(infos),
        "selected_messages": len(selected_infos),
        "sample_size_requested": args.sample_size,
        "subset_mode": args.subset_mode,
        "seed": args.seed,
        "category_counts": dict(category_counts),
        "selected_segments_csv": str(selected_csv),
    }
    write_json(subset_dir / "subset_summary.json", subset_summary)
    print(json.dumps(subset_summary, ensure_ascii=False, indent=2), file=sys.stderr)

    wanted = {x.strip().upper() for x in args.conditions.split(",") if x.strip()}
    condition_list = [c for c in CONDITIONS if c["id"] in wanted]
    if not condition_list:
        raise ValueError(f"No valid conditions selected from {args.conditions!r}")

    condition_summaries: List[Dict[str, Any]] = []
    for cond in condition_list:
        slug = str(cond["slug"])
        cond_outdir = conditions_dir / slug
        output_ts = cond_outdir / f"{slug}.ts"
        copy_ts = outputs_dir / f"{slug}.ts"
        cond_outdir.mkdir(parents=True, exist_ok=True)
        print(f"[condition] {cond['id']} {cond['description']}", file=sys.stderr)
        write_json(cond_outdir / "condition.json", cond)

        try:
            if cond["mask"]:
                summary = run_masked_condition(
                    input_ts=subset_ts,
                    output_ts=output_ts,
                    outdir=cond_outdir,
                    provider=args.provider,
                    model_id=args.model_id,
                    glossary_paths=args.glossary,
                    use_glossary=bool(cond["ods"]),
                    num_candidates=int(cond["num_candidates"]),
                    api_key=workflow_api_key,
                    api_parallelism=args.api_parallelism,
                    rpm_limit=args.rpm_limit,
                    max_retries=args.max_retries,
                    retry_base_sleep=args.retry_base_sleep,
                    max_output_tokens=args.max_output_tokens,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    no_resume=args.no_resume,
                    use_response_schema=not args.no_response_schema,
                    mask_script=args.mask_script,
                    target_language=args.target_language,
                )
            else:
                summary = run_raw_condition(
                    input_ts=subset_ts,
                    output_ts=output_ts,
                    outdir=cond_outdir,
                    provider=args.provider,
                    model_id=args.model_id,
                    glossary_entries=glossary_entries,
                    use_glossary=bool(cond["ods"]),
                    num_candidates=int(cond["num_candidates"]),
                    api_key=workflow_api_key,
                    api_parallelism=args.api_parallelism,
                    rpm_limit=args.rpm_limit,
                    max_retries=args.max_retries,
                    retry_base_sleep=args.retry_base_sleep,
                    max_output_tokens=args.max_output_tokens,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    no_resume=args.no_resume,
                    raw_candidate_selection="first" if cond["num_candidates"] == 1 else args.raw_candidate_selection,
                    hard_lock_final=bool(args.hard_lock_raw),
                    use_response_schema=not args.no_response_schema,
                )
            # Copy each final output .ts into a flat directory for batch evaluation.
            if output_ts.exists():
                copy_ts.write_bytes(output_ts.read_bytes())
            summary = {
                "condition": cond,
                "summary": summary,
                "output_ts": str(output_ts),
                "flat_output_ts": str(copy_ts),
                "status": "ok",
            }
        except Exception as e:
            summary = {
                "condition": cond,
                "output_ts": str(output_ts),
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
            }
            write_json(cond_outdir / "error.json", summary)
            print(f"[condition error] {cond['id']}: {e}", file=sys.stderr)
            # Continue other conditions so one failed arm does not destroy the whole run.
        condition_summaries.append(summary)
        write_json(args.workdir / "condition_summaries_partial.json", condition_summaries)

    eval_summary: Dict[str, Any] = {}
    if args.run_eval:
        eval_script = find_eval_script(args.eval_script)
        if eval_script is None or not eval_script.exists():
            raise FileNotFoundError("Evaluator script not found. Pass --eval-script path/to/evaluate_all_structure100.py")
        eval_summary = run_evaluator(
            outputs_dir=outputs_dir,
            eval_outdir=eval_outdir,
            eval_script=eval_script,
            structure_score_limit=len(selected_infos) if len(selected_infos) > 0 else 10000,
        )

    final_manifest = {
        "workflow": "qgis_ts_c0_c4_ablation",
        "provider": args.provider,
        "model_id": args.model_id,
        "input_ts": str(args.input),
        "workdir": str(args.workdir),
        "subset": subset_summary,
        "conditions": CONDITIONS,
        "selected_condition_ids": sorted(wanted),
        "condition_summaries": condition_summaries,
        "outputs_dir": str(outputs_dir),
        "evaluation": eval_summary,
        "notes": [
            "C0 and C2 are no-mask direct translation conditions implemented by this script.",
            "C1, C3, and C4 call the existing format-safe masked translator script.",
            "For ablation, --hard-lock-raw is off by default so no-mask structure failures remain visible.",
            "Use the same selected_segments.csv for all models to keep ablation comparisons fair.",
        ],
    }
    write_json(args.workdir / "workflow_manifest.json", final_manifest)
    print(json.dumps(final_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
