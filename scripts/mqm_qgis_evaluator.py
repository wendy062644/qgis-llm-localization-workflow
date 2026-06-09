#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mqm_qgis_evaluator.py

Standalone MQM-style evaluator for QGIS / Qt Linguist .ts translation outputs.

It complements the deterministic structure statistics produced by score_qgis.py.
The deterministic checks in this script are used as PRE-CHECKS for prompt context
and sampling. The final MQM score is based on LLM-judge error annotations.

Outputs:
  outdir/mqm_requests.jsonl
  outdir/mqm_results.jsonl
  outdir/selected_mqm_segments.csv
  outdir/mqm_segment_scores.csv
  outdir/mqm_error_items.csv
  outdir/mqm_file_summary.csv
  outdir/mqm_condition_summary.csv
  outdir/mqm_summary.json
  outdir/mqm_report.md

Install:
  pip install -U openai httpx pandas odfpy openpyxl

Example dry-run:
  python mqm_qgis_evaluator.py --ts-dir paper_ablation_grok_3000/outputs_ts \
    --outdir paper_ablation_grok_3000/mqm_eval --glossary 1.ods 2.ods \
    --total-request-budget 3000 --repeats 3

Example run Grok judge:
  export XAI_API_KEY = ""
  python mqm_qgis_evaluator.py --ts-dir paper_ablation_grok_3000/outputs_ts \
    --outdir paper_ablation_grok_3000/mqm_eval --glossary 1.ods 2.ods \
    --total-request-budget 3000 --repeats 3 --run-grok --max-workers 4 --rpm-limit 60
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import random
import re
import statistics
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

DEFAULT_MODEL = "grok-4.3"
DEFAULT_TARGET_LANGUAGE = "zh-Hant"
PROMPT_VERSION = "qgis_mqm_zh_tw_v1_standalone"
DEFAULT_SAMPLE_WINDOW = 10000
XAI_API_KEY = ""

# Canonical MQM-like severity penalties. The model may output penalty values,
# but this program overwrites them from severity labels for reproducibility.
SEVERITY_PENALTY = {
    "Neutral": 0.0,
    "Minor": 1.0,
    "Major": 5.0,
    "Critical": 25.0,
}
DASHBOARD_POINTS_PER_ERROR_POINT = 4.0

# Technical tokens / software-localization invariants.
RE_QT_PLACEHOLDER = re.compile(r"%(?:L)?\d+|%n")
RE_BRACE_PLACEHOLDER = re.compile(r"\{[^{}\n]*\}")
RE_PRINTF_PLACEHOLDER = re.compile(r"%(?:[+#0\-]*)?(?:\d+|\*)?(?:\.\d+)?[hlL]?[diouxXeEfFgGcs]")
RE_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|hellip|[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);")
RE_TAG = re.compile(r"</?\s*([A-Za-z][\w:.-]*)(?:\s+[^<>]*)?/?>")
RE_NUMBER = re.compile(r"(?<![%\w])[-+]?\d+(?:[.,]\d+)?(?![%\w])")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")
RE_LATIN = re.compile(r"[A-Za-z]")
RE_TERM_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[+#._:-]+[A-Za-z0-9]+)*[+#]*")
RE_ESCAPED_CONTROL = re.compile(r"\\[nrt]")
COPY_OK = re.compile(r"^(?:[A-Z0-9_+\-./:%#(){}\[\]<>*|\\ ]+|[A-Za-z0-9_+\-./:%#(){}\[\]<>*|\\ ]{1,12})$")
HTML_TAGS = {
    "html", "head", "body", "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "span", "div", "b", "i", "u", "strong", "em", "code", "pre", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "td", "th", "font", "style", "img", "blockquote",
}

STYLE_GUIDE = """
繁體中文（台灣）QGIS/軟體介面評估準則：
- 目標語應是自然、清楚、適合 UI 的繁體中文（台灣）。
- 不應因個人偏好扣分；只有會影響理解、術語一致性、介面可用性或格式安全的問題才標錯。
- 保留軟體必要元素：%1、%n、{}、{name}、printf placeholder、HTML/XML tag、entity、數字、換行、快捷鍵。
- 專案詞庫優先於一般翻譯偏好；若詞庫允許多個譯名，不因其中一個合理譯名扣分。
- QGIS/GIS 常見術語需一致，例如 layer=圖層、feature=圖徵、raster=網格、vector=向量、CRS=CRS。
- 保留合理英文專有名詞、API、SQL、檔名、副檔名、程式識別字與標準縮寫。
""".strip()


@dataclass
class Segment:
    id: str
    index: int
    context: str
    source: str
    translation: str
    translations: List[str]
    translation_type: str
    numerus: bool
    locations: List[str]
    comment: str = ""
    extracomment: str = ""
    ts_file: str = ""


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
class DeterministicIssue:
    segment_id: str
    index: int
    context: str
    issue_type: str
    severity: str
    detail: str
    source: str
    translation: str
    locations: str


def stable_id(context: str, source: str, index: int) -> str:
    digest = hashlib.sha1(f"{context}\u241f{source}\u241f{index}".encode("utf-8")).hexdigest()[:12]
    return f"seg_{index:06d}_{digest}"


def text_of(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext())


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def source_char_count(seg: Segment) -> int:
    return max(1, len(seg.source or ""))


def csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


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
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                rows.append({"_json_parse_error": line[:500]})
    return rows


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: List[str] = []
        seen = set()
        for row in rows:
            for k in row.keys():
                if k not in seen:
                    keys.append(k)
                    seen.add(k)
        fieldnames = keys
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_value(row.get(k, "")) for k in fieldnames})


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def dashboard_score_from_epk(epk: float) -> float:
    # Primary formal metric remains error points per 1000 source characters.
    # The 0-100 score is a dashboard representation.
    return round(max(0.0, 100.0 - float(epk)), 3)


def condition_id_from_path(path: Path) -> str:
    m = re.search(r"(C\d+)", path.stem)
    return m.group(1) if m else path.stem


