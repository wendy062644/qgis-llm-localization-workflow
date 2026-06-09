#!/usr/bin/env python3
"""
QGIS / Qt Linguist .ts translation evaluator with ODS glossary support,
hash-indexed glossary matching, and Grok MQM judging.

This script has two phases:
1. Deterministic validation: Qt TS/XML/placeholder/tag/entity/terminology checks.
   Terminology matching uses n-gram + hash index lookup for speed.
2. Optional LLM-as-a-Judge: Grok performs MQM-style semantic review.

Default mode is safe/dry: it writes reports and Grok request prompts but does NOT
call the xAI/Grok API unless --run-grok is explicitly passed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import traceback
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

# -----------------------------
# Project defaults
# -----------------------------
MODEL_DEFAULT = "grok-4.3"
PROMPT_VERSION = "mqm_zh_tw_qgis_v1.0_structural_v2"
GLOSSARY_VERSION_DEFAULT = "1.ods+2.ods"
MAX_SAMPLE_WINDOW = 10000
DEFAULT_SAMPLE_WINDOW = MAX_SAMPLE_WINDOW
DEFAULT_REPEAT_RUNS = 1
DEFAULT_SAMPLE_SIZE = 50
MQM_DASHBOARD_POINTS_PER_ERROR_POINT = 4.0
MQM_EPK_TO_DASHBOARD_SCORE_MULTIPLIER = 1.0

# Optional inline API key. Keep this empty in committed/shared code.
# For Grok/xAI, use --api-key, XAI_API_KEY, GROK_API_KEY, or a secrets manager.
# A Google/Gemini API key will not authenticate against xAI.
# Do NOT commit a real API key to GitHub.
XAI_API_KEY = ""
GROK_API_KEY = XAI_API_KEY
XAI_API_BASE_URL = "https://api.x.ai/v1"

SEVERITY_PENALTY = {
    "Neutral": 0.0,
    "Minor": 1.0,
    "Major": 5.0,
    "Critical": 25.0,
}

DETERMINISTIC_PENALTY = {
    "critical": 12.0,
    "major": 5.0,
    "minor": 1.5,
    "info": 0.0,
}

# Technical tokens and software-localization invariants.
RE_QT_PLACEHOLDER = re.compile(r"%(?:L)?\d+|%n")
RE_BRACE_PLACEHOLDER = re.compile(r"\{[^{}\n]*\}")
RE_PRINTF_PLACEHOLDER = re.compile(r"%(?:[+#0\-]*)?(?:\d+|\*)?(?:\.\d+)?[hlL]?[diouxXeEfFgGcs]")
RE_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|hellip|[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);")
RE_TAG = re.compile(r"</?\s*([A-Za-z][\w:.-]*)(?:\s+[^<>]*)?/?>")
RE_NUMBER = re.compile(r"(?<![%\w])[-+]?\d+(?:[.,]\d+)?(?![%\w])")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")
RE_LATIN = re.compile(r"[A-Za-z]")
RE_SEGMENT_SPLIT = re.compile(r"\s*[|;；]\s*")

HTML_TAGS = {
    "html", "head", "body", "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "span", "div", "b", "i", "u", "strong", "em", "code", "pre", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "td", "th", "font", "style", "img", "blockquote",
}

# Chinese script/locale checking is intentionally NOT hard-coded.
# Paper-grade runs should use either:
#   1. Unicode Unihan_Variants.txt via --script-check unihan, or
#   2. OpenCC dictionaries via --script-check opencc/both.
# This avoids maintaining an ad-hoc SIMPLIFIED_SUSPECT table in the evaluator.

# Some strings are intentionally language-neutral and should not be counted as untranslated.
COPY_OK = re.compile(r"^(?:[A-Z0-9_+\-./:%#(){}\[\]<>*|\\ ]+|[A-Za-z0-9_+\-./:%#(){}\[\]<>*|\\ ]{1,12})$")

DEFAULT_STYLE_GUIDE_SUMMARY = """
繁體中文（台灣）QGIS/軟體介面風格摘要：
- 目標語使用繁體中文，避免簡體字與中國大陸慣用詞。
- UI 動作按鈕盡量簡潔；錯誤與警告訊息要清楚指出原因與下一步。
- 保留所有技術 token：%1、%n、{}、{name}、HTML/XML tag、entity、數字、檔案副檔名、快捷鍵。
- 專有名詞以專案詞庫為最高優先；若詞庫允許多個譯名，不因個人偏好扣分。
- QGIS/GIS 常見詞需一致，例如 layer=圖層、feature=圖徵、raster=網格、vector=向量、CRS=CRS。
- 對使用者稱謂保持一致，避免過度口語或過度中國化表述。
""".strip()

# -----------------------------
# Data models
# -----------------------------
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

@dataclass
class GlossaryEntry:
    source_term: str
    target_terms: List[str]
    forbidden_terms: List[str] = field(default_factory=list)
    priority: str = "medium"  # high / medium / low
    domain: str = ""
    note: str = ""
    source_file: str = ""
    sheet: str = ""

@dataclass
class Issue:
    segment_id: str
    index: int
    context: str
    issue_type: str
    severity: str
    detail: str
    source: str
    translation: str
    locations: str


@dataclass
class ScriptVariantHit:
    char: str
    kind: str
    evidence: str


@dataclass
class ScriptVariantDetector:
    """Detect zh-TW script/locale risks using external references, not a hand-built table.

    Modes:
    - unihan: Uses Unicode Unihan_Variants.txt. Characters with kTraditionalVariant
      are treated as candidate simplified/variant forms in a zh-Hant target.
    - opencc: Uses OpenCC s2tw conversion. Differences are treated as candidate
      script/locale risks.
    - both: Runs both detectors and de-duplicates hits.

    The detector only produces *candidate* issues for review. It is not a semantic
    translation judge and should not replace MQM/Grok/human review.
    """
    mode: str = "none"
    unihan_source: str = ""
    unihan_ktraditional_chars: set[str] = field(default_factory=set)
    opencc_converter: Any = None
    warnings: List[str] = field(default_factory=list)

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        return "\u3400" <= ch <= "\u9fff" or "\U00020000" <= ch <= "\U0002ebe0"

    @staticmethod
    def from_unihan_file(path: Path, mode: str = "unihan") -> "ScriptVariantDetector":
        detector = ScriptVariantDetector(mode=mode, unihan_source=str(path))
        if not path or not path.exists():
            detector.warnings.append(
                f"Unihan variants file not found: {path}. Script variant check disabled for Unihan. "
                "Download Unicode Unihan.zip and pass --unihan-variants Unihan_Variants.txt, "
                "or use --script-check opencc."
            )
            return detector
        count = 0
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                codepoint, prop, values = parts[0], parts[1], parts[2]
                if prop != "kTraditionalVariant" or not codepoint.startswith("U+"):
                    continue
                try:
                    ch = chr(int(codepoint[2:], 16))
                except ValueError:
                    continue
                # Ignore self-variant records if present.
                variant_codes = [v.split("<", 1)[0] for v in values.split()]
                if codepoint in variant_codes:
                    continue
                detector.unihan_ktraditional_chars.add(ch)
                count += 1
        detector.warnings.append(f"Loaded {len(detector.unihan_ktraditional_chars)} kTraditionalVariant candidate characters from {path}.")
        return detector

    @staticmethod
    def from_opencc(mode: str = "opencc") -> "ScriptVariantDetector":
        detector = ScriptVariantDetector(mode=mode)
        try:
            from opencc import OpenCC  # type: ignore
            try:
                detector.opencc_converter = OpenCC("s2tw")
            except Exception:
                # Some Python wrappers require a config filename.
                detector.opencc_converter = OpenCC("s2tw.json")
            detector.warnings.append("OpenCC s2tw converter loaded.")
        except Exception as e:
            detector.warnings.append(
                "OpenCC is not installed or could not be loaded. "
                "Install with `python -m pip install OpenCC` or use --script-check unihan. "
                f"Original error: {e}"
            )
        return detector

    @staticmethod
    def build(mode: str, unihan_path: Optional[Path]) -> "ScriptVariantDetector":
        mode = (mode or "none").lower()
        if mode == "none":
            return ScriptVariantDetector(mode="none")
        if mode == "unihan":
            return ScriptVariantDetector.from_unihan_file(unihan_path or Path("Unihan_Variants.txt"), mode="unihan")
        if mode == "opencc":
            return ScriptVariantDetector.from_opencc(mode="opencc")
        if mode == "both":
            uni = ScriptVariantDetector.from_unihan_file(unihan_path or Path("Unihan_Variants.txt"), mode="both")
            occ = ScriptVariantDetector.from_opencc(mode="both")
            uni.opencc_converter = occ.opencc_converter
            uni.warnings.extend(occ.warnings)
            return uni
        return ScriptVariantDetector(mode="none", warnings=[f"Unknown script-check mode: {mode}. Disabled."])

    def detect(self, text: str) -> List[ScriptVariantHit]:
        if not text or self.mode == "none":
            return []
        hits: Dict[Tuple[str, str], ScriptVariantHit] = {}

        if self.mode in {"unihan", "both"} and self.unihan_ktraditional_chars:
            for ch in text:
                if ch in self.unihan_ktraditional_chars:
                    hits[(ch, "unihan_kTraditionalVariant")] = ScriptVariantHit(
                        ch, "unihan_kTraditionalVariant", "Unicode Unihan kTraditionalVariant"
                    )

        if self.mode in {"opencc", "both"} and self.opencc_converter is not None:
            try:
                converted = self.opencc_converter.convert(text)
            except Exception as e:
                self.warnings.append(f"OpenCC conversion failed once: {e}")
                converted = text
            if converted != text:
                # Keep this intentionally light-weight: report unique changed CJK chars.
                # It avoids expensive span alignment and still provides enough evidence
                # for downstream MQM/Grok/human review.
                import difflib
                sm = difflib.SequenceMatcher(None, text, converted)
                for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
                    if tag == "equal":
                        continue
                    for ch in text[i1:i2]:
                        if self._is_cjk(ch):
                            hits[(ch, "opencc_s2tw_changed")] = ScriptVariantHit(
                                ch, "opencc_s2tw_changed", f"OpenCC s2tw converted text to: {converted}"
                            )

        return sorted(hits.values(), key=lambda h: (h.char, h.kind))

    def stats(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "unihan_source": self.unihan_source,
            "unihan_candidate_char_count": len(self.unihan_ktraditional_chars),
            "opencc_available": self.opencc_converter is not None,
            "warnings": self.warnings,
        }


# -----------------------------
# Utilities
# -----------------------------
def stable_id(context: str, source: str, index: int) -> str:
    digest = hashlib.sha1(f"{context}\u241f{source}\u241f{index}".encode("utf-8")).hexdigest()[:12]
    return f"seg_{index:06d}_{digest}"


def text_of(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext())


def read_config(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required to read YAML config files. Install with: pip install pyyaml")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a YAML mapping/object.")
    return data


def ensure_outdir(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


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
    names = []
    for m in RE_TAG.finditer(text or ""):
        name = m.group(1).lower()
        if name in HTML_TAGS:
            names.append(name)
    return names


def accelerator_tokens(text: str) -> List[str]:
    """Return full Qt mnemonic tokens, e.g. &Save -> ['&S'].

    Escaped literal ampersands (&&) and XML/HTML entities are ignored.  The
    zh-TW suffix convention 儲存(&S) is treated as the same token ['&S'].
    This is stricter than a count-only check and verifies the character after &.
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
            continue
        tokens.append("&")
        i += 1
    return tokens


def accelerator_count(text: str) -> int:
    # Backward-compatible count for older reports.
    return len(accelerator_tokens(text))


def issue_penalty(issue: Issue) -> float:
    return DETERMINISTIC_PENALTY.get(issue.severity, 1.0)

# -----------------------------
# TS parsing
# -----------------------------
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
            locs = []
            for loc in msg.findall("location"):
                fn = loc.attrib.get("filename", "")
                line = loc.attrib.get("line", "")
                locs.append(f"{fn}:{line}" if line else fn)
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
                    locations=locs,
                    comment=msg.findtext("comment") or "",
                    extracomment=msg.findtext("extracomment") or "",
                )
            )
    return root, segments

# -----------------------------
# Glossary handling
# -----------------------------
HEADER_ALIASES = {
    "source": ["source", "source_term", "english", "en", "term", "原文", "英文", "英語", "來源", "source term"],
    "target": ["target", "target_term", "translation", "traditional chinese", "zh", "zh-tw", "zh_tw", "中文", "繁中", "繁體中文", "譯文", "翻譯", "target term"],
    "forbidden": ["forbidden", "forbidden_terms", "禁止", "禁用", "錯誤譯法"],
    "priority": ["priority", "severity", "重要性", "優先", "等級"],
    "domain": ["domain", "領域", "分類", "category"],
    "note": ["note", "備註", "說明", "comment"],
}


def _norm_header(x: Any) -> str:
    return str(x or "").strip().lower().replace("\n", " ")


def infer_columns(df: Any) -> Dict[str, Optional[str]]:
    headers = list(df.columns)
    normalized = {_norm_header(c): c for c in headers}
    out: Dict[str, Optional[str]] = {k: None for k in HEADER_ALIASES}
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            a = alias.lower()
            if a in normalized:
                out[key] = normalized[a]
                break
        if out[key] is None:
            # Fuzzy containment.
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
    parts = [p.strip() for p in RE_SEGMENT_SPLIT.split(text) if p.strip()]
    return parts or [text]


def load_glossary_file(path: Path) -> Tuple[List[GlossaryEntry], List[str]]:
    warnings: List[str] = []
    entries: List[GlossaryEntry] = []
    suffix = path.suffix.lower()
    if suffix == ".csv":
        tables = {"csv": pd.read_csv(path, dtype=str, keep_default_na=False) if pd is not None else None}
    elif suffix in {".ods", ".xlsx", ".xls"}:
        if pd is None:
            raise RuntimeError("pandas is required to read ODS/XLSX glossary files. Install with: pip install pandas odfpy openpyxl")
        engine = "odf" if suffix == ".ods" else None
        tables = pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False, engine=engine)
    else:
        raise ValueError(f"Unsupported glossary format: {path}. Use .ods, .csv, .xlsx, or .xls")

    for sheet_name, df in tables.items():
        if df is None or df.empty:
            continue
        # Drop completely empty rows/columns.
        df = df.dropna(how="all")
        df = df.loc[:, [c for c in df.columns if df[c].astype(str).str.strip().replace({"nan": ""}).ne("").any()]]
        if df.empty:
            continue
        cols = infer_columns(df)
        if not cols.get("source") or not cols.get("target"):
            warnings.append(f"{path.name}:{sheet_name}: could not infer source/target columns; skipped")
            continue
        if _norm_header(cols["source"]) not in [_norm_header(x) for xs in HEADER_ALIASES.values() for x in xs] and _norm_header(cols["target"]) not in [_norm_header(x) for xs in HEADER_ALIASES.values() for x in xs]:
            warnings.append(f"{path.name}:{sheet_name}: source/target columns inferred as {cols['source']!r}/{cols['target']!r}; verify this mapping")
        for _, row in df.iterrows():
            src = str(row.get(cols["source"], "")).strip()
            tgts = split_terms(row.get(cols["target"], ""))
            if not src or src.lower() == "nan" or not tgts:
                continue
            forbidden = split_terms(row.get(cols["forbidden"], "")) if cols.get("forbidden") else []
            priority = str(row.get(cols["priority"], "medium")).strip().lower() if cols.get("priority") else "medium"
            if priority not in {"high", "medium", "low"}:
                priority = "medium"
            domain = str(row.get(cols["domain"], "")).strip() if cols.get("domain") else ""
            note = str(row.get(cols["note"], "")).strip() if cols.get("note") else ""
            entries.append(
                GlossaryEntry(
                    source_term=src,
                    target_terms=tgts,
                    forbidden_terms=forbidden,
                    priority=priority,
                    domain=domain,
                    note=note,
                    source_file=path.name,
                    sheet=str(sheet_name),
                )
            )
    return entries, warnings