def parse_ts(path: Path) -> Tuple[ET.Element, List[Segment]]:
    tree = ET.parse(path)
    root = tree.getroot()
    segments: List[Segment] = []
    idx = 0
    for context in root.findall("context"):
        ctx_name = context.findtext("name") or ""
        for msg in context.findall("message"):
            idx += 1
            source = msg.findtext("source") or ""
            tr_el = msg.find("translation")
            tr_type = "missing_translation_element" if tr_el is None else tr_el.attrib.get("type", "")
            numerus = msg.attrib.get("numerus") == "yes"
            if tr_el is None:
                translations = [""]
            else:
                forms = tr_el.findall("numerusform")
                translations = [text_of(f) for f in forms] if forms else [text_of(tr_el)]
            translation_joined = " ||| ".join(translations)
            locations: List[str] = []
            for loc in msg.findall("location"):
                fn = loc.attrib.get("filename", "")
                line = loc.attrib.get("line", "")
                locations.append(f"{fn}:{line}" if line else fn)
            segments.append(
                Segment(
                    id=stable_id(ctx_name, source, idx),
                    index=idx,
                    context=ctx_name,
                    source=source,
                    translation=translation_joined,
                    translations=translations,
                    translation_type=tr_type,
                    numerus=numerus,
                    locations=locations,
                    comment=msg.findtext("comment") or "",
                    extracomment=msg.findtext("extracomment") or "",
                    ts_file=str(path),
                )
            )
    return root, segments


def discover_ts_files(ts_dir: Path, recursive: bool = False) -> List[Path]:
    pattern = "**/*.ts" if recursive else "*.ts"
    return sorted(p for p in ts_dir.glob(pattern) if p.is_file())


# -----------------------------
# Glossary loading/matching
# -----------------------------
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

    def relevant_entries(self, source: str, max_entries: int = 12) -> List[GlossaryEntry]:
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


# -----------------------------
# Deterministic pre-checks
# -----------------------------
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
    critical = {"&lt;", "&gt;", "&amp;", "&quot;", "&apos;", "&nbsp;"}
    return [tok for tok in RE_ENTITY.findall(text or "") if tok in critical]


def html_tag_names(text: str) -> List[str]:
    names: List[str] = []
    for m in RE_TAG.finditer(text or ""):
        name = m.group(1).lower()
        if name in HTML_TAGS:
            names.append(name)
    return names


def number_tokens(text: str) -> List[str]:
    return RE_NUMBER.findall(text or "")


def accelerator_tokens(text: str) -> List[str]:
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


def add_issue(issues: List[DeterministicIssue], seg: Segment, issue_type: str, severity: str, detail: str) -> None:
    issues.append(
        DeterministicIssue(
            segment_id=seg.id,
            index=seg.index,
            context=seg.context,
            issue_type=issue_type,
            severity=severity,
            detail=detail,
            source=seg.source,
            translation=seg.translation,
            locations="; ".join(seg.locations),
        )
    )


def structure_forms(seg: Segment) -> List[Tuple[int, str]]:
    if seg.numerus and seg.translations:
        return [(i + 1, t) for i, t in enumerate(seg.translations)]
    return [(0, seg.translation)]


def check_token_preservation(seg: Segment, issues: List[DeterministicIssue], name: str, extractor, missing_sev: str = "critical", extra_sev: str = "major") -> None:
    for form_idx, translation in structure_forms(seg):
        missing, extra = counter_diff(extractor(seg.source), extractor(translation))
        prefix = f"numerusform[{form_idx}]: " if form_idx else ""
        if missing:
            add_issue(issues, seg, f"missing_{name}", missing_sev, f"{prefix}Missing {name}: {missing}")
        if extra:
            add_issue(issues, seg, f"extra_{name}", extra_sev, f"{prefix}Extra {name}: {extra}")


def deterministic_precheck(root: ET.Element, segments: Sequence[Segment], glossary: Sequence[GlossaryEntry], target_language: str) -> List[DeterministicIssue]:
    issues: List[DeterministicIssue] = []
    matcher = GlossaryMatcher(glossary)
    if target_language and root.attrib.get("language") not in {target_language, "zh_TW", "zh-TW", "zh-Hant", "zh_Hant"}:
        dummy = Segment("file_metadata", 0, "@file", "", "", [""], "", False, [])
        add_issue(issues, dummy, "ts_language_mismatch", "major", f"TS@language is {root.attrib.get('language')!r}; expected {target_language!r}.")

    for seg in segments:
        src_norm = normalize_text(seg.source)
        trg_norm = normalize_text(seg.translation)
        if not trg_norm:
            add_issue(issues, seg, "empty_translation", "critical", "Translation is empty")
        if seg.translation_type == "unfinished":
            add_issue(issues, seg, "unfinished_translation", "major", "translation@type='unfinished'")
        if seg.translation_type == "missing_translation_element":
            add_issue(issues, seg, "missing_translation_element", "critical", "No <translation> element")
        if src_norm and trg_norm and src_norm == trg_norm and not COPY_OK.fullmatch(src_norm):
            add_issue(issues, seg, "possibly_untranslated", "major", "Source and translation are identical")
        if src_norm and (trg_norm.startswith(src_norm + " -") or trg_norm.startswith(src_norm + " –") or trg_norm.startswith(src_norm + " —")):
            add_issue(issues, seg, "bilingual_residue", "major", "Translation appears to keep English source before a dash")
        latin_count = len(RE_LATIN.findall(seg.translation))
        cjk_count = len(RE_CJK.findall(seg.translation))
        if cjk_count > 0 and latin_count > max(12, cjk_count * 1.2) and not any(x in seg.source for x in ["SQL", "HTML", "API", "CRS"]):
            add_issue(issues, seg, "high_english_residue", "minor", f"Latin letters={latin_count}, CJK chars={cjk_count}")

        check_token_preservation(seg, issues, "qt_placeholder", qt_tokens)
        check_token_preservation(seg, issues, "brace_placeholder", brace_tokens)
        check_token_preservation(seg, issues, "printf_placeholder", printf_tokens)
        check_token_preservation(seg, issues, "html_xml_entity", entity_tokens, "major", "major")
        check_token_preservation(seg, issues, "html_xml_tag", html_tag_names, "major", "major")

        for form_idx, translation in structure_forms(seg):
            prefix = f"numerusform[{form_idx}]: " if form_idx else ""
            missing_num, extra_num = counter_diff(number_tokens(seg.source), number_tokens(translation))
            if missing_num:
                add_issue(issues, seg, "missing_number", "major", f"{prefix}Missing numeric token(s): {missing_num}")
            if extra_num:
                add_issue(issues, seg, "extra_number", "minor", f"{prefix}Extra numeric token(s): {extra_num}")
            if seg.source.count("\n") != translation.count("\n"):
                add_issue(issues, seg, "newline_count_mismatch", "minor", f"{prefix}source_newlines={seg.source.count(chr(10))}, target_newlines={translation.count(chr(10))}")
            missing_acc, extra_acc = counter_diff(accelerator_tokens(seg.source), accelerator_tokens(translation))
            if missing_acc or extra_acc:
                add_issue(issues, seg, "accelerator_count_mismatch", "minor", f"{prefix}missing={missing_acc}, extra={extra_acc}")

        # Glossary is diagnostic; final MQM judge decides whether this is a true error in context.
        for entry in matcher.relevant_entries(seg.source):
            if entry.forbidden_terms:
                bad = [t for t in entry.forbidden_terms if t and t in seg.translation]
                if bad:
                    sev = "major" if entry.priority == "high" else "minor"
                    add_issue(issues, seg, "forbidden_term", sev, f"Source term {entry.source_term!r} uses forbidden target term(s): {bad}")
            if entry.target_terms and not any(t in seg.translation for t in entry.target_terms):
                sev = "major" if entry.priority == "high" else ("minor" if entry.priority == "medium" else "info")
                add_issue(issues, seg, "glossary_target_missing", sev, f"Source term {entry.source_term!r} expected target term(s): {entry.target_terms}")
    return issues


# -----------------------------
# MQM prompt/schema
# -----------------------------
def mqm_response_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "segment_id": {"type": "string"},
            "acceptability": {"type": "string", "enum": ["Accept", "Minor Revision", "Major Revision", "Reject"]},
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": ["Accuracy", "Terminology", "Fluency", "StyleLocale", "Format", "UIUsability", "SourceIssue"]},
                        "subcategory": {"type": "string", "enum": [
                            "Mistranslation", "Omission", "Addition", "WrongTerm", "InconsistentTerm",
                            "Grammar", "Unnatural", "Punctuation", "LocaleConvention", "Register",
                            "Placeholder", "Tag", "Entity", "Number", "Newline", "Mnemonic",
                            "TooLong", "AmbiguousUI", "SourceAmbiguity", "Other"
                        ]},
                        "severity": {"type": "string", "enum": ["Neutral", "Minor", "Major", "Critical"]},
                        "penalty": {"type": "number"},
                        "source_span": {"type": "string"},
                        "target_span": {"type": "string"},
                        "explanation_zh_tw": {"type": "string"},
                        "suggested_correction": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["category", "subcategory", "severity", "penalty", "source_span", "target_span", "explanation_zh_tw", "suggested_correction", "confidence"],
                    "additionalProperties": False,
                },
            },
            "weighted_error_points": {"type": "number"},
            "mqm_score_0_100": {"type": "number"},
            "improved_translation": {"type": "string"},
            "summary_zh_tw": {"type": "string"},
        },
        "required": ["segment_id", "acceptability", "errors", "weighted_error_points", "mqm_score_0_100", "improved_translation", "summary_zh_tw"],
        "additionalProperties": False,
    }


def glossary_lines(entries: Sequence[GlossaryEntry]) -> str:
    if not entries:
        return "- 本句未命中特定詞庫術語；仍需依 QGIS/GIS/軟體在地化常識評估。"
    lines = []
    for e in entries[:30]:
        forbidden = f"；禁用：{'、'.join(e.forbidden_terms)}" if e.forbidden_terms else ""
        note = f"；備註：{e.note}" if e.note else ""
        lines.append(f"- {e.source_term} => {' / '.join(e.target_terms)}；priority={e.priority}{forbidden}{note}")
    return "\n".join(lines)


def issue_lines(issues: Sequence[DeterministicIssue]) -> str:
    if not issues:
        return "- 無 deterministic pre-check 問題。"
    lines = []
    for i in issues[:25]:
        lines.append(f"- [{i.severity}] {i.issue_type}: {i.detail}")
    if len(issues) > 25:
        lines.append(f"- ... 另有 {len(issues) - 25} 個 pre-check issue 未列出")
    return "\n".join(lines)