def load_glossaries(paths: Sequence[Path]) -> Tuple[List[GlossaryEntry], List[str]]:
    entries: List[GlossaryEntry] = []
    warnings: List[str] = []
    for path in paths:
        if not path.exists():
            warnings.append(f"Glossary file not found: {path}")
            continue
        got, warn = load_glossary_file(path)
        entries.extend(got)
        warnings.extend(warn)
    # Deduplicate by source+targets+forbidden.
    seen = set()
    deduped: List[GlossaryEntry] = []
    for e in entries:
        key = (e.source_term.casefold(), tuple(e.target_terms), tuple(e.forbidden_terms))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    deduped.sort(key=lambda e: len(e.source_term), reverse=True)
    return deduped, warnings


def save_normalized_glossary(path: Path, entries: Sequence[GlossaryEntry]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source_term", "target_terms", "forbidden_terms", "priority", "domain", "source_file", "sheet", "note"],
        )
        writer.writeheader()
        for e in entries:
            writer.writerow(
                {
                    "source_term": e.source_term,
                    "target_terms": "|".join(e.target_terms),
                    "forbidden_terms": "|".join(e.forbidden_terms),
                    "priority": e.priority,
                    "domain": e.domain,
                    "source_file": e.source_file,
                    "sheet": e.sheet,
                    "note": e.note,
                }
            )