def build_mqm_prompt(seg: Segment, glossary: Sequence[GlossaryEntry], precheck_issues: Sequence[DeterministicIssue]) -> str:
    locations = "; ".join(seg.locations) if seg.locations else ""
    plural_note = ""
    if seg.numerus and len(seg.translations) > 1:
        plural_note = "\nPlural forms are joined with ' ||| '. Judge each plural form fairly; identical Chinese plural forms may be acceptable if %n is preserved."
    return f"""
你是繁體中文（台灣）軟體在地化與 GIS/QGIS 翻譯審查員。
請依據 MQM-style analytic Translation Quality Evaluation 評估此翻譯。

重要原則：
1. 只標註實際錯誤，不要因個人偏好扣分。
2. 若譯文可接受但你有更好說法，最多標 Neutral 或不標錯。
3. 若 placeholder、HTML/XML tag、entity、數字、快捷鍵、換行被破壞，依影響標 Major 或 Critical。
4. 若專案詞庫要求特定譯名，詞庫優先於一般偏好；但仍要確認語境是否真的需要該詞。
5. 若 source 本身是保留字、API、SQL、檔名、副檔名、快捷鍵、公式、單位，通常應保留。
6. 使用台灣繁體中文判斷自然度與在地化；避免簡體字與中國大陸慣用語。
7. deterministic pre-check 僅是候選問題，不能直接照抄成錯誤；必須依 source/target 自行確認。

Severity 與 penalty（輸出 severity 即可，程式會重新計算 penalty）：
- Neutral = 0：不影響理解或只是偏好。
- Minor = 1：小錯，基本不影響使用。
- Major = 5：明顯錯誤，會誤導、降低可用性，或造成術語/格式風險。
- Critical = 25：相反意思、嚴重漏譯、破壞格式造成軟體錯誤、重大資料/安全風險。

Style guide:
{STYLE_GUIDE}

專案詞庫命中：
{glossary_lines(glossary)}

Deterministic pre-check（候選問題，必須自行確認）：
{issue_lines(precheck_issues)}

Segment metadata:
- segment_id: {seg.id}
- index: {seg.index}
- context: {seg.context}
- locations: {locations}
- comment: {seg.comment}
- extracomment: {seg.extracomment}
- numerus: {seg.numerus}{plural_note}

Source English:
{seg.source}

Target zh-TW translation:
{seg.translation}

請輸出 JSON，欄位必須符合 schema。
weighted_error_points 請等於所有 errors.penalty 的總和。
mqm_score_0_100 請用 max(0, 100 - weighted_error_points * 4) 粗略轉換，僅作單句 dashboard 分數。
""".strip()


def strict_schema(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {k: strict_schema(v) for k, v in obj.items()}
        if out.get("type") == "object" or "properties" in out:
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(obj, list):
        return [strict_schema(x) for x in obj]
    return obj


def response_format(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "qgis_mqm_translation_judgment",
            "schema": strict_schema(schema),
            "strict": True,
        },
    }


# -----------------------------
# Sampling / request planning
# -----------------------------
def select_segments_for_mqm(
    segments: Sequence[Segment],
    issues_by_segment: Dict[str, List[DeterministicIssue]],
    sample_size: int,
    seed: int,
    sampling_mode: str,
    sample_window: int,
) -> List[Segment]:
    window = DEFAULT_SAMPLE_WINDOW if sample_window <= 0 else min(sample_window, DEFAULT_SAMPLE_WINDOW)
    pool = list(segments[: min(len(segments), window)])
    if sample_size <= 0 or sample_size >= len(pool):
        return pool
    rng = random.Random(seed)
    if sampling_mode == "random":
        chosen = pool[:]
        rng.shuffle(chosen)
        return chosen[:sample_size]

    severity_rank = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    issue_sorted = sorted(
        [i for i in (x for xs in issues_by_segment.values() for x in xs)],
        key=lambda i: (severity_rank.get(i.severity, 9), i.index),
    )
    seg_by_id = {s.id: s for s in pool}
    selected_ids: List[str] = []

    def add_issue_enriched(limit: int) -> None:
        for issue in issue_sorted:
            if issue.segment_id in seg_by_id and issue.segment_id not in selected_ids:
                selected_ids.append(issue.segment_id)
            if len(selected_ids) >= limit:
                break

    if sampling_mode == "mixed":
        add_issue_enriched(sample_size // 2)
        remaining = [s.id for s in pool if s.id not in selected_ids]
        rng.shuffle(remaining)
        selected_ids.extend(remaining[: sample_size - len(selected_ids)])
    else:
        add_issue_enriched(sample_size)
        remaining = [s.id for s in pool if s.id not in selected_ids]
        rng.shuffle(remaining)
        selected_ids.extend(remaining[: sample_size - len(selected_ids)])
    return [seg_by_id[sid] for sid in selected_ids[:sample_size] if sid in seg_by_id]


def effective_sample_size(args: argparse.Namespace, ts_files: Sequence[Path]) -> Tuple[int, Dict[str, Any]]:
    if args.total_request_budget <= 0:
        return int(args.sample_size), {"enabled": False}
    denominator = max(1, len(ts_files) * max(1, int(args.repeats)))
    per_run = max(1, int(args.total_request_budget) // denominator)
    plan = {
        "enabled": True,
        "total_request_budget_requested": int(args.total_request_budget),
        "file_count": len(ts_files),
        "repeats": int(args.repeats),
        "sample_size_per_file_per_run": per_run,
        "effective_total_requests": per_run * denominator,
        "unused_budget_due_to_rounding": max(0, int(args.total_request_budget) - per_run * denominator),
    }
    return per_run, plan


def build_requests(
    ts_files: Sequence[Path],
    glossary: Sequence[GlossaryEntry],
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], Segment], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    matcher = GlossaryMatcher(glossary, max_ngram=int(args.max_ngram))
    requests: List[Dict[str, Any]] = []
    seg_map: Dict[Tuple[str, str], Segment] = {}
    selected_rows: List[Dict[str, Any]] = []
    deterministic_issue_rows: List[Dict[str, Any]] = []

    per_run_sample_size, budget_plan = effective_sample_size(args, ts_files)
    for file_no, ts_path in enumerate(ts_files, start=1):
        root, segments = parse_ts(ts_path)
        file_key = ts_path.name
        condition_id = condition_id_from_path(ts_path)
        issues = deterministic_precheck(root, segments, glossary, args.target_language)
        issues_by_segment: Dict[str, List[DeterministicIssue]] = defaultdict(list)
        for issue in issues:
            issues_by_segment[issue.segment_id].append(issue)
            row = asdict(issue)
            row.update({"ts_file": str(ts_path), "condition_id": condition_id})
            deterministic_issue_rows.append(row)
        for seg in segments:
            seg_map[(file_key, seg.id)] = seg

        for run_no in range(1, max(1, int(args.repeats)) + 1):
            run_id = f"run_{run_no:02d}"
            run_seed = int(args.seed) + run_no - 1
            selected = select_segments_for_mqm(
                segments,
                issues_by_segment,
                sample_size=per_run_sample_size,
                seed=run_seed,
                sampling_mode=args.sampling_mode,
                sample_window=args.sample_window,
            )
            for pos, seg in enumerate(selected, start=1):
                relevant = matcher.relevant_entries(seg.source, max_entries=12)
                precheck = issues_by_segment.get(seg.id, [])
                prompt = build_mqm_prompt(seg, relevant, precheck)
                request_key = f"{file_key}|{condition_id}|{run_id}|{pos:04d}|{seg.id}"
                requests.append({
                    "request_key": request_key,
                    "ts_file": str(ts_path),
                    "ts_file_name": file_key,
                    "condition_id": condition_id,
                    "run_id": run_id,
                    "sample_position": pos,
                    "segment_id": seg.id,
                    "index": seg.index,
                    "source_chars": source_char_count(seg),
                    "context": seg.context,
                    "source": seg.source,
                    "translation": seg.translation,
                    "numerus": seg.numerus,
                    "prompt_version": PROMPT_VERSION,
                    "judge_model": args.model,
                    "contents": prompt,
                    "schema": mqm_response_schema(),
                })
                selected_rows.append({
                    "ts_file": str(ts_path),
                    "condition_id": condition_id,
                    "run_id": run_id,
                    "sample_position": pos,
                    "segment_id": seg.id,
                    "index": seg.index,
                    "context": seg.context,
                    "source": seg.source,
                    "translation": seg.translation,
                    "translation_type": seg.translation_type,
                    "numerus": seg.numerus,
                    "source_chars": source_char_count(seg),
                    "precheck_issue_count": len(precheck),
                    "locations": "; ".join(seg.locations),
                })
    meta = {
        "budget_plan": budget_plan,
        "requested_ts_files": [str(p) for p in ts_files],
        "total_requests": len(requests),
        "sample_size_per_file_per_run": per_run_sample_size,
        "glossary_entry_count": len(glossary),
    }
    return requests, seg_map, meta, selected_rows, deterministic_issue_rows


# -----------------------------
# Grok judge call
# -----------------------------
class RateLimiter:
    def __init__(self, rpm: int = 0):
        self.rpm = int(rpm or 0)
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


def effective_api_key(explicit: str = "") -> str:
    for key in [explicit, os.environ.get("XAI_API_KEY", ""), os.environ.get("GROK_API_KEY", "")]:
        key = (key or "").strip()
        if key:
            return key
    return ""


def extract_response_text(resp: Any) -> str:
    # OpenAI Python SDK object.
    try:
        content = resp.choices[0].message.content
        if isinstance(content, str):
            return content
    except Exception:
        pass
    # Generic dictionary fallback.
    if isinstance(resp, dict):
        choices = resp.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]
    return str(resp)