# -----------------------------
# Deterministic checks
# -----------------------------
def add_issue(issues: List[Issue], seg: Segment, issue_type: str, severity: str, detail: str) -> None:
    issues.append(
        Issue(
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


def structure_check_translations(seg: Segment) -> List[Tuple[int, str]]:
    """Return translation forms for structure checks.

    For numerus messages, each <numerusform> is checked against the source
    independently.  The old joined-form check made a valid two-form translation
    look as if it had duplicate %n/%1 tokens.
    """
    if seg.numerus and seg.translations:
        return [(i + 1, t) for i, t in enumerate(seg.translations)]
    return [(0, seg.translation)]


def _form_detail_prefix(form_index: int) -> str:
    return f"numerusform[{form_index}]: " if form_index else ""


def check_token_preservation(seg: Segment, issues: List[Issue], name: str, extractor) -> None:
    for form_index, translation in structure_check_translations(seg):
        missing, extra = counter_diff(extractor(seg.source), extractor(translation))
        prefix = _form_detail_prefix(form_index)
        if missing:
            add_issue(issues, seg, f"missing_{name}", "critical", f"{prefix}Missing {name}: {missing}")
        if extra:
            add_issue(issues, seg, f"extra_{name}", "major", f"{prefix}Extra {name}: {extra}")


# -----------------------------------------------------------------------------
# Enhanced structural-format marker checks beyond placeholders/tags/entities
# -----------------------------------------------------------------------------
try:
    from ts_format_guard import FORMAT_GUARD_PROMPT_NOTE, validate_translation as _guard_validate_translation
    FORMAT_GUARD_ENABLED = True
    if FORMAT_GUARD_PROMPT_NOTE not in DEFAULT_STYLE_GUIDE_SUMMARY:
        DEFAULT_STYLE_GUIDE_SUMMARY = DEFAULT_STYLE_GUIDE_SUMMARY + "\n" + FORMAT_GUARD_PROMPT_NOTE
except Exception as _format_guard_import_error:  # pragma: no cover
    FORMAT_GUARD_ENABLED = False
    _guard_validate_translation = None

# Legacy issue types already produced by the evaluator. The guard also checks
# these, so filter them out to avoid duplicate rows in deterministic_issues.csv.
_LEGACY_EVALUATOR_ISSUE_TYPES = {
    "empty_translation", "unfinished_translation", "missing_translation_element",
    "possibly_untranslated", "bilingual_residue", "high_english_residue", "script_variant_risk",
    "missing_qt_placeholder", "extra_qt_placeholder",
    "missing_brace_placeholder", "extra_brace_placeholder",
    "missing_printf_placeholder", "extra_printf_placeholder",
    "missing_html_xml_entity", "extra_html_xml_entity",
    "missing_html_xml_tag", "extra_html_xml_tag",
    "missing_number", "extra_number", "newline_count_mismatch", "accelerator_count_mismatch",
}


def check_enhanced_structural_markers(seg: Segment, issues: List[Issue]) -> None:
    """Add quote/code/escape/operator structure issues missed by legacy checks."""
    if not FORMAT_GUARD_ENABLED or _guard_validate_translation is None:
        return
    try:
        guard_issues = _guard_validate_translation(seg.source, seg.translation, include_content_risk=True)
    except Exception as e:
        add_issue(issues, seg, "format_guard_exception", "minor", f"Enhanced structural guard failed: {e!r}")
        return
    seen_details = {(i.issue_type, i.detail) for i in issues if i.segment_id == seg.id}
    for item in guard_issues:
        issue_type = str(item.get("issue_type", "format_guard_issue"))
        if issue_type in _LEGACY_EVALUATOR_ISSUE_TYPES:
            continue
        detail = str(item.get("detail", ""))
        if (issue_type, detail) in seen_details:
            continue
        severity = str(item.get("severity", "minor")).lower()
        if severity not in {"critical", "major", "minor", "info"}:
            severity = "minor"
        add_issue(issues, seg, issue_type, severity, detail)


# -----------------------------
# Hash-based glossary matching
# -----------------------------
# The old implementation compared every segment with every glossary entry:
#   O(number_of_segments * number_of_glossary_terms)
# This version builds a hash index from normalized source terms and then turns
# each source string into n-gram candidates. Each candidate is looked up in O(1)
# average time via a Python dict.
RE_TERM_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[+#._-]+[A-Za-z0-9]+)*[+#]*")


def term_tokens(text: str) -> List[str]:
    """Tokenize terms/source text for glossary lookup.

    This is intentionally simple and deterministic:
    - case-insensitive
    - keeps common technical tokens such as C++, qgis_process, 3d, epsg:4326-ish parts
    - ignores punctuation boundaries such as slash, parentheses, colon, and apostrophe
    """
    text = html.unescape(text or "").casefold()
    return [m.group(0).strip("_.-") for m in RE_TERM_TOKEN.finditer(text) if m.group(0).strip("_.-")]


def singularize_token(token: str) -> str:
    """Lightweight English plural normalization for UI/glossary matching.

    This is not a linguistic lemmatizer. It only helps glossary terms like
    "feature" match source strings containing "features". It deliberately avoids
    risky changes for words ending in ss/us/is.
    """
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


def legacy_term_in_source(term: str, source: str) -> bool:
    """Fallback matcher for terms that cannot be tokenized."""
    if not term:
        return False
    if re.fullmatch(r"[A-Za-z0-9_ .+\-/()]+", term):
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])"
        return re.search(pattern, source, flags=re.IGNORECASE) is not None
    return term in source


@dataclass
class GlossaryHashMatcher:
    """Hash index for source-term lookup.

    index[n][("raster", "layer")] -> [GlossaryEntry(...)]

    At evaluation time, a source string such as "Export raster layers" becomes
    candidate n-grams like ("export",), ("raster",), ("layer",),
    ("raster", "layer"), etc. Each candidate is checked with dict lookup.
    """

    entries: Sequence[GlossaryEntry]
    max_ngram: int = 8
    index: Dict[int, Dict[Tuple[str, ...], List[GlossaryEntry]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    fallback_entries: List[GlossaryEntry] = field(default_factory=list)
    indexed_entry_count: int = 0
    skipped_long_entry_count: int = 0

    def __post_init__(self) -> None:
        max_seen = 0
        for entry in self.entries:
            key = canonical_term_key(entry.source_term)
            if not key:
                self.fallback_entries.append(entry)
                continue
            if len(key) > self.max_ngram:
                # Extremely long glossary phrases are rare in UI terminology and
                # expensive to n-gram. Keep them in fallback exact matching.
                self.fallback_entries.append(entry)
                self.skipped_long_entry_count += 1
                continue
            self.index[len(key)][key].append(entry)
            self.indexed_entry_count += 1
            max_seen = max(max_seen, len(key))
        self.max_ngram = min(self.max_ngram, max_seen or self.max_ngram)

    def source_key_sequences(self, source: str) -> List[List[str]]:
        base = term_tokens(source)
        if not base:
            return []
        singular = [singularize_token(t) for t in base]
        # Use canonical sequence first. Add base sequence only if it differs,
        # so glossary entries containing already-plural terms can still match.
        if singular == base:
            return [singular]
        return [singular, base]

    def relevant_entries(self, source: str) -> List[GlossaryEntry]:
        found: Dict[int, GlossaryEntry] = {}
        for tokens in self.source_key_sequences(source):
            limit = min(self.max_ngram, len(tokens))
            for n in range(1, limit + 1):
                table = self.index.get(n)
                if not table:
                    continue
                for start in range(0, len(tokens) - n + 1):
                    key = tuple(tokens[start:start + n])
                    matches = table.get(key)
                    if matches:
                        for entry in matches:
                            found[id(entry)] = entry
        # Only un-tokenizable or overly long terms use the slow fallback path.
        if self.fallback_entries:
            for entry in self.fallback_entries:
                if legacy_term_in_source(entry.source_term, source):
                    found[id(entry)] = entry
        # Prefer longer source terms first, so prompts and reports show the most
        # specific glossary hits before generic one-word terms.
        return sorted(found.values(), key=lambda e: len(canonical_term_key(e.source_term)), reverse=True)

    def stats(self) -> Dict[str, Any]:
        bucket_sizes = {str(n): sum(len(v) for v in bucket.values()) for n, bucket in self.index.items()}
        return {
            "engine": "hash_ngram",
            "entry_count": len(self.entries),
            "indexed_entry_count": self.indexed_entry_count,
            "fallback_entry_count": len(self.fallback_entries),
            "skipped_long_entry_count": self.skipped_long_entry_count,
            "max_ngram": self.max_ngram,
            "bucket_sizes_by_ngram_length": dict(sorted(bucket_sizes.items(), key=lambda kv: int(kv[0]))),
        }


def glossary_relevant_entries(seg: Segment, matcher: GlossaryHashMatcher) -> List[GlossaryEntry]:
    return matcher.relevant_entries(seg.source)


def check_glossary(seg: Segment, issues: List[Issue], matcher: GlossaryHashMatcher) -> None:
    for e in glossary_relevant_entries(seg, matcher):
        if e.forbidden_terms:
            bad = [t for t in e.forbidden_terms if t and t in seg.translation]
            if bad:
                sev = "major" if e.priority == "high" else "minor"
                add_issue(issues, seg, "forbidden_term", sev, f"Source term {e.source_term!r} uses forbidden target term(s): {bad}")
        if e.target_terms and not any(t in seg.translation for t in e.target_terms):
            if e.priority == "high":
                sev = "major"
            elif e.priority == "medium":
                sev = "minor"
            else:
                sev = "info"
            add_issue(
                issues,
                seg,
                "glossary_target_missing",
                sev,
                f"Source term {e.source_term!r} expected target term(s): {e.target_terms}",
            )


def deterministic_checks(
    root: ET.Element,
    segments: Sequence[Segment],
    glossary: Sequence[GlossaryEntry],
    target_language: str,
    matcher: Optional[GlossaryHashMatcher] = None,
    progress_interval: int = 0,
    script_detector: Optional[ScriptVariantDetector] = None,
) -> Tuple[List[Issue], Dict[str, Any]]:
    issues: List[Issue] = []
    matcher = matcher or GlossaryHashMatcher(glossary)
    script_detector = script_detector or ScriptVariantDetector(mode="none")
    meta: Dict[str, Any] = {
        "ts_version": root.attrib.get("version", ""),
        "ts_language": root.attrib.get("language", ""),
        "target_language_expected": target_language,
        "message_count": len(segments),
        "glossary_matcher": matcher.stats(),
        "script_variant_detector": script_detector.stats(),
        "format_guard_enabled": bool(FORMAT_GUARD_ENABLED),
    }

    if target_language and root.attrib.get("language") not in {target_language, "zh_TW", "zh-TW", "zh-Hant", "zh_Hant"}:
        dummy = Segment("file_metadata", 0, "@file", "", "", [""], "", False, [])
        add_issue(
            issues,
            dummy,
            "ts_language_mismatch",
            "major",
            f"TS@language is {root.attrib.get('language')!r}; expected {target_language!r} or zh-TW/zh-Hant.",
        )

    for pos, seg in enumerate(segments, start=1):
        if progress_interval and pos % progress_interval == 0:
            print(f"[deterministic] checked {pos}/{len(segments)} segments...", file=sys.stderr)
        tr_norm = normalize_text(seg.translation)
        src_norm = normalize_text(seg.source)

        if not tr_norm:
            add_issue(issues, seg, "empty_translation", "critical", "Translation is empty")
        if seg.translation_type == "unfinished":
            add_issue(issues, seg, "unfinished_translation", "major", "translation@type='unfinished'")
        if seg.translation_type == "missing_translation_element":
            add_issue(issues, seg, "missing_translation_element", "critical", "No <translation> element")

        if src_norm and tr_norm and src_norm == tr_norm and not COPY_OK.fullmatch(src_norm):
            add_issue(issues, seg, "possibly_untranslated", "major", "Source and translation are identical")

        if src_norm and tr_norm.startswith(src_norm + " -") or tr_norm.startswith(src_norm + " –") or tr_norm.startswith(src_norm + " —"):
            add_issue(issues, seg, "bilingual_residue", "major", "Translation appears to keep English source before a dash")

        # If a translation contains substantial English and Chinese, flag for review.
        latin_count = len(RE_LATIN.findall(seg.translation))
        cjk_count = len(RE_CJK.findall(seg.translation))
        if cjk_count > 0 and latin_count > max(12, cjk_count * 1.2) and not any(x in seg.source for x in ["SQL", "HTML", "API", "CRS"]):
            add_issue(issues, seg, "high_english_residue", "minor", f"Latin letters={latin_count}, CJK chars={cjk_count}")

        variant_hits = script_detector.detect(seg.translation)
        if variant_hits:
            # Candidate zh-TW script/locale risk from external references.
            # Keep severity minor and let MQM/Grok/human review confirm whether it is truly wrong in context.
            hit_chars = "".join(h.char for h in variant_hits[:20])
            hit_kinds = ", ".join(sorted({h.kind for h in variant_hits}))
            add_issue(
                issues,
                seg,
                "script_variant_risk",
                "minor",
                f"External script/locale detector flagged: {hit_chars}; evidence={hit_kinds}",
            )

        # Technical preservation checks.
        check_token_preservation(seg, issues, "qt_placeholder", qt_tokens)
        check_token_preservation(seg, issues, "brace_placeholder", brace_tokens)
        check_token_preservation(seg, issues, "printf_placeholder", printf_tokens)
        check_token_preservation(seg, issues, "html_xml_entity", entity_tokens)
        check_token_preservation(seg, issues, "html_xml_tag", html_tag_names)
        check_enhanced_structural_markers(seg, issues)

        for form_index, translation in structure_check_translations(seg):
            prefix = _form_detail_prefix(form_index)
            missing_numbers, extra_numbers = counter_diff(RE_NUMBER.findall(seg.source), RE_NUMBER.findall(translation))
            if missing_numbers:
                add_issue(issues, seg, "missing_number", "major", f"{prefix}Missing numeric token(s): {missing_numbers}")
            if extra_numbers:
                add_issue(issues, seg, "extra_number", "minor", f"{prefix}Extra numeric token(s): {extra_numbers}")

            if seg.source.count("\n") != translation.count("\n"):
                add_issue(issues, seg, "newline_count_mismatch", "minor", f"{prefix}source_newlines={seg.source.count(chr(10))}, target_newlines={translation.count(chr(10))}")

            src_acc = accelerator_tokens(seg.source)
            trg_acc = accelerator_tokens(translation)
            missing_acc, extra_acc = counter_diff(src_acc, trg_acc)
            if missing_acc or extra_acc:
                # This preserves the character after &, not only the ampersand count.
                add_issue(
                    issues,
                    seg,
                    "accelerator_count_mismatch",
                    "minor",
                    f"{prefix}source_accelerators={src_acc}, target_accelerators={trg_acc}, missing={missing_acc}, extra={extra_acc}",
                )

        check_glossary(seg, issues, matcher)

    return issues, meta

# -----------------------------
# Scoring and sampling
# -----------------------------
def source_char_count(seg: Segment) -> int:
    """Character denominator for MQM-style corpus aggregation.

    We use source characters because Qt .ts UI strings are short, token-heavy,
    and often do not have stable whitespace/word boundaries.  This makes the
    score much less sensitive to how messages are split than a plain segment
    average.
    """
    return max(1, len(seg.source or ""))


def dashboard_score_from_error_rate(error_rate_per_1000_source_chars: float, multiplier: float = MQM_EPK_TO_DASHBOARD_SCORE_MULTIPLIER) -> float:
    """Convert an error-rate metric to a 0-100 dashboard score.

    The primary research/QA metric remains error points per 1000 source chars
    (lower is better).  This score is only a human-friendly dashboard number.
    Keep the multiplier fixed when comparing runs.
    """
    return round(max(0.0, 100.0 - float(error_rate_per_1000_source_chars) * float(multiplier)), 2)


# -----------------------------
# Structure-only scoring
# -----------------------------
# These groups are intentionally separated from completion-state issues.
# unfinished / empty / missing translation are reported, but not counted in the
# structural score unless --structure-include-completion-state is explicitly set.
STRUCTURE_SCORE_GROUPS = {
    "qt_placeholder": {
        "missing_qt_placeholder",
        "extra_qt_placeholder",
    },
    "brace_placeholder": {
        "missing_brace_placeholder",
        "extra_brace_placeholder",
    },
    "printf_placeholder": {
        "missing_printf_placeholder",
        "extra_printf_placeholder",
    },
    "html_xml_entity": {
        "missing_html_xml_entity",
        "extra_html_xml_entity",
    },
    "html_xml_tag": {
        "missing_html_xml_tag",
        "extra_html_xml_tag",
    },
    "number": {
        "missing_number",
        "extra_number",
    },
    "newline": {
        "newline_count_mismatch",
    },
    "accelerator": {
        "accelerator_count_mismatch",
    },
}

COMPLETION_STATE_ISSUES = {
    "unfinished_translation",
    "empty_translation",
    "missing_translation_element",
}

CONTENT_RISK_ISSUES = {
    "possibly_untranslated",
    "bilingual_residue",
    "high_english_residue",
    "script_variant_risk",
    "glossary_target_missing",
    "forbidden_term",
    "ts_language_mismatch",
}

STRUCTURE_SCORE_GROUP_ORDER = [
    "qt_placeholder",
    "brace_placeholder",
    "printf_placeholder",
    "html_xml_entity",
    "html_xml_tag",
    "number",
    "newline",
    "accelerator",
]


def issue_group_for_structure_score(issue_type: str) -> str:
    for group_name, issue_types in STRUCTURE_SCORE_GROUPS.items():
        if issue_type in issue_types:
            return group_name
    if issue_type in COMPLETION_STATE_ISSUES:
        return "completion_state"
    if issue_type in CONTENT_RISK_ISSUES:
        return "content_or_terminology"
    if issue_type.startswith("missing_") or issue_type.startswith("extra_") or issue_type.endswith("_mismatch"):
        return "other_structure"
    return "other"


def structure_score_summary(
    segments: Sequence[Segment],
    issues: Sequence[Issue],
    limit: int = 10000,
    include_completion_state: bool = False,
) -> Dict[str, Any]:
    """Compute structure-only scores from the first N messages.

    Scoring rule for each structure item:
        item_score = 100 * (1 - affected_segment_count / checked_message_count)

    This intentionally counts affected segments rather than raw issue rows, so a
    single very broken segment does not dominate the score just because it has
    multiple issue rows. The total score is the sum of item scores, and the final
    average score is total / number_of_items.
    """
    try:
        limit = int(limit)
    except Exception:
        limit = 10000
    if limit <= 0:
        limit = 10000

    selected_segments = list(segments[: min(len(segments), limit)])
    selected_ids = {s.id for s in selected_segments}
    denominator = max(1, len(selected_segments))

    structure_issues = [i for i in issues if i.segment_id in selected_ids and issue_group_for_structure_score(i.issue_type) in STRUCTURE_SCORE_GROUPS]
    completion_issues = [i for i in issues if i.segment_id in selected_ids and i.issue_type in COMPLETION_STATE_ISSUES]
    content_or_term_issues = [i for i in issues if i.segment_id in selected_ids and issue_group_for_structure_score(i.issue_type) == "content_or_terminology"]
    other_structure_issues = [i for i in issues if i.segment_id in selected_ids and issue_group_for_structure_score(i.issue_type) == "other_structure"]

    counted_issues = list(structure_issues)
    if include_completion_state:
        counted_issues.extend(completion_issues)

    item_rows: List[Dict[str, Any]] = []
    item_scores: List[float] = []
    for group_name in STRUCTURE_SCORE_GROUP_ORDER:
        group_issue_types = STRUCTURE_SCORE_GROUPS[group_name]
        group_issues = [i for i in structure_issues if i.issue_type in group_issue_types]
        affected_segments = {i.segment_id for i in group_issues}
        issue_counts = Counter(i.issue_type for i in group_issues)
        severity_counts = Counter(i.severity for i in group_issues)
        score = round(max(0.0, 100.0 * (1.0 - len(affected_segments) / denominator)), 3)
        item_scores.append(score)
        item_rows.append({
            "item": group_name,
            "score_0_100": score,
            "affected_segment_count": len(affected_segments),
            "issue_count": len(group_issues),
            "issue_counts": dict(issue_counts),
            "severity_counts": dict(severity_counts),
        })

    # Optional counted completion item for workflows that deliberately want it.
    if include_completion_state:
        affected_segments = {i.segment_id for i in completion_issues}
        score = round(max(0.0, 100.0 * (1.0 - len(affected_segments) / denominator)), 3)
        item_scores.append(score)
        item_rows.append({
            "item": "completion_state",
            "score_0_100": score,
            "affected_segment_count": len(affected_segments),
            "issue_count": len(completion_issues),
            "issue_counts": dict(Counter(i.issue_type for i in completion_issues)),
            "severity_counts": dict(Counter(i.severity for i in completion_issues)),
        })

    total_score = round(sum(item_scores), 3)
    average_score = round(total_score / max(1, len(item_scores)), 3)

    return {
        "score_definition": "Each item score = 100 * (1 - affected segments / checked messages). Total is the sum of item scores; final average is total divided by item count.",
        "checked_messages": len(selected_segments),
        "limit": limit,
        "include_completion_state_in_score": bool(include_completion_state),
        "item_count": len(item_rows),
        "items": item_rows,
        "total_score_sum": total_score,
        "average_score_0_100": average_score,
        "counted_structure_issue_count": len(counted_issues),
        "counted_structure_issue_counts": dict(Counter(i.issue_type for i in counted_issues)),
        "structure_issue_count": len(structure_issues),
        "structure_issue_counts": dict(Counter(i.issue_type for i in structure_issues)),
        "completion_state_issue_count_not_scored": 0 if include_completion_state else len(completion_issues),
        "completion_state_issue_counts_not_scored": {} if include_completion_state else dict(Counter(i.issue_type for i in completion_issues)),
        "content_or_terminology_issue_count_not_scored": len(content_or_term_issues),
        "content_or_terminology_issue_counts_not_scored": dict(Counter(i.issue_type for i in content_or_term_issues)),
        "other_structure_issue_count_not_in_items": len(other_structure_issues),
        "other_structure_issue_counts_not_in_items": dict(Counter(i.issue_type for i in other_structure_issues)),
    }


def build_structure_failed_sentence_rows(
    ts_path: Path,
    segments: Sequence[Segment],
    issues: Sequence[Issue],
    limit: int = 10000,
    include_completion_state: bool = False,
    include_other_structure: bool = False,
) -> List[Dict[str, Any]]:
    """Return sentence-level rows explaining why the structure score is not 100.

    The default output mirrors structure_score_summary(): it only includes
    issues that are actually counted in the eight structure-score items, and it
    uses the same first-N message window.  Set include_other_structure=True to
    also include enhanced guard issues grouped as "other_structure".
    """
    try:
        limit = int(limit)
    except Exception:
        limit = 10000
    if limit <= 0:
        limit = 10000

    selected_segments = list(segments[: min(len(segments), limit)])
    seg_by_id = {s.id: s for s in selected_segments}
    grouped: Dict[Tuple[str, str], List[Issue]] = defaultdict(list)

    for issue in issues:
        seg = seg_by_id.get(issue.segment_id)
        if seg is None:
            continue
        group = issue_group_for_structure_score(issue.issue_type)
        if group in STRUCTURE_SCORE_GROUPS:
            grouped[(issue.segment_id, group)].append(issue)
        elif include_completion_state and group == "completion_state":
            grouped[(issue.segment_id, group)].append(issue)
        elif include_other_structure and group == "other_structure":
            grouped[(issue.segment_id, group)].append(issue)

    rows: List[Dict[str, Any]] = []
    for (segment_id, group), group_issues in sorted(grouped.items(), key=lambda kv: (seg_by_id[kv[0][0]].index, kv[0][1])):
        seg = seg_by_id[segment_id]
        issue_types = [i.issue_type for i in group_issues]
        severities = [i.severity for i in group_issues]
        details = [i.detail for i in group_issues]
        rows.append({
            "ts_file": str(ts_path),
            "index": seg.index,
            "segment_id": seg.id,
            "context": seg.context,
            "structure_item": group,
            "issue_count": len(group_issues),
            "issue_types": " | ".join(issue_types),
            "severities": " | ".join(severities),
            "details": " || ".join(details),
            "source": seg.source,
            "translation": seg.translation,
            "locations": "; ".join(seg.locations),
        })
    return rows


def structure_failed_rows_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_item = Counter(str(r.get("structure_item", "")) for r in rows)
    by_issue_type: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    for row in rows:
        for issue_type in str(row.get("issue_types", "")).split(" | "):
            if issue_type:
                by_issue_type[issue_type] += 1
        for severity in str(row.get("severities", "")).split(" | "):
            if severity:
                by_severity[severity] += 1
    return {
        "failed_sentence_rows": len(rows),
        "failed_segment_count": len({str(r.get("segment_id", "")) for r in rows if r.get("segment_id")}),
        "failed_rows_by_structure_item": dict(by_item),
        "failed_issue_counts": dict(by_issue_type),
        "failed_severity_counts": dict(by_severity),
    }


def write_structure_failed_sentences_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    fieldnames = [
        "ts_file", "index", "segment_id", "context", "structure_item", "issue_count",
        "issue_types", "severities", "details", "source", "translation", "locations",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})



def deterministic_summary(segments: Sequence[Segment], issues: Sequence[Issue], glossary: Sequence[GlossaryEntry], meta: Dict[str, Any], matcher: Optional[GlossaryHashMatcher] = None, structure_score_limit: int = 10000, structure_include_completion_state: bool = False) -> Dict[str, Any]:
    matcher = matcher or GlossaryHashMatcher(glossary)
    issue_counts = Counter(i.issue_type for i in issues)
    sev_counts = Counter(i.severity for i in issues)
    total_penalty = sum(issue_penalty(i) for i in issues)
    total_source_chars = sum(source_char_count(s) for s in segments)
    deterministic_epk = (total_penalty / max(1, total_source_chars)) * 1000.0
    legacy_segment_avg_score = max(0.0, 100.0 - total_penalty / max(1, len(segments)) * 10.0)
    char_weighted_score = dashboard_score_from_error_rate(deterministic_epk)
    affected_segment_count = len({i.segment_id for i in issues if i.segment_id})
    blocking_issue_types = {
        "empty_translation",
        "missing_translation_element",
        "missing_qt_placeholder",
        "missing_brace_placeholder",
        "missing_printf_placeholder",
        "missing_html_xml_entity",
        "missing_html_xml_tag",
    }
    blocking_issue_count = sum(1 for i in issues if i.severity == "critical" or i.issue_type in blocking_issue_types)

    # Preservation-rate metrics.
    def rate_for(issue_prefix: str) -> float:
        affected = len({i.segment_id for i in issues if i.issue_type.startswith(issue_prefix)})
        return round(1.0 - affected / max(1, len(segments)), 6)

    glossary_source_hits = 0
    glossary_ok = 0
    for seg in segments:
        rel = glossary_relevant_entries(seg, matcher)
        glossary_source_hits += len(rel)
        for e in rel:
            if e.target_terms and any(t in seg.translation for t in e.target_terms):
                glossary_ok += 1
    terminology_accuracy = round(glossary_ok / glossary_source_hits, 6) if glossary_source_hits else None

    structure_scores = structure_score_summary(
        segments,
        issues,
        limit=structure_score_limit,
        include_completion_state=structure_include_completion_state,
    )

    return {
        "metadata": meta,
        "structure_scores": structure_scores,
        "structure_average_score_0_100": structure_scores.get("average_score_0_100"),
        "structure_total_score_sum": structure_scores.get("total_score_sum"),
        "structure_score_item_count": structure_scores.get("item_count"),
        "deterministic_score_0_100": char_weighted_score,
        "legacy_deterministic_score_0_100": round(legacy_segment_avg_score, 2),
        "deterministic_error_points_per_1000_source_chars": round(deterministic_epk, 3),
        "deterministic_total_weighted_error_points": round(total_penalty, 3),
        "message_count": len(segments),
        "total_source_chars": total_source_chars,
        "issue_count": len(issues),
        "affected_segment_count": affected_segment_count,
        "affected_segment_rate": round(affected_segment_count / max(1, len(segments)), 6),
        "blocking_issue_count": blocking_issue_count,
        "issue_counts": dict(issue_counts),
        "severity_counts": dict(sev_counts),
        "placeholder_preservation_rate": rate_for("missing_qt_placeholder"),
        "brace_placeholder_preservation_rate": rate_for("missing_brace_placeholder"),
        "tag_preservation_rate": rate_for("missing_html_xml_tag"),
        "entity_preservation_rate": rate_for("missing_html_xml_entity"),
        "terminology_accuracy": terminology_accuracy,
        "glossary_entry_count": len(glossary),
        "glossary_matcher": matcher.stats(),
        "score_note": "deterministic_score_0_100 is derived from deterministic_error_points_per_1000_source_chars; use it as a dashboard score, not a formal MQM score.",
    }


def select_segments_for_llm(
    segments: Sequence[Segment],
    issues: Sequence[Issue],
    sample_size: int,
    judge_all: bool,
    seed: int,
    sampling_mode: str = "random",
) -> List[Segment]:
    """Select segments for MQM judging.

    sampling_mode:
    - random: fixed-seed random sample. This is the default for paper-grade
      estimation because it avoids over-representing deterministic issues.
    - issue_enriched: prioritizes deterministic issues, useful for diagnostic
      error analysis but not for estimating corpus-level quality.
    - mixed: half issue-enriched and half random.
    """
    if judge_all:
        return list(segments)

    sample_size = max(0, int(sample_size))
    rng = random.Random(seed)
    by_id = {s.id: s for s in segments}
    segment_ids = [s.id for s in segments]

    if sampling_mode == "random":
        chosen = segment_ids[:]
        rng.shuffle(chosen)
        return [by_id[sid] for sid in chosen[:sample_size]]

    severity_rank = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    issue_sorted = sorted(issues, key=lambda i: (severity_rank.get(i.severity, 9), i.index))

    def issue_first(limit: int) -> List[str]:
        selected: List[str] = []
        for i in issue_sorted:
            if i.segment_id in by_id and i.segment_id not in selected:
                selected.append(i.segment_id)
            if len(selected) >= limit:
                break
        return selected

    if sampling_mode == "mixed":
        issue_quota = sample_size // 2
        selected_ids = issue_first(issue_quota)
        remaining = [sid for sid in segment_ids if sid not in selected_ids]
        rng.shuffle(remaining)
        selected_ids.extend(remaining[: max(0, sample_size - len(selected_ids))])
        return [by_id[sid] for sid in selected_ids[:sample_size] if sid in by_id]

    # Backward-compatible default for unknown/legacy mode: issue_enriched.
    selected_ids = issue_first(sample_size)
    remaining = [sid for sid in segment_ids if sid not in selected_ids]
    rng.shuffle(remaining)
    selected_ids.extend(remaining[: max(0, sample_size - len(selected_ids))])
    return [by_id[sid] for sid in selected_ids[:sample_size] if sid in by_id]


def effective_sample_window(sample_window: int) -> int:
    """Return the enforced first-N sampling window for MQM.

    MQM sampling is capped at the first MAX_SAMPLE_WINDOW parsed segments even
    if --sample-window is 0, negative, or larger than MAX_SAMPLE_WINDOW.
    Deterministic checks still scan the whole .ts file.
    """
    try:
        requested = int(sample_window)
    except Exception:
        requested = MAX_SAMPLE_WINDOW
    if requested <= 0:
        return MAX_SAMPLE_WINDOW
    return min(requested, MAX_SAMPLE_WINDOW)


def sample_pool_from_first_n(segments: Sequence[Segment], sample_window: int) -> List[Segment]:
    """Return the first-N parsed segments as the MQM sampling frame.

    This function enforces the paper/reproducibility rule that Grok/MQM samples
    only from the first MAX_SAMPLE_WINDOW entries.  Deterministic checks still
    scan every segment in the .ts file.
    """
    window = effective_sample_window(sample_window)
    return list(segments[: min(len(segments), window)])


def build_repeated_mqm_plan(
    segments: Sequence[Segment],
    issues: Sequence[Issue],
    issues_by_segment: Dict[str, List[Issue]],
    matcher: GlossaryHashMatcher,
    style_guide_summary: str,
    model: str,
    sample_size: int,
    repeats: int,
    seed: int,
    sample_window: int,
    sampling_mode: str,
    judge_all: bool = False,
) -> Tuple[List[Segment], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build repeated-sampling MQM request rows and a manifest.

    Returns:
    - sampling_pool: the first-N segment pool actually used
    - run_manifests: one record per repeated draw
    - request_rows: flattened request list, with unique request_key values
    """
    sampling_pool = sample_pool_from_first_n(segments, sample_window)
    pool_ids = {s.id for s in sampling_pool}
    pool_issues = [i for i in issues if i.segment_id in pool_ids]

    repeats = 1 if judge_all else max(1, int(repeats))
    sample_size = len(sampling_pool) if judge_all else min(max(0, int(sample_size)), len(sampling_pool))

    run_manifests: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    for run_no in range(1, repeats + 1):
        run_id = f"run_{run_no:02d}"
        run_seed = int(seed) + run_no - 1
        selected = select_segments_for_llm(
            sampling_pool,
            pool_issues,
            sample_size,
            judge_all=judge_all,
            seed=run_seed,
            sampling_mode=sampling_mode,
        )
        run_rows = request_rows_for_segments(
            selected,
            issues_by_segment,
            matcher,
            style_guide_summary,
            model,
            run_id=run_id,
        )
        all_rows.extend(run_rows)
        run_manifests.append({
            "run_id": run_id,
            "run_no": run_no,
            "seed": run_seed,
            "sampling_mode": sampling_mode,
            "sample_window": len(sampling_pool),
            "requested_sample_size": int(sample_size),
            "selected_segment_count": len(selected),
            "selected_segment_ids": [s.id for s in selected],
        })
    return sampling_pool, run_manifests, all_rows


def write_selected_mqm_runs_csv(path: Path, run_manifests: Sequence[Dict[str, Any]], seg_by_id: Dict[str, Segment]) -> None:
    fieldnames = [
        "run_id", "run_no", "seed", "sample_position", "segment_id", "index", "context",
        "source", "translation", "translation_type", "numerus", "locations", "comment", "extracomment",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for run in run_manifests:
            for pos, sid in enumerate(run.get("selected_segment_ids", []), start=1):
                s = seg_by_id.get(sid)
                if not s:
                    continue
                writer.writerow({
                    "run_id": run.get("run_id", ""),
                    "run_no": run.get("run_no", ""),
                    "seed": run.get("seed", ""),
                    "sample_position": pos,
                    "segment_id": s.id,
                    "index": s.index,
                    "context": s.context,
                    "source": s.source,
                    "translation": s.translation,
                    "translation_type": s.translation_type,
                    "numerus": s.numerus,
                    "locations": "; ".join(s.locations),
                    "comment": s.comment,
                    "extracomment": s.extracomment,
                })


# -----------------------------
# Grok MQM prompt and schema
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
                        "category": {
                            "type": "string",
                            "enum": ["Accuracy", "Terminology", "Fluency", "StyleLocale", "Format", "UIUsability", "SourceIssue"],
                        },
                        "subcategory": {
                            "type": "string",
                            "enum": [
                                "Mistranslation", "Omission", "Addition", "WrongTerm", "InconsistentTerm",
                                "Grammar", "Unnatural", "Punctuation", "LocaleConvention", "Register",
                                "Placeholder", "Tag", "Entity", "Number", "Newline", "Mnemonic",
                                "TooLong", "AmbiguousUI", "SourceAmbiguity", "Other",
                            ],
                        },
                        "severity": {"type": "string", "enum": ["Neutral", "Minor", "Major", "Critical"]},
                        "penalty": {"type": "number"},
                        "source_span": {"type": "string"},
                        "target_span": {"type": "string"},
                        "explanation_zh_tw": {"type": "string"},
                        "suggested_correction": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "category", "subcategory", "severity", "penalty", "source_span", "target_span",
                        "explanation_zh_tw", "suggested_correction", "confidence",
                    ],
                },
            },
            "weighted_error_points": {"type": "number"},
            "mqm_score_0_100": {"type": "number"},
            "improved_translation": {"type": "string"},
            "summary_zh_tw": {"type": "string"},
        },
        "required": ["segment_id", "acceptability", "errors", "weighted_error_points", "mqm_score_0_100", "improved_translation", "summary_zh_tw"],
    }


def build_prompt(seg: Segment, relevant_terms: Sequence[GlossaryEntry], segment_issues: Sequence[Issue], style_guide_summary: str) -> str:
    glossary_lines = []
    for e in relevant_terms[:30]:
        forbidden = f"；禁用：{'、'.join(e.forbidden_terms)}" if e.forbidden_terms else ""
        note = f"；備註：{e.note}" if e.note else ""
        glossary_lines.append(f"- {e.source_term} => {' / '.join(e.target_terms)}；priority={e.priority}{forbidden}{note}")
    if not glossary_lines:
        glossary_lines.append("- 本句未命中特定詞庫術語；仍需依 QGIS/GIS/軟體在地化常識評估。")

    precheck_lines = []
    for i in segment_issues[:20]:
        precheck_lines.append(f"- [{i.severity}] {i.issue_type}: {i.detail}")
    if not precheck_lines:
        precheck_lines.append("- 無 deterministic pre-check 問題。")

    locations = "; ".join(seg.locations) if seg.locations else ""
    return f"""
你是繁體中文（台灣）軟體在地化與 GIS/QGIS 翻譯審查員。
請依據 MQM-Core 與本研究的 QGIS software-localization extension 評估此翻譯。

重要原則：
1. 只標註實際錯誤，不要因個人偏好扣分。
2. 若譯文可接受但你有更好說法，最多標 Neutral 或不標錯。
3. 若 placeholder、HTML/XML tag、entity、數字、快捷鍵、換行被破壞，依影響標 Major 或 Critical。
4. 若專案詞庫要求特定譯名，詞庫優先於一般偏好。
5. 若 source 本身是保留字、API、SQL、檔名、副檔名、快捷鍵、公式、單位，通常應保留。
6. 使用台灣繁體中文判斷自然度與在地化；避免簡體字與中國大陸慣用語。

Severity 與 penalty：
- Neutral = 0：不影響理解或只是偏好。
- Minor = 1：小錯，基本不影響使用。
- Major = 5：明顯錯誤，會誤導或降低可用性。
- Critical = 25：相反意思、嚴重漏譯、破壞格式造成軟體錯誤、重大安全/資料風險。

Style guide summary:
{style_guide_summary}

專案詞庫命中：
{chr(10).join(glossary_lines)}

Deterministic pre-check（僅為候選問題；不可直接採信，必須依 source/target 自行確認是否真的有錯）：
{chr(10).join(precheck_lines)}

Segment metadata:
- segment_id: {seg.id}
- index: {seg.index}
- context: {seg.context}
- locations: {locations}
- comment: {seg.comment}
- extracomment: {seg.extracomment}
- numerus: {seg.numerus}

Source English:
{seg.source}

Target zh-TW translation:
{seg.translation}

請輸出 JSON，欄位必須符合 schema。
weighted_error_points 請等於所有 errors.penalty 的總和。
mqm_score_0_100 請用 max(0, 100 - weighted_error_points * 4) 粗略轉換，僅作 dashboard 分數。
""".strip()


def request_rows_for_segments(
    segments: Sequence[Segment],
    issues_by_segment: Dict[str, List[Issue]],
    matcher: GlossaryHashMatcher,
    style_guide_summary: str,
    model: str,
    run_id: str = "",
) -> List[Dict[str, Any]]:
    """Build Grok request rows.

    Each row has a stable segment_id and, when repeated sampling is enabled,
    a unique request_key of the form run_XX:NNN:<segment_id>.  The unique key
    prevents duplicate segment IDs across repeated samples from colliding in
    Batch API outputs.

    Grok/xAI uses OpenAI-compatible structured output.  The row stores the
    prompt, schema, model, and unique request key; call_grok_row() or the xAI
    Batch API helpers convert it to /v1/chat/completions payloads.
    """
    rows: List[Dict[str, Any]] = []
    schema = mqm_response_schema()
    for sample_position, seg in enumerate(segments, start=1):
        rel = glossary_relevant_entries(seg, matcher)
        prompt = build_prompt(seg, rel, issues_by_segment.get(seg.id, []), style_guide_summary)
        request_key = f"{run_id}:{sample_position:03d}:{seg.id}" if run_id else seg.id
        rows.append(
            {
                "request_key": request_key,
                "run_id": run_id,
                "sample_position": sample_position,
                "segment_id": seg.id,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "contents": prompt,
                "schema": schema,
                "generation_config": {
                    "temperature": 0,
                    "response_mime_type": "application/json",
                },
            }
        )
    return rows



def _strict_json_schema_for_xai(obj: Any) -> Any:
    """Return a JSON Schema variant suited for xAI structured outputs.

    xAI supports the OpenAI-style response_format={type: json_schema, ...}.
    Adding additionalProperties=False to object nodes makes the expected shape
    explicit and helps the model avoid extra fields.
    """
    if isinstance(obj, dict):
        out: Dict[str, Any] = {k: _strict_json_schema_for_xai(v) for k, v in obj.items()}
        if out.get("type") == "object" or "properties" in out:
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(obj, list):
        return [_strict_json_schema_for_xai(x) for x in obj]
    return obj


def _effective_grok_api_key(explicit_key: Optional[str] = None) -> str:
    candidates = [
        explicit_key,
        globals().get("XAI_API_KEY", ""),
        globals().get("GROK_API_KEY", ""),
        os.environ.get("XAI_API_KEY", ""),
        os.environ.get("GROK_API_KEY", ""),
    ]
    for key in candidates:
        key = (key or "").strip()
        if key and key not in {"REPLACE_WITH_YOUR_XAI_API_KEY", "REPLACE_WITH_YOUR_GROK_API_KEY"}:
            return key
    return ""


def _xai_base_url() -> str:
    return (os.environ.get("XAI_API_BASE_URL") or globals().get("XAI_API_BASE_URL", "https://api.x.ai/v1")).rstrip("/")


def _xai_json_request(
    method: str,
    path: str,
    api_key: str,
    body: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    import urllib.error
    import urllib.parse
    import urllib.request

    url = _xai_base_url() + path
    if query:
        clean_query = {k: v for k, v in query.items() if v is not None and v != ""}
        if clean_query:
            url += "?" + urllib.parse.urlencode(clean_query)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:  # pragma: no cover - requires live API
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"xAI API HTTP {e.code} for {method.upper()} {path}: {detail}") from e
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw_text": raw}


def _xai_upload_file(path: Path, api_key: str, timeout_seconds: int = 300) -> Dict[str, Any]:
    """Upload a JSONL file to xAI's Files API using only the standard library."""
    import mimetypes
    import urllib.error
    import urllib.request
    import uuid

    boundary = f"----xai-boundary-{uuid.uuid4().hex}"
    filename = path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/jsonl"
    file_bytes = path.read_bytes()
    body = b"".join([
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ])
    req = urllib.request.Request(
        _xai_base_url() + "/files",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:  # pragma: no cover - requires live API
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"xAI file upload failed with HTTP {e.code}: {detail}") from e
    return json.loads(raw) if raw.strip() else {}


def _grok_response_format(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "mqm_translation_judgement",
            "schema": _strict_json_schema_for_xai(schema),
            "strict": True,
        },
    }


def _grok_chat_body_from_row(row: Dict[str, Any], model: str) -> Dict[str, Any]:
    schema = row.get("schema") or mqm_response_schema()
    config = row.get("generation_config", {}) or {}
    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict MQM translation evaluator. Return only JSON matching the provided schema.",
            },
            {"role": "user", "content": row["contents"]},
        ],
        "temperature": float(config.get("temperature", 0)),
        "response_format": _grok_response_format(schema),
    }
    max_tokens = config.get("max_tokens") or config.get("max_output_tokens")
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    return body