def parse_json_object_text(text: str) -> Dict[str, Any]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(cleaned[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("Could not parse response text as JSON object")


def call_grok(request: Dict[str, Any], args: argparse.Namespace, rate_limiter: RateLimiter) -> Dict[str, Any]:
    from openai import OpenAI  # type: ignore
    api_key = effective_api_key(args.api_key)
    if not api_key:
        raise RuntimeError("xAI/Grok API key required. Set XAI_API_KEY or pass --api-key.")
    client = OpenAI(api_key=api_key, base_url=args.api_base_url, timeout=float(args.timeout_seconds))
    body: Dict[str, Any] = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a strict MQM translation evaluator. Return JSON only."},
            {"role": "user", "content": request["contents"]},
        ],
        "temperature": 0,
    }
    if not args.no_response_schema:
        body["response_format"] = response_format(request.get("schema") or mqm_response_schema())
    if args.max_output_tokens > 0:
        body["max_tokens"] = int(args.max_output_tokens)

    last_error: Optional[Exception] = None
    for attempt in range(1, int(args.max_retries) + 2):
        try:
            rate_limiter.acquire()
            try:
                resp = client.chat.completions.create(**body)
            except Exception as e:
                if "max_tokens" in body and ("max_tokens" in repr(e) or "max_completion_tokens" in repr(e)):
                    body2 = dict(body)
                    body2["max_completion_tokens"] = body2.pop("max_tokens")
                    resp = client.chat.completions.create(**body2)
                else:
                    raise
            text = extract_response_text(resp)
            payload = parse_json_object_text(text)
            payload["_raw_text"] = text
            payload["_request_key"] = request["request_key"]
            payload["_ts_file"] = request["ts_file"]
            payload["_ts_file_name"] = request["ts_file_name"]
            payload["_condition_id"] = request["condition_id"]
            payload["_run_id"] = request["run_id"]
            payload["_sample_position"] = request["sample_position"]
            payload["_judge_model"] = args.model
            payload["_prompt_version"] = PROMPT_VERSION
            if not payload.get("segment_id"):
                payload["segment_id"] = request["segment_id"]
            return payload
        except Exception as e:  # pragma: no cover - requires live API
            last_error = e
            retryable = any(x in repr(e) for x in ["429", "RateLimit", "timeout", "Timeout", "503", "500", "504", "Connection"])
            if attempt > int(args.max_retries) or not retryable:
                break
            time.sleep(float(args.retry_base_sleep) * attempt)
    return {
        "segment_id": request.get("segment_id", ""),
        "_request_key": request["request_key"],
        "_ts_file": request["ts_file"],
        "_ts_file_name": request["ts_file_name"],
        "_condition_id": request["condition_id"],
        "_run_id": request["run_id"],
        "_sample_position": request["sample_position"],
        "_judge_model": args.model,
        "_prompt_version": PROMPT_VERSION,
        "_error": f"Grok call failed: {last_error!r}",
        "errors": [],
        "weighted_error_points": None,
        "mqm_score_0_100": None,
        "acceptability": "Reject",
        "improved_translation": "",
        "summary_zh_tw": "Grok request failed.",
    }


def normalize_mqm_result(result: Dict[str, Any], request_by_key: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(result)
    key = str(out.get("_request_key") or "")
    request = request_by_key.get(key, {})
    out.setdefault("_ts_file", request.get("ts_file", ""))
    out.setdefault("_ts_file_name", request.get("ts_file_name", ""))
    out.setdefault("_condition_id", request.get("condition_id", ""))
    out.setdefault("_run_id", request.get("run_id", ""))
    out.setdefault("_sample_position", request.get("sample_position", ""))
    out.setdefault("segment_id", request.get("segment_id", ""))
    out["source_chars"] = int(request.get("source_chars", 1) or 1)

    if out.get("_error"):
        return out

    raw_errors = out.get("errors", [])
    warnings = list(out.get("_normalization_warnings", []) or [])
    if not isinstance(raw_errors, list):
        warnings.append("errors was not a list; ignored")
        raw_errors = []
    normalized_errors: List[Dict[str, Any]] = []
    total = 0.0
    for err in raw_errors:
        if not isinstance(err, dict):
            warnings.append(f"ignored non-object error: {err!r}")
            continue
        e = dict(err)
        sev = str(e.get("severity", "Neutral"))
        if sev not in SEVERITY_PENALTY:
            warnings.append(f"unknown severity {sev!r}; treated as Neutral")
            sev = "Neutral"
        penalty = float(SEVERITY_PENALTY[sev])
        e["severity"] = sev
        e["penalty"] = penalty
        total += penalty
        normalized_errors.append(e)
    out["errors"] = normalized_errors
    out["weighted_error_points"] = round(total, 3)
    out["mqm_score_0_100"] = round(max(0.0, 100.0 - total * DASHBOARD_POINTS_PER_ERROR_POINT), 3)
    out["_normalized_penalties"] = True
    if warnings:
        out["_normalization_warnings"] = warnings
    return out


def run_grok_requests(requests: Sequence[Dict[str, Any]], args: argparse.Namespace, results_path: Path) -> List[Dict[str, Any]]:
    existing = read_jsonl(results_path) if args.resume else []
    done = {str(r.get("_request_key")) for r in existing if r.get("_request_key")}
    to_run = [r for r in requests if r["request_key"] not in done]
    print(f"[grok] existing={len(existing)} to_run={len(to_run)}", file=sys.stderr)
    if not to_run:
        return existing
    rate_limiter = RateLimiter(args.rpm_limit)
    lock = threading.Lock()
    all_results = list(existing)
    with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as executor:
        futures = {executor.submit(call_grok, req, args, rate_limiter): req for req in to_run}
        finished = 0
        for fut in as_completed(futures):
            req = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                result = {
                    "segment_id": req.get("segment_id", ""),
                    "_request_key": req["request_key"],
                    "_ts_file": req["ts_file"],
                    "_ts_file_name": req["ts_file_name"],
                    "_condition_id": req["condition_id"],
                    "_run_id": req["run_id"],
                    "_sample_position": req["sample_position"],
                    "_error": f"worker exception: {type(e).__name__}: {e}",
                    "_traceback": traceback.format_exc(),
                    "errors": [],
                    "weighted_error_points": None,
                    "mqm_score_0_100": None,
                    "acceptability": "Reject",
                    "improved_translation": "",
                    "summary_zh_tw": "Worker failed.",
                }
            with lock:
                append_jsonl(results_path, result)
                all_results.append(result)
                finished += 1
                if finished % max(1, int(args.progress_interval)) == 0 or finished == len(to_run):
                    print(f"[grok] completed {finished}/{len(to_run)}", file=sys.stderr)
    return all_results


# -----------------------------
# Aggregation
# -----------------------------
def build_score_tables(results: Sequence[Dict[str, Any]], request_by_key: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    normalized = [normalize_mqm_result(r, request_by_key) for r in results]
    segment_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []

    for res in normalized:
        key = str(res.get("_request_key") or "")
        req = request_by_key.get(key, {})
        errors = res.get("errors") if isinstance(res.get("errors"), list) else []
        category_counts = Counter(e.get("category", "Unknown") for e in errors if isinstance(e, dict))
        severity_counts = Counter(e.get("severity", "Unknown") for e in errors if isinstance(e, dict))
        row = {
            "request_key": key,
            "ts_file": res.get("_ts_file", ""),
            "ts_file_name": res.get("_ts_file_name", ""),
            "condition_id": res.get("_condition_id", ""),
            "run_id": res.get("_run_id", ""),
            "sample_position": res.get("_sample_position", ""),
            "segment_id": res.get("segment_id", ""),
            "index": req.get("index", ""),
            "source_chars": int(res.get("source_chars", req.get("source_chars", 1)) or 1),
            "acceptability": res.get("acceptability", ""),
            "weighted_error_points": res.get("weighted_error_points", ""),
            "mqm_score_0_100": res.get("mqm_score_0_100", ""),
            "error_count": len(errors),
            "error_categories": dict(category_counts),
            "error_severity": dict(severity_counts),
            "error": res.get("_error", ""),
            "source": req.get("source", ""),
            "translation": req.get("translation", ""),
            "summary_zh_tw": res.get("summary_zh_tw", ""),
            "improved_translation": res.get("improved_translation", ""),
        }
        segment_rows.append(row)
        for n, err in enumerate(errors, start=1):
            if not isinstance(err, dict):
                continue
            erow = {
                "request_key": key,
                "ts_file": res.get("_ts_file", ""),
                "ts_file_name": res.get("_ts_file_name", ""),
                "condition_id": res.get("_condition_id", ""),
                "run_id": res.get("_run_id", ""),
                "segment_id": res.get("segment_id", ""),
                "index": req.get("index", ""),
                "error_no": n,
                "category": err.get("category", ""),
                "subcategory": err.get("subcategory", ""),
                "severity": err.get("severity", ""),
                "penalty": err.get("penalty", ""),
                "source_span": err.get("source_span", ""),
                "target_span": err.get("target_span", ""),
                "explanation_zh_tw": err.get("explanation_zh_tw", ""),
                "suggested_correction": err.get("suggested_correction", ""),
                "confidence": err.get("confidence", ""),
                "source": req.get("source", ""),
                "translation": req.get("translation", ""),
            }
            error_rows.append(erow)

    run_groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in segment_rows:
        run_groups[(str(row["ts_file_name"]), str(row["ts_file"]), str(row["condition_id"]), str(row["run_id"]))].append(row)

    run_summary_rows: List[Dict[str, Any]] = []
    for (file_name, ts_file, cond, run_id), rows in sorted(run_groups.items()):
        valid = [r for r in rows if r.get("weighted_error_points") not in (None, "")]
        failed = len(rows) - len(valid)
        total_points = sum(float(r.get("weighted_error_points") or 0) for r in valid)
        total_chars = sum(int(r.get("source_chars") or 1) for r in valid)
        epk = (total_points / max(1, total_chars)) * 1000.0
        run_summary_rows.append({
            "row_type": "run",
            "ts_file_name": file_name,
            "ts_file": ts_file,
            "condition_id": cond,
            "run_id": run_id,
            "judged_segments": len(valid),
            "failed_segments": failed,
            "total_source_chars": total_chars,
            "total_weighted_error_points": round(total_points, 3),
            "mqm_error_rate_per_1000_source_chars": round(epk, 3),
            "primary_score_0_100": dashboard_score_from_epk(epk),
            "average_segment_mqm_score_0_100": round(statistics.mean([float(r.get("mqm_score_0_100") or 0) for r in valid]), 3) if valid else "",
            "error_category_counts": dict(Counter(e.get("category", "") for e in error_rows if e.get("ts_file_name") == file_name and e.get("condition_id") == cond and e.get("run_id") == run_id)),
            "error_severity_counts": dict(Counter(e.get("severity", "") for e in error_rows if e.get("ts_file_name") == file_name and e.get("condition_id") == cond and e.get("run_id") == run_id)),
        })

    avg_groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in run_summary_rows:
        avg_groups[(str(row["ts_file_name"]), str(row["ts_file"]), str(row["condition_id"]))].append(row)

    file_summary_rows = list(run_summary_rows)
    condition_summary_rows: List[Dict[str, Any]] = []
    for (file_name, ts_file, cond), rows in sorted(avg_groups.items()):
        scores = [float(r["primary_score_0_100"]) for r in rows if r.get("primary_score_0_100") not in (None, "")]
        epks = [float(r["mqm_error_rate_per_1000_source_chars"]) for r in rows if r.get("mqm_error_rate_per_1000_source_chars") not in (None, "")]
        judged = sum(int(r.get("judged_segments") or 0) for r in rows)
        failed = sum(int(r.get("failed_segments") or 0) for r in rows)
        score_mean = statistics.mean(scores) if scores else None
        epk_mean = statistics.mean(epks) if epks else None
        score_sd = statistics.stdev(scores) if len(scores) >= 2 else 0.0 if scores else None
        epk_sd = statistics.stdev(epks) if len(epks) >= 2 else 0.0 if epks else None
        score_ci = 1.96 * score_sd / math.sqrt(len(scores)) if scores and len(scores) >= 2 else 0.0 if scores else None
        epk_ci = 1.96 * epk_sd / math.sqrt(len(epks)) if epks and len(epks) >= 2 else 0.0 if epks else None
        avg_row = {
            "row_type": "average",
            "ts_file_name": file_name,
            "ts_file": ts_file,
            "condition_id": cond,
            "run_id": "AVERAGE",
            "run_count": len(rows),
            "judged_segments": judged,
            "failed_segments": failed,
            "average_primary_score_0_100": round(score_mean, 3) if score_mean is not None else "",
            "primary_score_stddev": round(score_sd, 3) if score_sd is not None else "",
            "primary_score_95ci_half_width": round(score_ci, 3) if score_ci is not None else "",
            "average_mqm_error_rate_per_1000_source_chars": round(epk_mean, 3) if epk_mean is not None else "",
            "mqm_error_rate_stddev": round(epk_sd, 3) if epk_sd is not None else "",
            "mqm_error_rate_95ci_half_width": round(epk_ci, 3) if epk_ci is not None else "",
        }
        file_summary_rows.append(avg_row)
        condition_summary_rows.append(avg_row)
    return segment_rows, error_rows, file_summary_rows, condition_summary_rows


def write_markdown_report(path: Path, summary: Dict[str, Any], condition_rows: Sequence[Dict[str, Any]]) -> None:
    lines: List[str] = []
    lines.append("# MQM evaluation report")
    lines.append("")
    lines.append(f"- Judge model: `{summary.get('judge_model')}`")
    lines.append(f"- Prompt version: `{PROMPT_VERSION}`")
    lines.append(f"- Total requests: {summary.get('total_requests')}")
    lines.append(f"- Run Grok: {summary.get('run_grok')}")
    lines.append("")
    lines.append("## Condition summary")
    lines.append("")
    cols = ["condition_id", "ts_file_name", "run_count", "judged_segments", "failed_segments", "average_primary_score_0_100", "primary_score_95ci_half_width", "average_mqm_error_rate_per_1000_source_chars", "mqm_error_rate_95ci_half_width"]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] + ["---:"] * (len(cols) - 1)) + "|")
    for row in condition_rows:
        vals = [str(row.get(c, "")) for c in cols]
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("## Interpretation notes")
    lines.append("")
    lines.append("- `mqm_error_rate_per_1000_source_chars` is the primary lower-is-better metric.")
    lines.append("- `primary_score_0_100 = max(0, 100 - error_rate_per_1000_source_chars)` is a dashboard score.")
    lines.append("- Per-segment penalties are canonicalized from severity labels: Neutral=0, Minor=1, Major=5, Critical=25.")
    lines.append("- Deterministic pre-checks are only prompt context; they are not automatically counted as MQM errors.")
    path.write_text("\n".join(lines), encoding="utf-8")