def _extract_text_from_xai_chat_response_obj(resp: Any) -> str:
    """Extract assistant text from xAI/OpenAI-style chat, responses, or batch result objects."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if not isinstance(resp, dict):
        try:
            resp = resp.model_dump()  # pydantic-like object
        except Exception:
            return str(resp)

    # xAI Batch API wraps successful chat completion responses here.
    if isinstance(resp.get("chat_get_completion"), dict):
        return _extract_text_from_xai_chat_response_obj(resp["chat_get_completion"])

    # OpenAI/xAI Chat Completions response.
    choices = resp.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        parts.append(str(part.get("text") or part.get("content") or ""))
                    else:
                        parts.append(str(part))
                return "".join(parts)
        if isinstance(choices[0].get("text"), str):
            return choices[0]["text"]

    # Responses API style.
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    output = resp.get("output") or []
    for item in output:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                if isinstance(content.get("text"), str):
                    return content["text"]
                if isinstance(content.get("output_text"), str):
                    return content["output_text"]

    # OpenAI Batch JSONL style can wrap body under response.body.
    if isinstance(resp.get("body"), dict):
        return _extract_text_from_xai_chat_response_obj(resp["body"])
    if isinstance(resp.get("response"), dict):
        return _extract_text_from_xai_chat_response_obj(resp["response"])
    return ""


def _parse_json_object_text(text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
        raise ValueError("JSON root is not an object")
    except Exception:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(cleaned[start:end + 1])
            if isinstance(payload, dict):
                return payload
        raise


def _result_from_text(
    segment_id: str,
    text: str,
    model: str,
    request_key: str = "",
    run_id: str = "",
    batch_api: bool = False,
) -> Dict[str, Any]:
    try:
        payload = _parse_json_object_text(text)
        payload["segment_id"] = payload.get("segment_id") or segment_id
        payload["_raw_text"] = text
        payload["_model"] = model
        payload["_provider"] = "xai_grok"
        payload["_prompt_version"] = payload.get("_prompt_version", PROMPT_VERSION)
        payload["_batch_api"] = bool(batch_api)
        if request_key:
            payload["_request_key"] = request_key
        if run_id:
            payload["_run_id"] = run_id
        return payload
    except Exception as e:
        return {
            "segment_id": segment_id,
            "_model": model,
            "_provider": "xai_grok",
            "_prompt_version": PROMPT_VERSION,
            "_batch_api": bool(batch_api),
            "_request_key": request_key,
            "_run_id": run_id,
            "_error": f"Could not parse Grok response JSON: {e}",
            "_raw_text": text,
            "errors": [],
            "weighted_error_points": None,
            "mqm_score_0_100": None,
            "acceptability": "Reject",
            "improved_translation": "",
            "summary_zh_tw": "Grok response parse failed.",
        }


def _error_result(segment_id: str, model: str, message: str, request_key: str = "", run_id: str = "") -> Dict[str, Any]:
    return {
        "segment_id": segment_id,
        "_model": model,
        "_provider": "xai_grok",
        "_prompt_version": PROMPT_VERSION,
        "_batch_api": True,
        "_request_key": request_key,
        "_run_id": run_id,
        "_error": message,
        "errors": [],
        "weighted_error_points": None,
        "mqm_score_0_100": None,
        "acceptability": "Reject",
        "improved_translation": "",
        "summary_zh_tw": "Grok request failed.",
    }


def call_grok_row(
    row: Dict[str, Any],
    model: str,
    retries: int = 3,
    sleep_seconds: float = 1.0,
    thinking_level: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Call xAI/Grok synchronously through the OpenAI-compatible Chat Completions endpoint."""
    del thinking_level  # Kept for CLI/config compatibility with the previous Grok version.
    key = _effective_grok_api_key(api_key)
    if not key:
        raise RuntimeError(
            "xAI/Grok API key is required. Pass --api-key, set XAI_API_KEY / GROK_API_KEY, "
            "or set XAI_API_KEY / GROK_API_KEY or pass --api-key."
        )

    body = _grok_chat_body_from_row(row, model)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = _xai_json_request("POST", "/chat/completions", key, body=body, timeout_seconds=240)
            text = _extract_text_from_xai_chat_response_obj(response)
            if not text:
                raise RuntimeError(f"Grok returned no assistant content: {response}")
            return _result_from_text(
                str(row.get("segment_id", "")),
                text,
                model,
                request_key=str(row.get("request_key") or row.get("segment_id") or ""),
                run_id=str(row.get("run_id") or ""),
                batch_api=False,
            )
        except Exception as e:  # pragma: no cover - requires live API
            last_error = e
            if attempt < retries:
                time.sleep(sleep_seconds * attempt)

    return _error_result(
        str(row.get("segment_id", "")),
        model,
        str(last_error),
        request_key=str(row.get("request_key") or row.get("segment_id") or ""),
        run_id=str(row.get("run_id") or ""),
    )