# -----------------------------
# Main
# -----------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Standalone MQM-style evaluator for QGIS/Qt .ts translations.")
    p.add_argument("--ts-dir", type=Path, required=True, help="Directory containing .ts files to judge, e.g. paper_ablation_grok_3000/outputs_ts")
    p.add_argument("--outdir", type=Path, required=True, help="Output directory for MQM reports.")
    p.add_argument("--recursive-ts", action="store_true")
    p.add_argument("--glossary", nargs="*", type=Path, default=[Path("1.ods"), Path("2.ods")])
    p.add_argument("--target-language", default=DEFAULT_TARGET_LANGUAGE)
    p.add_argument("--model", default=DEFAULT_MODEL, help="Grok/xAI judge model.")
    p.add_argument("--api-base-url", default="https://api.x.ai/v1")
    p.add_argument("--api-key", default="", help="Optional xAI API key. Prefer XAI_API_KEY env var.")
    p.add_argument("--run-grok", action="store_true", help="Actually call Grok. If omitted, only mqm_requests.jsonl is created.")
    p.add_argument("--resume", action="store_true", help="Reuse existing mqm_results.jsonl rows by request_key.")
    p.add_argument("--sample-size", type=int, default=200, help="Segments sampled per .ts file per repeat unless total budget is used.")
    p.add_argument("--total-request-budget", type=int, default=0, help="Distribute this total request budget over files × repeats.")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--sample-window", type=int, default=DEFAULT_SAMPLE_WINDOW, help="Only first N parsed segments are eligible; capped at 10000 by default.")
    p.add_argument("--sampling-mode", choices=["random", "issue_enriched", "mixed"], default="random")
    p.add_argument("--max-ngram", type=int, default=8)
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--rpm-limit", type=int, default=60)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--retry-base-sleep", type=float, default=2.0)
    p.add_argument("--timeout-seconds", type=int, default=240)
    p.add_argument("--max-output-tokens", type=int, default=2048)
    p.add_argument("--no-response-schema", action="store_true")
    p.add_argument("--progress-interval", type=int, default=50)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    if not args.ts_dir.exists():
        raise FileNotFoundError(f"TS directory not found: {args.ts_dir}")
    ts_files = discover_ts_files(args.ts_dir, recursive=args.recursive_ts)
    if not ts_files:
        raise FileNotFoundError(f"No .ts files found in {args.ts_dir}")

    print(f"[discover] {len(ts_files)} .ts files", file=sys.stderr)
    print(f"[glossary] loading {', '.join(str(p) for p in args.glossary) if args.glossary else '(none)'}", file=sys.stderr)
    glossary, glossary_warnings = load_glossaries(args.glossary)

    requests, seg_map, meta, selected_rows, deterministic_issue_rows = build_requests(ts_files, glossary, args)
    request_by_key = {r["request_key"]: r for r in requests}

    write_jsonl(args.outdir / "mqm_requests.jsonl", requests)
    write_csv(args.outdir / "selected_mqm_segments.csv", selected_rows)
    write_csv(args.outdir / "mqm_deterministic_precheck_issues.csv", deterministic_issue_rows)
    write_json(args.outdir / "mqm_plan.json", {
        "judge_model": args.model,
        "prompt_version": PROMPT_VERSION,
        "ts_files": [str(p) for p in ts_files],
        "request_count": len(requests),
        "run_grok": bool(args.run_grok),
        "sample_size": args.sample_size,
        "total_request_budget": args.total_request_budget,
        "repeats": args.repeats,
        "sampling_mode": args.sampling_mode,
        "sample_window": args.sample_window,
        "budget_plan": meta.get("budget_plan"),
        "glossary_entry_count": len(glossary),
        "glossary_warnings": glossary_warnings,
    })
    print(f"[plan] wrote {len(requests)} requests to {args.outdir / 'mqm_requests.jsonl'}", file=sys.stderr)

    results_path = args.outdir / "mqm_results.jsonl"
    if args.run_grok:
        results = run_grok_requests(requests, args, results_path)
    else:
        results = read_jsonl(results_path)
        if not results:
            print("[dry-run] --run-grok not set; no MQM calls made.", file=sys.stderr)

    segment_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []
    file_summary_rows: List[Dict[str, Any]] = []
    condition_summary_rows: List[Dict[str, Any]] = []
    if results:
        segment_rows, error_rows, file_summary_rows, condition_summary_rows = build_score_tables(results, request_by_key)
        write_csv(args.outdir / "mqm_segment_scores.csv", segment_rows)
        write_csv(args.outdir / "mqm_error_items.csv", error_rows)
        write_csv(args.outdir / "mqm_file_summary.csv", file_summary_rows)
        write_csv(args.outdir / "mqm_condition_summary.csv", condition_summary_rows)

    summary = {
        "workflow": "qgis_mqm_standalone",
        "judge_model": args.model,
        "prompt_version": PROMPT_VERSION,
        "ts_dir": str(args.ts_dir),
        "outdir": str(args.outdir),
        "ts_files": [str(p) for p in ts_files],
        "total_requests": len(requests),
        "run_grok": bool(args.run_grok),
        "result_count": len(results),
        "scored_segment_count": len(segment_rows),
        "error_item_count": len(error_rows),
        "budget_plan": meta.get("budget_plan"),
        "glossary_entry_count": len(glossary),
        "glossary_warnings": glossary_warnings,
        "outputs": {
            "requests": str(args.outdir / "mqm_requests.jsonl"),
            "results": str(results_path),
            "selected_segments": str(args.outdir / "selected_mqm_segments.csv"),
            "deterministic_precheck_issues": str(args.outdir / "mqm_deterministic_precheck_issues.csv"),
            "segment_scores": str(args.outdir / "mqm_segment_scores.csv"),
            "error_items": str(args.outdir / "mqm_error_items.csv"),
            "file_summary": str(args.outdir / "mqm_file_summary.csv"),
            "condition_summary": str(args.outdir / "mqm_condition_summary.csv"),
            "report": str(args.outdir / "mqm_report.md"),
        },
    }
    write_json(args.outdir / "mqm_summary.json", summary)
    write_markdown_report(args.outdir / "mqm_report.md", summary, condition_summary_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