# -----------------------------
# Grok Batch API helpers
# -----------------------------
def _grok_batch_request_from_row(row: Dict[str, Any], model: str) -> Dict[str, Any]:
    return {
        "batch_request_id": str(row.get("request_key") or row.get("segment_id")),
        "batch_request": {
            "chat_get_completion": _grok_chat_body_from_row(row, model),
        },
    }


def write_grok_batch_input_jsonl(path: Path, request_rows: Sequence[Dict[str, Any]], model: str) -> None:
    """Write xAI Batch JSONL input compatible with /v1/chat/completions."""
    with path.open("w", encoding="utf-8") as f:
        for row in request_rows:
            f.write(json.dumps({
                "custom_id": str(row.get("request_key") or row.get("segment_id")),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": _grok_chat_body_from_row(row, model),
            }, ensure_ascii=False) + "\n")


def _grok_batch_id(batch_obj: Dict[str, Any]) -> str:
    for key in ("batch_id", "id", "name"):
        val = str(batch_obj.get(key) or "").strip()
        if val:
            return val.split("/")[-1]
    return ""


def _grok_batch_state_done(batch_obj: Dict[str, Any]) -> bool:
    state = batch_obj.get("state") or {}
    if isinstance(state, dict):
        if state.get("num_requests", 0) and int(state.get("num_pending", 0) or 0) == 0:
            return True
    status = str(batch_obj.get("status") or batch_obj.get("state") or "").lower()
    return status in {"completed", "succeeded", "failed", "cancelled", "expired"}


def _grok_batch_state_name(batch_obj: Dict[str, Any]) -> str:
    state = batch_obj.get("state")
    if isinstance(state, dict):
        return json.dumps(state, ensure_ascii=False)
    return str(batch_obj.get("status") or state or "")


def _request_row_map(request_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "position": i,
            "request_key": str(row.get("request_key") or row.get("segment_id") or f"row_{i}"),
            "segment_id": str(row.get("segment_id") or ""),
            "run_id": str(row.get("run_id") or ""),
            "sample_position": row.get("sample_position", ""),
        }
        for i, row in enumerate(request_rows)
    ]


def create_grok_batch_job(
    request_rows: Sequence[Dict[str, Any]],
    model: str,
    outdir: Path,
    api_key: Optional[str] = None,
    mode: str = "batch-inline",
    display_name: str = "qgis-ts-mqm-grok-random500",
    config_variant: str = "",
) -> Dict[str, Any]:
    """Submit an xAI/Grok Batch API job and write a local job manifest."""
    del config_variant  # Kept for CLI/config compatibility with the previous Grok version.
    key = _effective_grok_api_key(api_key)
    if not key:
        raise RuntimeError(
            "xAI/Grok API key is required. Pass --api-key, set XAI_API_KEY / GROK_API_KEY, "
            "or set XAI_API_KEY / GROK_API_KEY or pass --api-key."
        )

    outdir.mkdir(parents=True, exist_ok=True)
    key_map = _request_row_map(request_rows)
    write_json(outdir / "grok_batch_key_map.json", key_map)

    if mode == "batch-file":
        input_path = outdir / "grok_batch_requests.jsonl"
        write_grok_batch_input_jsonl(input_path, request_rows, model)
        uploaded = _xai_upload_file(input_path, key)
        input_file_id = uploaded.get("id") or uploaded.get("file_id") or uploaded.get("name")
        if not input_file_id:
            raise RuntimeError(f"Could not find uploaded file id in xAI response: {uploaded}")
        batch_obj = _xai_json_request("POST", "/batches", key, body={"name": display_name, "input_file_id": input_file_id}, timeout_seconds=120)
        batch_id = _grok_batch_id(batch_obj)
        manifest = {
            "mode": mode,
            "model": model,
            "display_name": display_name,
            "batch_id": batch_id,
            "job_name": batch_id,
            "state": _grok_batch_state_name(batch_obj),
            "request_count": len(request_rows),
            "input_jsonl": str(input_path),
            "uploaded_file_id": input_file_id,
            "upload_response": uploaded,
            "batch_response": batch_obj,
        }
    else:
        batch_obj = _xai_json_request("POST", "/batches", key, body={"name": display_name}, timeout_seconds=120)
        batch_id = _grok_batch_id(batch_obj)
        if not batch_id:
            raise RuntimeError(f"Could not find batch_id in xAI response: {batch_obj}")
        chunk_size = 100
        submitted = 0
        for start in range(0, len(request_rows), chunk_size):
            chunk = request_rows[start:start + chunk_size]
            payload = {"batch_requests": [_grok_batch_request_from_row(row, model) for row in chunk]}
            _xai_json_request("POST", f"/batches/{batch_id}/requests", key, body=payload, timeout_seconds=300)
            submitted += len(chunk)
        manifest = {
            "mode": "batch-inline",
            "model": model,
            "display_name": display_name,
            "batch_id": batch_id,
            "job_name": batch_id,
            "state": _grok_batch_state_name(batch_obj),
            "request_count": len(request_rows),
            "submitted_request_count": submitted,
            "batch_response": batch_obj,
            "batch_add_chunk_size": chunk_size,
        }

    write_json(outdir / "grok_batch_job.json", manifest)
    return manifest


def _segment_id_from_request_key(request_key: str) -> str:
    if ":" in request_key:
        return request_key.split(":", 2)[-1]
    return request_key


def collect_grok_batch_results(
    job_name: str,
    outdir: Path,
    model: str,
    api_key: Optional[str] = None,
    wait: bool = False,
    poll_seconds: int = 30,
    timeout_seconds: int = 86400,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Collect xAI/Grok Batch API results if the batch has completed."""
    key = _effective_grok_api_key(api_key)
    if not key:
        raise RuntimeError("xAI/Grok API key is required to collect a batch job.")

    batch_id = str(job_name or "").strip().split("/")[-1]
    if not batch_id:
        raise RuntimeError("A Grok/xAI batch id is required.")

    key_map_path = outdir / "grok_batch_key_map.json"
    if not key_map_path.exists():
        legacy_path = outdir / "gemini_batch_key_map.json"
        key_map_path = legacy_path if legacy_path.exists() else key_map_path
    key_map: List[Dict[str, Any]] = []
    if key_map_path.exists():
        key_map = json.loads(key_map_path.read_text(encoding="utf-8"))
    key_to_info = {str(x.get("request_key") or x.get("segment_id") or x.get("position")): x for x in key_map}

    start = time.time()
    batch_obj = _xai_json_request("GET", f"/batches/{batch_id}", key, timeout_seconds=120)
    while wait and not _grok_batch_state_done(batch_obj):
        if time.time() - start > timeout_seconds:
            break
        print(f"[batch] current state: {_grok_batch_state_name(batch_obj)}; polling again in {poll_seconds}s", file=sys.stderr)
        time.sleep(max(1, int(poll_seconds)))
        batch_obj = _xai_json_request("GET", f"/batches/{batch_id}", key, timeout_seconds=120)

    manifest = {
        "batch_id": batch_id,
        "job_name": batch_id,
        "state": _grok_batch_state_name(batch_obj),
        "model": model,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "batch_response": batch_obj,
    }

    results: List[Dict[str, Any]] = []
    if not _grok_batch_state_done(batch_obj):
        manifest["error"] = "Batch job has not completed yet. Use --batch-wait or collect later."
        write_json(outdir / "grok_batch_job_status.json", manifest)
        return results, manifest

    raw_pages: List[Dict[str, Any]] = []
    pagination_token: Optional[str] = None
    while True:
        page = _xai_json_request(
            "GET",
            f"/batches/{batch_id}/results",
            key,
            query={"limit": 1000, "pagination_token": pagination_token},
            timeout_seconds=300,
        )
        raw_pages.append(page)
        for item in page.get("results", []) or []:
            request_key = str(item.get("batch_request_id") or item.get("custom_id") or item.get("id") or "")
            info = key_to_info.get(request_key, {})
            seg_id = str(info.get("segment_id") or _segment_id_from_request_key(request_key))
            run_id = str(info.get("run_id") or (request_key.split(":", 1)[0] if request_key.startswith("run_") else ""))
            error_message = item.get("error_message") or item.get("error") or item.get("batch_result", {}).get("error")
            response_obj = (item.get("batch_result") or {}).get("response") or item.get("response") or item
            text = _extract_text_from_xai_chat_response_obj(response_obj)
            if text:
                results.append(_result_from_text(seg_id, text, model, request_key=request_key, run_id=run_id, batch_api=True))
            else:
                results.append(_error_result(seg_id, model, str(error_message or f"No response content in batch item: {item}"), request_key=request_key, run_id=run_id))
        pagination_token = page.get("pagination_token") or page.get("next_page_token")
        if not pagination_token:
            break

    write_json(outdir / "grok_batch_results_raw.json", raw_pages)
    write_json(outdir / "grok_batch_job_status.json", manifest)
    return results, manifest


# -----------------------------
# Output reports
# -----------------------------
def write_issues_csv(path: Path, issues: Sequence[Issue]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(issues[0]).keys()) if issues else [
            "segment_id", "index", "context", "issue_type", "severity", "detail", "source", "translation", "locations"
        ])
        writer.writeheader()
        for i in issues:
            writer.writerow(asdict(i))


def write_segment_csv(path: Path, segments: Sequence[Segment]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["segment_id", "index", "context", "source", "translation", "translation_type", "numerus", "locations", "comment", "extracomment"])
        writer.writeheader()
        for s in segments:
            writer.writerow({
                "segment_id": s.id,
                "index": s.index,
                "context": s.context,
                "source": s.source,
                "translation": s.translation,
                "translation_type": s.translation_type,
                "numerus": s.numerus,
                "locations": "; ".join(s.locations),
                "comment": s.comment,
                "extracomment": s.extracomment,
            })


def normalize_mqm_result(result: Dict[str, Any], seg_by_id: Optional[Dict[str, Segment]] = None) -> Dict[str, Any]:
    """Canonicalize one LLM/MQM result before scoring.

    The LLM is allowed to judge severity, but the program—not the model—owns the
    penalty mapping.  This prevents inconsistent outputs such as Major+1 point
    from affecting the final score.
    """
    out = dict(result)
    if out.get("_error"):
        return out

    warnings = list(out.get("_normalization_warnings", []) or [])
    raw_errors = out.get("errors", [])
    if raw_errors is None:
        raw_errors = []
    if not isinstance(raw_errors, list):
        warnings.append("errors was not a list; ignored for canonical penalty calculation.")
        raw_errors = []

    normalized_errors: List[Dict[str, Any]] = []
    total = 0.0
    for err in raw_errors:
        if not isinstance(err, dict):
            warnings.append(f"Ignored non-object error item: {err!r}")
            continue
        e = dict(err)
        severity = str(e.get("severity", "Neutral"))
        if severity not in SEVERITY_PENALTY:
            warnings.append(f"Unknown severity {severity!r}; treated as Neutral.")
            severity = "Neutral"
        penalty = float(SEVERITY_PENALTY[severity])
        e["severity"] = severity
        e["penalty"] = penalty
        total += penalty
        normalized_errors.append(e)

    if str(out.get("acceptability", "")).lower() in {"reject", "major revision"} and not normalized_errors:
        warnings.append("Model marked the segment as Reject/Major Revision but returned no errors; canonical score remains 100 for this segment.")

    out["errors"] = normalized_errors
    out["weighted_error_points"] = round(total, 3)
    out["mqm_score_0_100"] = round(max(0.0, 100.0 - total * MQM_DASHBOARD_POINTS_PER_ERROR_POINT), 2)
    out["_normalized_penalties"] = True
    if warnings:
        out["_normalization_warnings"] = warnings

    if seg_by_id is not None:
        seg = seg_by_id.get(str(out.get("segment_id", "")))
        if seg is not None:
            out["source_chars"] = source_char_count(seg)
    return out


def aggregate_grok_results(results: Sequence[Dict[str, Any]], segments: Sequence[Segment]) -> Dict[str, Any]:
    if not results:
        return {"grok_run": False, "gemini_run": False}

    seg_by_id = {s.id: s for s in segments}
    normalized = [normalize_mqm_result(r, seg_by_id) for r in results]
    valid = [r for r in normalized if r.get("weighted_error_points") is not None]
    total_points = sum(float(r.get("weighted_error_points") or 0) for r in valid)
    total_chars = 0
    for r in valid:
        if r.get("source_chars") is not None:
            total_chars += int(r.get("source_chars") or 1)
        else:
            seg = seg_by_id.get(str(r.get("segment_id", "")))
            total_chars += source_char_count(seg) if seg else 1
    error_rate_per_1000_chars = (total_points / max(1, total_chars)) * 1000
    avg_segment_score = sum(float(r.get("mqm_score_0_100") or 0) for r in valid) / max(1, len(valid))
    char_weighted_score = dashboard_score_from_error_rate(error_rate_per_1000_chars)

    categories = Counter()
    severity = Counter()
    for r in valid:
        for e in r.get("errors", []) or []:
            categories[e.get("category", "Unknown")] += 1
            severity[e.get("severity", "Unknown")] += 1

    return {
        "grok_run": True,
        "gemini_run": True,
        "judged_segments": len(valid),
        "failed_segments": len(results) - len(valid),
        "total_source_chars": total_chars,
        "total_weighted_error_points": round(total_points, 3),
        "mqm_error_rate_per_1000_source_chars": round(error_rate_per_1000_chars, 3),
        "char_weighted_mqm_score_0_100": char_weighted_score,
        "primary_score_0_100": char_weighted_score,
        "average_mqm_score_0_100": round(avg_segment_score, 2),
        "average_segment_mqm_score_0_100": round(avg_segment_score, 2),
        "error_categories": dict(categories),
        "error_severity": dict(severity),
        "score_note": "primary_score_0_100 is char-weighted and derived from MQM error points per 1000 source chars; average_segment_mqm_score_0_100 is secondary.",
    }


def aggregate_repeated_mqm_results(
    results: Sequence[Dict[str, Any]],
    segments: Sequence[Segment],
    expected_run_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    run_ids = list(expected_run_ids or [])
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in results:
        rid = str(r.get("_run_id") or r.get("run_id") or "run_01")
        grouped[rid].append(r)
        if rid not in run_ids:
            run_ids.append(rid)
    if not run_ids:
        run_ids = sorted(grouped)

    runs: List[Dict[str, Any]] = []
    for rid in run_ids:
        run_summary = aggregate_grok_results(grouped.get(rid, []), segments)
        run_summary["run_id"] = rid
        runs.append(run_summary)

    scored_runs = [r for r in runs if (r.get("grok_run") or r.get("gemini_run")) and int(r.get("judged_segments", 0)) > 0]
    score_values = [float(r["primary_score_0_100"]) for r in scored_runs if r.get("primary_score_0_100") is not None]
    epk_values = [float(r["mqm_error_rate_per_1000_source_chars"]) for r in scored_runs if r.get("mqm_error_rate_per_1000_source_chars") is not None]

    def mean(values: Sequence[float]) -> Optional[float]:
        return round(statistics.mean(values), 3) if values else None

    def stdev(values: Sequence[float]) -> Optional[float]:
        return round(statistics.stdev(values), 3) if len(values) >= 2 else (0.0 if values else None)

    def ci95_half_width(values: Sequence[float]) -> Optional[float]:
        if len(values) < 2:
            return 0.0 if values else None
        return round(1.96 * statistics.stdev(values) / (len(values) ** 0.5), 3)

    avg_score = mean(score_values)
    score_ci = ci95_half_width(score_values)
    avg_epk = mean(epk_values)
    epk_ci = ci95_half_width(epk_values)

    return {
        "grok_run": bool(results),
        "gemini_run": bool(results),
        "score_metric": "primary_score_0_100 / char_weighted_mqm_score_0_100",
        "error_rate_metric": "mqm_error_rate_per_1000_source_chars",
        "run_count_requested": len(run_ids),
        "run_count_scored": len(scored_runs),
        "runs": runs,
        "average_primary_score_0_100": avg_score,
        "primary_score_stddev": stdev(score_values),
        "primary_score_95ci_half_width": score_ci,
        "primary_score_95ci": [round(max(0.0, avg_score - score_ci), 3), round(min(100.0, avg_score + score_ci), 3)] if avg_score is not None and score_ci is not None else None,
        "average_mqm_error_rate_per_1000_source_chars": avg_epk,
        "mqm_error_rate_stddev": stdev(epk_values),
        "mqm_error_rate_95ci_half_width": epk_ci,
        "mqm_error_rate_95ci": [round(max(0.0, avg_epk - epk_ci), 3), round(avg_epk + epk_ci, 3)] if avg_epk is not None and epk_ci is not None else None,
        "score_note": "Average is computed over the repeated random samples. Lower error rate is better; higher primary score is better.",
    }


def write_repeated_scores_csv(path: Path, repeated_summary: Dict[str, Any]) -> None:
    fieldnames = [
        "run_id", "grok_run", "judged_segments", "failed_segments", "total_source_chars",
        "total_weighted_error_points", "mqm_error_rate_per_1000_source_chars",
        "primary_score_0_100", "char_weighted_mqm_score_0_100", "average_segment_mqm_score_0_100",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in repeated_summary.get("runs", []) or []:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def write_markdown_report(path: Path, ts_path: Path, summary: Dict[str, Any], glossary_warnings: Sequence[str], grok_summary: Dict[str, Any], repeated_summary: Optional[Dict[str, Any]] = None, structure_failed_rows: Optional[Sequence[Dict[str, Any]]] = None) -> None:
    det = summary
    lines = []
    lines.append(f"# QGIS .ts 翻譯評估報告\n")
    lines.append(f"- 評估檔案：`{ts_path.name}`")
    lines.append(f"- deterministic dashboard score：**{det['deterministic_score_0_100']} / 100**")
    lines.append(f"- deterministic error points / 1000 source chars：{det.get('deterministic_error_points_per_1000_source_chars')}")
    lines.append(f"- messages：{det['message_count']}")
    lines.append(f"- deterministic issues：{det['issue_count']}\n")
    lines.append("## Structure-only scores\n")
    structure_scores = det.get("structure_scores", {}) or {}
    if structure_scores:
        lines.append(f"- structure total score sum：**{structure_scores.get('total_score_sum')}**")
        lines.append(f"- structure average score：**{structure_scores.get('average_score_0_100')} / 100**")
        lines.append(f"- checked messages：{structure_scores.get('checked_messages')}")
        lines.append("")
        lines.append("| 項目 | 分數 | affected segments | issue count |")
        lines.append("|---|---:|---:|---:|")
        for item in structure_scores.get("items", []) or []:
            lines.append(f"| {item.get('item')} | {item.get('score_0_100')} | {item.get('affected_segment_count')} | {item.get('issue_count')} |")
        lines.append("")
        lines.append("Completion-state issues are reported separately and are not counted in the structure score unless `--structure-include-completion-state` is enabled.\n")
        if structure_failed_rows:
            lines.append("### Structure failed sentences")
            lines.append("")
            lines.append("Full sentence-level details are written to `structure_failed_sentences.csv`. Previewing the first 20 rows:")
            lines.append("")
            lines.append("| index | item | issue types | source | translation |")
            lines.append("|---:|---|---|---|---|")
            for row in list(structure_failed_rows)[:20]:
                src = str(row.get("source", "")).replace("|", "\\|").replace("\n", "\\n")
                trg = str(row.get("translation", "")).replace("|", "\\|").replace("\n", "\\n")
                if len(src) > 160:
                    src = src[:157] + "..."
                if len(trg) > 160:
                    trg = trg[:157] + "..."
                lines.append(
                    f"| {row.get('index', '')} | {row.get('structure_item', '')} | {row.get('issue_types', '')} | {src} | {trg} |"
                )
            lines.append("")
    lines.append("## Deterministic QA\n")
    lines.append("| 指標 | 值 |")
    lines.append("|---|---:|")
    lines.append(f"| Placeholder preservation rate | {det['placeholder_preservation_rate']} |")
    lines.append(f"| Brace placeholder preservation rate | {det['brace_placeholder_preservation_rate']} |")
    lines.append(f"| HTML/XML tag preservation rate | {det['tag_preservation_rate']} |")
    lines.append(f"| Entity preservation rate | {det['entity_preservation_rate']} |")
    lines.append(f"| Terminology accuracy | {det['terminology_accuracy']} |")
    lines.append(f"| Affected segment rate | {det.get('affected_segment_rate')} |")
    lines.append(f"| Blocking issue count | {det.get('blocking_issue_count')} |\n")
    lines.append("### Issue counts\n")
    lines.append("```json")
    lines.append(json.dumps(det["issue_counts"], ensure_ascii=False, indent=2))
    lines.append("```\n")
    if glossary_warnings:
        lines.append("## 詞庫載入警告\n")
        for w in glossary_warnings:
            lines.append(f"- {w}")
        lines.append("")
    lines.append("## Grok / MQM\n")
    if grok_summary.get("grok_run") or grok_summary.get("gemini_run"):
        lines.append("| 指標 | 值 |")
        lines.append("|---|---:|")
        lines.append(f"| judged segments | {grok_summary.get('judged_segments')} |")
        lines.append(f"| failed segments | {grok_summary.get('failed_segments')} |")
        lines.append(f"| total weighted error points | {grok_summary.get('total_weighted_error_points')} |")
        lines.append(f"| MQM error rate / 1000 source chars | {grok_summary.get('mqm_error_rate_per_1000_source_chars')} |")
        lines.append(f"| primary char-weighted MQM score | {grok_summary.get('primary_score_0_100')} |")
        lines.append(f"| average segment MQM score | {grok_summary.get('average_segment_mqm_score_0_100')} |\n")
        lines.append("### Grok error categories\n")
        lines.append("```json")
        lines.append(json.dumps(grok_summary.get("error_categories", {}), ensure_ascii=False, indent=2))
        lines.append("```\n")
    else:
        lines.append("Grok 尚未執行。已輸出 `grok_requests.jsonl`，加上 `--run-grok` 後會呼叫 Grok API。\n")
    if repeated_summary and repeated_summary.get("runs"):
        lines.append("## Repeated random-sampling MQM scores\n")
        lines.append(f"- average primary score：**{repeated_summary.get('average_primary_score_0_100')} / 100**")
        lines.append(f"- average MQM error rate / 1000 source chars：**{repeated_summary.get('average_mqm_error_rate_per_1000_source_chars')}**")
        lines.append(f"- scored runs：{repeated_summary.get('run_count_scored')} / {repeated_summary.get('run_count_requested')}\n")
        lines.append("| run | judged | failed | error rate / 1000 chars | primary score |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in repeated_summary.get("runs", []):
            lines.append(
                f"| {r.get('run_id')} | {r.get('judged_segments', '')} | {r.get('failed_segments', '')} | "
                f"{r.get('mqm_error_rate_per_1000_source_chars', '')} | {r.get('primary_score_0_100', '')} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

# -----------------------------
# Main / Batch orchestration
# -----------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate Qt .ts translation files with ODS glossary and optional Grok MQM judging.")
    p.add_argument(
        "ts_file",
        nargs="?",
        type=Path,
        default=None,
        help=(
            "Qt Linguist .ts file with source and translation. "
            "If omitted, the script scans --ts-dir (default: current directory) for all .ts files. "
            "If a directory is supplied, it is scanned for .ts files."
        ),
    )
    p.add_argument("--all-ts", action="store_true", help="Evaluate every .ts file under --ts-dir, or under ts_file when ts_file is a directory.")
    p.add_argument("--ts-dir", type=Path, default=Path("."), help="Directory to scan for .ts files when ts_file is omitted or --all-ts is used. Default: current directory.")
    p.add_argument("--recursive-ts", action="store_true", help="Recursively scan subdirectories for .ts files.")
    p.add_argument("--batch-summary-name", default="all_ts_mqm_scores", help="Base filename for the cross-file score summary CSV/JSON/MD. Default: all_ts_mqm_scores.")
    p.add_argument("--glossary", nargs="*", type=Path, default=[Path("1.ods"), Path("2.ods")], help="Glossary files. Defaults to 1.ods 2.ods.")
    p.add_argument("--config", type=Path, default=None, help="Optional YAML config.")
    p.add_argument("--outdir", type=Path, default=Path("ts_grok_eval_output"), help="Output directory.")
    p.add_argument("--target-language", default="zh-Hant", help="Expected TS target language metadata.")
    p.add_argument("--model", default=MODEL_DEFAULT, help="Grok model ID.")
    p.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE, help="Number of segments sampled per repeated MQM run. Default: 500.")
    p.add_argument("--total-mqm-request-budget", type=int, default=0, help=("Optional total Grok/MQM request budget across all selected .ts files. " "When > 0 in multi-file mode, the script distributes requests evenly by reducing --sample-size per file/run. " "Example: 5 files, --repeats 5, --total-mqm-request-budget 5000 => --sample-size becomes 200 per run per file."))
    p.add_argument("--sample-window", type=int, default=DEFAULT_SAMPLE_WINDOW, help="Only the first N parsed segments are eligible for random MQM sampling, capped at 10000. Use 0 to keep the cap at 10000, not all segments. Default: 10000.")
    p.add_argument("--repeats", type=int, default=DEFAULT_REPEAT_RUNS, help="Number of repeated random samples for MQM judging. Default: 5.")
    p.add_argument("--sampling-mode", choices=["random", "issue_enriched", "mixed"], default="random", help="Sampling strategy for Grok/MQM. Default: random.")
    p.add_argument("--max-ngram", type=int, default=8, help="Maximum glossary source-term n-gram length used by the hash matcher. Default: 8.")
    p.add_argument("--progress-interval", type=int, default=5000, help="Print deterministic-check progress every N segments. Use 0 to disable.")
    p.add_argument("--structure-score-limit", type=int, default=10000, help="Compute structure item scores from the first N messages. Default: 10000.")
    p.add_argument("--structure-include-completion-state", action="store_true", help="Also count unfinished/empty/missing translation as a scored item. Default: false.")
    p.add_argument("--script-check", choices=["none", "unihan", "opencc", "both"], default="unihan", help="zh-TW script/locale risk detector. 'unihan' uses Unicode Unihan_Variants.txt; 'opencc' uses OpenCC s2tw; 'both' uses both. Default: unihan.")
    p.add_argument("--unihan-variants", type=Path, default=Path("Unihan_Variants.txt"), help="Path to Unicode Unihan_Variants.txt for --script-check unihan/both.")
    p.add_argument("--judge-all", action="store_true", help="Prepare/judge all segments. This may be expensive.")
    p.add_argument("--run-grok", "--run-gemini", dest="run_grok", action="store_true", help="Actually call xAI/Grok API. --run-gemini is accepted as a backwards-compatible alias.")
    p.add_argument("--grok-mode", "--gemini-mode", dest="grok_mode", choices=["sync", "batch-inline", "batch-file"], default="batch-inline", help="Grok execution mode. Default: batch-inline xAI Batch API; use batch-file to upload JSONL to xAI Files API.")
    p.add_argument("--batch-job-name", default="", help="Existing Grok Batch API job name to collect, e.g. batches/123456. In multi-file mode, omit this so each per-file outdir can read its own grok_batch_job.json.")
    p.add_argument("--collect-batch", action="store_true", help="Collect results from --batch-job-name or from each per-file grok_batch_job.json instead of submitting a new batch job.")
    p.add_argument("--batch-wait", action="store_true", help="After submitting/collecting Batch API job, poll until it finishes. Use this when you need final repeated-run scores immediately.")
    p.add_argument("--batch-poll-seconds", type=int, default=30, help="Polling interval for Batch API jobs.")
    p.add_argument("--batch-timeout-seconds", type=int, default=86400, help="Maximum seconds to wait for Batch API completion.")
    p.add_argument("--batch-config-variant", choices=["response_json_schema", "response_schema"], default="response_schema", help="Legacy compatibility option from the Gemini version; ignored by the xAI/Grok REST path.")
    p.add_argument("--api-key", default="", help="Optional xAI/Grok API key. Overrides inline XAI_API_KEY and environment variables.")
    p.add_argument("--thinking-level", default="", choices=["minimal", "low", "medium", "high", ""], help="Legacy compatibility option; ignored by the xAI/Grok REST path.")
    p.add_argument("--seed", type=int, default=42, help="Random seed for sampling.")
    p.add_argument("--sleep", type=float, default=0.5, help="Sleep seconds between API calls.")
    p.add_argument("--max-calls", type=int, default=None, help="Safety cap for Grok calls/Batch requests. Applies per .ts file in multi-file mode.")
    return p.parse_args(argv)


def _default_args_for_config_compare() -> argparse.Namespace:
    """Return parser defaults without requiring a real TS file."""
    return parse_args([])


def apply_config(args: argparse.Namespace, cfg: Dict[str, Any]) -> argparse.Namespace:
    """Apply YAML config values after CLI parsing.

    This keeps backwards compatibility with the original script while adding
    multi-file options.  Config values are treated as defaults: explicit CLI
    values still win for the common path/glossary options.
    """
    if not cfg:
        return args

    defaults = _default_args_for_config_compare()
    path_default_keys = {
        "glossary": [Path("1.ods"), Path("2.ods")],
        "outdir": Path("ts_grok_eval_output"),
        "ts_dir": Path("."),
        "unihan_variants": Path("Unihan_Variants.txt"),
    }
    config_keys = [
        "target_language", "model", "sample_size", "sample_window", "repeats", "sampling_mode", "total_mqm_request_budget", "structure_score_limit", "structure_include_completion_state",
        "judge_all", "thinking_level", "sleep", "max_calls", "max_ngram", "progress_interval",
        "script_check", "unihan_variants", "grok_mode", "gemini_mode", "batch_job_name",
        "collect_batch", "batch_wait", "batch_poll_seconds", "batch_timeout_seconds",
        "batch_config_variant", "all_ts", "ts_dir", "recursive_ts", "batch_summary_name",
    ]
    for key in config_keys:
        if key not in cfg:
            continue
        dest_key = "grok_mode" if key == "gemini_mode" else key
        default_value = path_default_keys.get(dest_key, getattr(defaults, dest_key, None))
        current_value = getattr(args, dest_key, None)
        if current_value == default_value:
            value = cfg[key]
            if dest_key in {"ts_dir", "unihan_variants"}:
                value = Path(value)
            setattr(args, dest_key, value)

    if "glossary" in cfg and args.glossary == [Path("1.ods"), Path("2.ods")]:
        args.glossary = [Path(x) for x in cfg["glossary"]]
    if "outdir" in cfg and args.outdir == Path("ts_grok_eval_output"):
        args.outdir = Path(cfg["outdir"])
    return args


def _is_probably_batch_mode(args: argparse.Namespace, ts_files: Sequence[Path]) -> bool:
    return bool(args.all_ts or args.ts_file is None or (args.ts_file and args.ts_file.is_dir()) or len(ts_files) > 1)


def discover_ts_files(args: argparse.Namespace) -> List[Path]:
    """Resolve the .ts files to evaluate.

    New behavior requested by the user:
    - no positional argument => scan the current directory for all .ts files;
    - --all-ts => scan --ts-dir;
    - positional directory => scan that directory;
    - positional file => original single-file behavior.
    """
    if args.ts_file and args.ts_file.is_file() and not args.all_ts:
        return [args.ts_file]

    base_dir = args.ts_dir
    if args.ts_file and args.ts_file.is_dir():
        base_dir = args.ts_file
    elif args.ts_file and args.all_ts and args.ts_file.exists() and args.ts_file.is_dir():
        base_dir = args.ts_file

    base_dir = Path(base_dir)
    pattern = "**/*.ts" if args.recursive_ts else "*.ts"
    return sorted(p for p in base_dir.glob(pattern) if p.is_file())


def _safe_output_name(ts_path: Path, seen: Optional[set[str]] = None) -> str:
    """Create a stable filesystem-safe directory name for one .ts output."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ts_path.stem).strip("._-") or "ts_file"
    digest = hashlib.sha1(str(ts_path.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:8]
    candidate = f"{cleaned}_{digest}"
    if seen is not None:
        original = candidate
        n = 2
        while candidate in seen:
            candidate = f"{original}_{n}"
            n += 1
        seen.add(candidate)
    return candidate


def _score_status_from_summary(summary: Dict[str, Any]) -> str:
    if summary.get("error"):
        return "error"
    repeated = summary.get("mqm_repeated_scores", {}) or {}
    if repeated.get("average_primary_score_0_100") is not None:
        return "scored"
    if summary.get("batch_job", {}).get("job_name"):
        return "batch_submitted_pending" if not summary.get("batch_job", {}).get("collected_at") else "batch_collected_no_scores"
    if summary.get("grok_mqm", {}).get("failed_segments"):
        return "grok_failed_or_partial"
    return "grok_not_run"


def _run_score_dict(repeated_summary: Dict[str, Any], repeat_count: int) -> Dict[str, Any]:
    runs = repeated_summary.get("runs", []) or []
    by_run_id: Dict[str, Any] = {}
    for r in runs:
        rid = str(r.get("run_id") or "")
        if rid:
            by_run_id[rid] = r.get("primary_score_0_100", "")
    out: Dict[str, Any] = {}
    for i in range(1, max(1, repeat_count) + 1):
        rid = f"run_{i:02d}"
        out[rid] = by_run_id.get(rid, "")
    return out


def score_row_from_summary(summary: Dict[str, Any], repeat_count: int) -> Dict[str, Any]:
    deterministic = summary.get("deterministic", {}) or {}
    repeated = summary.get("mqm_repeated_scores", {}) or {}
    batch_job = summary.get("batch_job", {}) or {}
    row: Dict[str, Any] = {
        "ts_file": summary.get("ts_file", ""),
        "score_status": _score_status_from_summary(summary),
        "outdir": summary.get("outdir", ""),
        "structure_failures_csv": summary.get("structure_failures_csv", ""),
        "structure_failed_sentence_count": (summary.get("structure_failed_sentences_summary", {}) or {}).get("failed_sentence_rows", ""),
        "message_count": deterministic.get("message_count", ""),
        "deterministic_score_0_100": deterministic.get("deterministic_score_0_100", ""),
        "deterministic_error_points_per_1000_source_chars": deterministic.get("deterministic_error_points_per_1000_source_chars", ""),
        "structure_total_score_sum": deterministic.get("structure_total_score_sum", ""),
        "structure_average_score_0_100": deterministic.get("structure_average_score_0_100", ""),
        "structure_qt_placeholder_score": ((deterministic.get("structure_scores", {}) or {}).get("items", [{}])[0].get("score_0_100", "") if (deterministic.get("structure_scores", {}) or {}).get("items") else ""),
        "structure_brace_placeholder_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "brace_placeholder"), ""),
        "structure_printf_placeholder_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "printf_placeholder"), ""),
        "structure_html_xml_entity_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "html_xml_entity"), ""),
        "structure_html_xml_tag_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "html_xml_tag"), ""),
        "structure_number_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "number"), ""),
        "structure_newline_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "newline"), ""),
        "structure_accelerator_score": next((x.get("score_0_100", "") for x in (deterministic.get("structure_scores", {}) or {}).get("items", []) if x.get("item") == "accelerator"), ""),
        "mqm_average_primary_score_0_100": repeated.get("average_primary_score_0_100", ""),
        "mqm_primary_score_stddev": repeated.get("primary_score_stddev", ""),
        "mqm_primary_score_95ci_half_width": repeated.get("primary_score_95ci_half_width", ""),
        "mqm_average_error_rate_per_1000_source_chars": repeated.get("average_mqm_error_rate_per_1000_source_chars", ""),
        "mqm_run_count_scored": repeated.get("run_count_scored", ""),
        "mqm_run_count_requested": repeated.get("run_count_requested", ""),
        "batch_job_name": batch_job.get("job_name", "") or batch_job.get("batch_id", ""),
        "error": summary.get("error", ""),
    }
    row.update(_run_score_dict(repeated, repeat_count))
    return row


def write_all_structure_failed_sentences_csv(path: Path, summaries: Sequence[Dict[str, Any]]) -> int:
    """Merge per-file structure_failed_sentences.csv files into one batch CSV."""
    fieldnames = [
        "ts_file", "index", "segment_id", "context", "structure_item", "issue_count",
        "issue_types", "severities", "details", "source", "translation", "locations",
    ]
    row_count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            csv_path = summary.get("structure_failures_csv") or summary.get("structure_failed_sentences_csv")
            if not csv_path:
                continue
            source_path = Path(str(csv_path))
            if not source_path.exists():
                continue
            with source_path.open("r", encoding="utf-8-sig", newline="") as src_f:
                reader = csv.DictReader(src_f)
                for row in reader:
                    writer.writerow({k: row.get(k, "") for k in fieldnames})
                    row_count += 1
    return row_count



def write_all_ts_scores_csv(path: Path, summaries: Sequence[Dict[str, Any]], repeat_count: int) -> None:
    run_fields = [f"run_{i:02d}" for i in range(1, max(1, repeat_count) + 1)]
    fieldnames = [
        "ts_file", "score_status", "message_count", "deterministic_score_0_100",
        "deterministic_error_points_per_1000_source_chars",
        "structure_qt_placeholder_score", "structure_brace_placeholder_score", "structure_printf_placeholder_score",
        "structure_html_xml_entity_score", "structure_html_xml_tag_score", "structure_number_score",
        "structure_newline_score", "structure_accelerator_score", "structure_total_score_sum",
        "structure_average_score_0_100",
        *run_fields,
        "mqm_average_primary_score_0_100", "mqm_primary_score_stddev", "mqm_primary_score_95ci_half_width",
        "mqm_average_error_rate_per_1000_source_chars", "mqm_run_count_scored", "mqm_run_count_requested",
        "batch_job_name", "structure_failed_sentence_count", "structure_failures_csv", "outdir", "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            row = score_row_from_summary(summary, repeat_count)
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_all_ts_scores_markdown(path: Path, summaries: Sequence[Dict[str, Any]], repeat_count: int) -> None:
    run_fields = [f"run_{i:02d}" for i in range(1, max(1, repeat_count) + 1)]
    lines: List[str] = []
    lines.append("# All .ts MQM scores")
    lines.append("")
    lines.append(f"- Files evaluated: {len(summaries)}")
    lines.append(f"- Repeated runs requested per file: {repeat_count}")
    lines.append("")
    header = ["ts_file", "status", *run_fields, "average"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] + ["---:"] * (len(header) - 1)) + "|")
    for summary in summaries:
        row = score_row_from_summary(summary, repeat_count)
        values = [
            Path(str(row.get("ts_file", ""))).name,
            row.get("score_status", ""),
            *[row.get(k, "") for k in run_fields],
            row.get("mqm_average_primary_score_0_100", ""),
        ]
        lines.append("| " + " | ".join(str(v) for v in values) + " |")
    lines.append("")
    lines.append("`grok_not_run` 或 `batch_submitted_pending` 代表尚未取得 Grok/MQM 的重複抽樣評分；可使用 `--run-grok --grok-mode sync`，或 Batch API 搭配 `--batch-wait` / `--collect-batch` 取得分數。")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_all_ts_score_summary(summaries: Sequence[Dict[str, Any]], repeat_count: int) -> Dict[str, Any]:
    rows = [score_row_from_summary(s, repeat_count) for s in summaries]
    valid_mqm_avgs = [float(r["mqm_average_primary_score_0_100"]) for r in rows if r.get("mqm_average_primary_score_0_100") not in {"", None}]
    valid_det_scores = [float(r["deterministic_score_0_100"]) for r in rows if r.get("deterministic_score_0_100") not in {"", None}]
    valid_structure_avgs = [float(r["structure_average_score_0_100"]) for r in rows if r.get("structure_average_score_0_100") not in {"", None}]
    return {
        "ts_file_count": len(summaries),
        "succeeded_count": sum(1 for s in summaries if not s.get("error")),
        "failed_count": sum(1 for s in summaries if s.get("error")),
        "repeat_count": int(repeat_count),
        "overall_average_of_ts_mqm_average_scores_0_100": round(statistics.mean(valid_mqm_avgs), 3) if valid_mqm_avgs else None,
        "overall_average_of_ts_deterministic_scores_0_100": round(statistics.mean(valid_det_scores), 3) if valid_det_scores else None,
        "overall_average_of_ts_structure_average_scores_0_100": round(statistics.mean(valid_structure_avgs), 3) if valid_structure_avgs else None,
        "rows": rows,
    }




def apply_total_mqm_request_budget(args: argparse.Namespace, ts_files: Sequence[Path]) -> Tuple[argparse.Namespace, Dict[str, Any]]:
    """Return args with an evenly distributed MQM request budget applied.

    Without this option, total Grok requests are approximately:
        file_count * repeats * sample_size

    With --total-mqm-request-budget, the script keeps the requested number of
    repeated runs and lowers sample_size so every selected .ts file receives the
    same number of sampled segments per run.  Deterministic checks still scan all
    messages in every file.
    """
    budget = int(getattr(args, "total_mqm_request_budget", 0) or 0)
    if budget <= 0 or not ts_files or getattr(args, "judge_all", False):
        return args, {}

    file_count = max(1, len(ts_files))
    repeats = max(1, int(getattr(args, "repeats", DEFAULT_REPEAT_RUNS) or DEFAULT_REPEAT_RUNS))
    denominator = file_count * repeats
    per_run_sample_size = budget // denominator
    budget_too_small = False
    if per_run_sample_size < 1:
        per_run_sample_size = 1
        budget_too_small = True

    effective_args = argparse.Namespace(**vars(args))
    effective_args.sample_size = int(per_run_sample_size)
    effective_total = int(per_run_sample_size * denominator)
    per_file_total = int(per_run_sample_size * repeats)

    plan = {
        "enabled": True,
        "total_mqm_request_budget_requested": budget,
        "file_count": file_count,
        "repeats": repeats,
        "effective_sample_size_per_run_per_file": int(per_run_sample_size),
        "effective_mqm_requests_per_file": per_file_total,
        "effective_total_mqm_requests": effective_total,
        "unused_budget_due_to_rounding": max(0, budget - effective_total),
        "budget_too_small_minimum_one_sample_per_run_per_file": budget_too_small,
        "note": "Budget applies to Grok/MQM request rows only; deterministic checks still scan all segments.",
    }
    return effective_args, plan

def evaluate_one_ts(
    args: argparse.Namespace,
    ts_path: Path,
    outdir: Path,
    glossary: Sequence[GlossaryEntry],
    glossary_warnings: Sequence[str],
) -> Dict[str, Any]:
    """Evaluate a single .ts file and return the same final summary shape as the original script."""
    ensure_outdir(outdir)

    print(f"[load] parsing TS: {ts_path}", file=sys.stderr)
    root, segments = parse_ts(ts_path)
    print(f"[load] parsed {len(segments)} segments", file=sys.stderr)

    save_normalized_glossary(outdir / "normalized_glossary.csv", glossary)
    glossary_matcher = GlossaryHashMatcher(glossary, max_ngram=max(1, int(args.max_ngram)))
    print(f"[glossary] {glossary_matcher.stats()}", file=sys.stderr)
    write_json(outdir / "glossary_hash_index_summary.json", glossary_matcher.stats())

    script_detector = ScriptVariantDetector.build(str(args.script_check), Path(args.unihan_variants) if args.unihan_variants else None)
    print(f"[script-check] {script_detector.stats()}", file=sys.stderr)
    write_json(outdir / "script_variant_detector_summary.json", script_detector.stats())

    issues, meta = deterministic_checks(
        root,
        segments,
        glossary,
        args.target_language,
        glossary_matcher,
        int(args.progress_interval),
        script_detector,
    )
    summary = deterministic_summary(segments, issues, glossary, meta, glossary_matcher, structure_score_limit=int(args.structure_score_limit), structure_include_completion_state=bool(args.structure_include_completion_state))

    structure_failed_rows = build_structure_failed_sentence_rows(
        ts_path,
        segments,
        issues,
        limit=int(args.structure_score_limit),
        include_completion_state=bool(args.structure_include_completion_state),
        include_other_structure=False,
    )
    structure_failed_rows_with_other = build_structure_failed_sentence_rows(
        ts_path,
        segments,
        issues,
        limit=int(args.structure_score_limit),
        include_completion_state=bool(args.structure_include_completion_state),
        include_other_structure=True,
    )
    structure_failed_summary = structure_failed_rows_summary(structure_failed_rows)
    structure_failed_summary_with_other = structure_failed_rows_summary(structure_failed_rows_with_other)

    issues_by_segment: Dict[str, List[Issue]] = defaultdict(list)
    for issue in issues:
        issues_by_segment[issue.segment_id].append(issue)

    seg_by_id = {s.id: s for s in segments}
    sampling_pool, run_manifests, request_rows = build_repeated_mqm_plan(
        segments,
        issues,
        issues_by_segment,
        glossary_matcher,
        DEFAULT_STYLE_GUIDE_SUMMARY,
        args.model,
        sample_size=int(args.sample_size),
        repeats=int(args.repeats),
        seed=int(args.seed),
        sample_window=int(args.sample_window),
        sampling_mode=str(args.sampling_mode),
        judge_all=bool(args.judge_all),
    )
    selected_all_segments = [seg_by_id[sid] for run in run_manifests for sid in run.get("selected_segment_ids", []) if sid in seg_by_id]

    write_segment_csv(outdir / "segments.csv", segments)
    write_segment_csv(outdir / "selected_mqm_segments_legacy.csv", selected_all_segments)
    write_selected_mqm_runs_csv(outdir / "selected_mqm_segments.csv", run_manifests, seg_by_id)
    write_issues_csv(outdir / "deterministic_issues.csv", issues)
    write_structure_failed_sentences_csv(outdir / "structure_failed_sentences.csv", structure_failed_rows)
    write_structure_failed_sentences_csv(outdir / "structure_failed_sentences_with_other_structure.csv", structure_failed_rows_with_other)
    write_json(outdir / "structure_failed_sentences_summary.json", structure_failed_summary)
    write_json(outdir / "structure_failed_sentences_with_other_structure_summary.json", structure_failed_summary_with_other)
    write_json(outdir / "deterministic_summary.json", summary)
    write_json(outdir / "mqm_schema.json", mqm_response_schema())
    write_jsonl(outdir / "grok_requests.jsonl", request_rows)
    # Backwards-compatible alias for older automation that expects the Gemini filename.
    write_jsonl(outdir / "gemini_requests.jsonl", request_rows)
    write_json(outdir / "glossary_warnings.json", list(glossary_warnings))
    write_json(outdir / "mqm_sampling_plan.json", {
        "sample_window_requested": int(args.sample_window),
        "sample_window_effective": effective_sample_window(int(args.sample_window)),
        "sampling_frame_size": len(sampling_pool),
        "sample_size_per_run": int(args.sample_size),
        "repeats": int(args.repeats),
        "sampling_mode": str(args.sampling_mode),
        "seed": int(args.seed),
        "total_request_rows": len(request_rows),
        "runs": run_manifests,
    })

    grok_results: List[Dict[str, Any]] = []
    batch_job_manifest: Dict[str, Any] = {}
    if args.run_grok:
        if not _effective_grok_api_key(getattr(args, "api_key", "")):
            raise RuntimeError(
                "xAI/Grok API key is required when --run-grok is used. "
                "Pass --api-key, or set XAI_API_KEY / GROK_API_KEY environment variable."
            )

        if args.grok_mode in {"batch-inline", "batch-file"}:
            if args.collect_batch:
                batch_job_name = str(getattr(args, "batch_job_name", "") or "")
                if not batch_job_name:
                    # In multi-file mode, each file has its own outdir and can read its own manifest.
                    manifest_path = outdir / "grok_batch_job.json"
                    if manifest_path.exists():
                        batch_job_name = json.loads(manifest_path.read_text(encoding="utf-8")).get("job_name", "")
                if not batch_job_name:
                    raise RuntimeError("--collect-batch requires --batch-job-name or an existing grok_batch_job.json in this file's output directory.")
                print(f"[batch] collecting Grok Batch API job: {batch_job_name}", file=sys.stderr)
                grok_results, batch_job_manifest = collect_grok_batch_results(
                    batch_job_name,
                    outdir,
                    args.model,
                    api_key=getattr(args, "api_key", ""),
                    wait=bool(args.batch_wait),
                    poll_seconds=int(args.batch_poll_seconds),
                    timeout_seconds=int(args.batch_timeout_seconds),
                )
                write_jsonl(outdir / "grok_mqm_results.jsonl", grok_results)
            else:
                rows_to_submit = request_rows[: args.max_calls] if args.max_calls else request_rows
                print(f"[batch] submitting {len(rows_to_submit)} requests with Grok Batch API mode={args.grok_mode}", file=sys.stderr)
                batch_job_manifest = create_grok_batch_job(
                    rows_to_submit,
                    args.model,
                    outdir,
                    api_key=getattr(args, "api_key", ""),
                    mode=args.grok_mode,
                    display_name=f"qgis-ts-mqm-{ts_path.stem}-random{len(rows_to_submit)}",
                    config_variant=str(args.batch_config_variant),
                )
                print(f"[batch] submitted job: {batch_job_manifest.get('job_name')} state={batch_job_manifest.get('state')}", file=sys.stderr)
                if args.batch_wait:
                    grok_results, batch_job_manifest = collect_grok_batch_results(
                        batch_job_manifest["job_name"],
                        outdir,
                        args.model,
                        api_key=getattr(args, "api_key", ""),
                        wait=True,
                        poll_seconds=int(args.batch_poll_seconds),
                        timeout_seconds=int(args.batch_timeout_seconds),
                    )
                    write_jsonl(outdir / "grok_mqm_results.jsonl", grok_results)
                else:
                    # Batch jobs are asynchronous. Results can be collected later with:
                    # --run-grok --collect-batch --all-ts
                    write_jsonl(outdir / "grok_mqm_results.jsonl", [])
        else:
            rows_to_call = request_rows[: args.max_calls] if args.max_calls else request_rows
            consecutive_failures = 0
            for n, row in enumerate(rows_to_call, start=1):
                print(f"[{n}/{len(rows_to_call)}] Grok judging {row['segment_id']}...", file=sys.stderr)
                result = call_grok_row(
                    row,
                    args.model,
                    thinking_level=args.thinking_level or None,
                    api_key=getattr(args, "api_key", ""),
                )
                grok_results.append(result)
                if result.get("_error"):
                    consecutive_failures += 1
                    print(f"[Grok error] {row['segment_id']}: {result.get('_error')}", file=sys.stderr)
                    if consecutive_failures >= 5:
                        write_jsonl(outdir / "grok_mqm_results.jsonl", grok_results)
                        raise RuntimeError(
                            "Grok failed 5 times consecutively. Stopped early to avoid wasting quota. "
                            "Check grok_mqm_results.jsonl for _error and _tried_config_keys."
                        )
                else:
                    consecutive_failures = 0
                time.sleep(float(args.sleep))
            write_jsonl(outdir / "grok_mqm_results.jsonl", grok_results)
    else:
        # Leave an empty file to make pipeline outputs predictable.
        write_jsonl(outdir / "grok_mqm_results.jsonl", [])

    if grok_results:
        grok_results = [normalize_mqm_result(r, seg_by_id) for r in grok_results]
        write_jsonl(outdir / "grok_mqm_results.jsonl", grok_results)

    expected_run_ids = [r.get("run_id", f"run_{idx + 1:02d}") for idx, r in enumerate(run_manifests)]
    grok_summary = aggregate_grok_results(grok_results, segments)
    repeated_summary = aggregate_repeated_mqm_results(grok_results, segments, expected_run_ids)
    write_json(outdir / "grok_mqm_summary.json", grok_summary)
    # Backwards-compatible alias for older automation that expects the Gemini filename.
    write_json(outdir / "gemini_mqm_summary.json", grok_summary)
    write_json(outdir / "mqm_repeated_scores_summary.json", repeated_summary)
    write_repeated_scores_csv(outdir / "mqm_repeated_scores.csv", repeated_summary)

    final_summary = {
        "ts_file": str(ts_path),
        "outdir": str(outdir),
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "sampling_mode": args.sampling_mode,
        "structure_score_limit": int(args.structure_score_limit),
        "structure_include_completion_state": bool(args.structure_include_completion_state),
        "sample_window_requested": int(args.sample_window),
        "sample_window_effective": effective_sample_window(int(args.sample_window)),
        "sampling_frame_size": len(sampling_pool),
        "sample_size_per_run": int(args.sample_size),
        "repeats": int(args.repeats),
        "total_mqm_request_rows": len(request_rows),
        "grok_mode": args.grok_mode,
        "batch_job": batch_job_manifest,
        "structure_failures_csv": str(outdir / "structure_failed_sentences.csv"),
        "structure_failures_with_other_structure_csv": str(outdir / "structure_failed_sentences_with_other_structure.csv"),
        "structure_failed_sentences_summary": structure_failed_summary,
        "structure_failed_sentences_with_other_structure_summary": structure_failed_summary_with_other,
        "glossary_files": [str(p) for p in args.glossary],
        "glossary_entry_count": len(glossary),
        "glossary_matcher": glossary_matcher.stats(),
        "script_variant_detector": script_detector.stats(),
        "deterministic": summary,
        "grok_mqm": grok_summary,
        "mqm_repeated_scores": repeated_summary,
        "notes": [
            "Structure-only score reports item scores, total score sum, and final average; unfinished/empty/missing translations are separated unless explicitly included.",
            "Deterministic QA validates software-localization invariants and terminology hits.",
            "Enhanced structural guard additionally checks quote literals, code/expression syntax, backslash escapes, encoded comparison operators, and mixed quote pairs.",
            "Deterministic pre-check issues are treated as candidates in the LLM prompt, not as facts to be blindly accepted.",
            "LLM/MQM penalties are canonicalized in code from severity labels: Neutral=0, Minor=1, Major=5, Critical=25.",
            "Primary MQM score is char-weighted and derived from error points per 1000 source characters; average per-segment MQM score is secondary.",
            "Default MQM estimation uses 5 repeated random samples from the first 10,000 parsed messages; control cost with --repeats, --sample-size, or --total-mqm-request-budget.",
            "Grok/MQM score is only available when --run-grok is used and, for Batch API, after the batch job has completed and been collected.",
            "Grok/xAI API key should be supplied by --api-key, XAI_API_KEY env var, or GROK_API_KEY env var; do not commit keys to source code.",
            "For paper-grade use, validate a subset against human MQM annotations.",
        ],
    }
    write_json(outdir / "summary.json", final_summary)
    write_markdown_report(outdir / "report.md", ts_path, summary, glossary_warnings, grok_summary, repeated_summary, structure_failed_rows)
    return final_summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cfg = read_config(args.config)
    args = apply_config(args, cfg)

    ts_files = discover_ts_files(args)
    if not ts_files:
        search_dir = args.ts_dir if not (args.ts_file and args.ts_file.is_dir()) else args.ts_file
        raise FileNotFoundError(f"No .ts files found in {search_dir!s}. Use a .ts file path, --ts-dir, --all-ts, or --recursive-ts.")

    batch_mode = _is_probably_batch_mode(args, ts_files)
    if batch_mode and args.collect_batch and args.batch_job_name and len(ts_files) > 1:
        raise RuntimeError(
            "In multi-file --collect-batch mode, omit --batch-job-name so the script can collect each file's own "
            "grok_batch_job.json from its output directory."
        )

    ensure_outdir(args.outdir)

    effective_args, budget_plan = apply_total_mqm_request_budget(args, ts_files)

    print(f"[discover] {len(ts_files)} .ts file(s) selected", file=sys.stderr)
    if budget_plan:
        print(
            "[budget] distributing Grok/MQM requests evenly: "
            f"requested={budget_plan['total_mqm_request_budget_requested']}, "
            f"files={budget_plan['file_count']}, repeats={budget_plan['repeats']}, "
            f"sample_size_per_run_per_file={budget_plan['effective_sample_size_per_run_per_file']}, "
            f"effective_total={budget_plan['effective_total_mqm_requests']}",
            file=sys.stderr,
        )
    print(f"[load] loading glossary files once: {', '.join(str(p) for p in effective_args.glossary) if effective_args.glossary else '(none)'}", file=sys.stderr)
    glossary, glossary_warnings = load_glossaries(effective_args.glossary)

    summaries: List[Dict[str, Any]] = []
    seen_output_names: set[str] = set()
    for index, ts_path in enumerate(ts_files, start=1):
        print(f"[file {index}/{len(ts_files)}] {ts_path}", file=sys.stderr)
        per_file_outdir = effective_args.outdir if not batch_mode else effective_args.outdir / _safe_output_name(ts_path, seen_output_names)
        try:
            summary = evaluate_one_ts(effective_args, ts_path, per_file_outdir, glossary, glossary_warnings)
        except Exception as e:
            if not batch_mode:
                raise
            error_summary = {
                "ts_file": str(ts_path),
                "outdir": str(per_file_outdir),
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
                "deterministic": {},
                "grok_mqm": {},
                "mqm_repeated_scores": {},
                "batch_job": {},
            }
            ensure_outdir(per_file_outdir)
            write_json(per_file_outdir / "summary.json", error_summary)
            summaries.append(error_summary)
            print(f"[error] {ts_path}: {e}", file=sys.stderr)
            continue
        summaries.append(summary)

    if batch_mode:
        base = effective_args.batch_summary_name.strip() or "all_ts_mqm_scores"
        csv_path = effective_args.outdir / f"{base}.csv"
        json_path = effective_args.outdir / f"{base}.json"
        md_path = effective_args.outdir / f"{base}.md"
        structure_failures_path = effective_args.outdir / f"{base}_structure_failed_sentences.csv"
        write_all_ts_scores_csv(csv_path, summaries, int(effective_args.repeats))
        write_all_ts_scores_markdown(md_path, summaries, int(effective_args.repeats))
        merged_structure_failure_rows = write_all_structure_failed_sentences_csv(structure_failures_path, summaries)
        all_summary = build_all_ts_score_summary(summaries, int(effective_args.repeats))
        all_summary.update({
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(md_path),
            "structure_failed_sentences_csv": str(structure_failures_path),
            "structure_failed_sentence_rows": merged_structure_failure_rows,
            "outdir": str(effective_args.outdir),
            "ts_files": [str(p) for p in ts_files],
            "mqm_request_budget_plan": budget_plan,
            "notes": [
                "Each per-file output directory contains the original detailed reports.",
                "The CSV/JSON/MD files at the batch root summarize run_01 ... run_N and each file's average MQM score.",
                "The batch root also includes *_structure_failed_sentences.csv, which merges the sentence-level structure failures for all .ts files.",
                "If Grok was not run, or if Batch API was submitted without collection, MQM run scores remain blank until results are collected.",
            ],
        })
        write_json(json_path, all_summary)
        print(json.dumps(all_summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summaries[0], ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
