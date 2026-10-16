from __future__ import annotations
import hashlib
import html
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from dateutil.parser import parse as parse_date

UTC = timezone.utc

def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")

def text(value: Any) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ")).strip()

def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))))

def iso(value: Any) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC).isoformat(timespec="seconds")
        # Preserve year/month precision rather than fabricating a day.
        if re.fullmatch(r"\d{4}(-\d{2})?", str(value).strip()):
            return str(value).strip()
        d = parse_date(str(value))
        return (d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return ""

def doi_of(value: str) -> str:
    match = re.search(r"10\.\d{4,9}/[^\s<>\"?#]+", value or "", re.I)
    return match.group(0).rstrip(".,;)").lower() if match else ""

def arxiv_of(value: str) -> str:
    match = re.search(r"(?:arxiv[:./]|/abs/|/pdf/)(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", value or "", re.I)
    if match:
        return match.group(1).lower()
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", value or ""):
        return re.sub(r"v\d+$", "", value)
    return ""

def safe_url(value: Any) -> str:
    value = str(value or "").strip()
    return value if urlparse(value).scheme in {"https", "http"} else ""

def code_links(value: str) -> list[str]:
    # A detected repository URL is NOT evidence that it is official or openly licensed.
    urls = re.findall(r"https?://(?:www\.)?(?:github\.com|gitlab\.com|codeberg\.org)/[^\s<>\"']+", value or "")
    return sorted(set(u.rstrip(".,;)]}") for u in urls))

def classify(kind: str, title: str = "", doi: str = "") -> str:
    s = (kind or "").casefold().strip()
    if "d41586" in doi or re.search(r"\b(news|editorial|comment|correction|retraction|obituary|perspective|briefing)\b", s + " " + title.casefold()[:45]):
        return "commentary"
    if "review" in s:
        return "review"
    if s in {"article", "research", "research article", "research-article", "research articles", "research_article"}:
        return "research_article"
    return "unspecified"

def rank(title: str, abstract: str, config: dict) -> tuple[int, list[str], list[str]]:
    title_n, body_n = f" {norm(title)} ", f" {norm(abstract)} "
    score, tags, hits = 0, [], []
    for topic in config["topics"]:
        local = []
        for term in topic["terms"]:
            term_n = f" {norm(term)} "
            if term_n in title_n or term_n in body_n:
                local.append(term)
                score += topic["weight"] * (2 if term_n in title_n else 1)
        if local:
            tags.append(topic["id"])
            hits.extend(local)
    return min(score, 100), tags, list(dict.fromkeys(hits))

def make_record(*, source: str, family: str, url: str, title: str,
                authors: list[str] | None = None, abstract: str = "", published: str = "",
                updated: str = "", status: str = "preprint", document_type: str = "unspecified",
                doi: str = "", arxiv_id: str = "", pdf_url: str = "", venue: str = "",
                code: list[str] | None = None, **extra: Any) -> dict:
    return dict(source=source, family=family, url=safe_url(url), title=text(title),
                authors=[text(a) for a in (authors or []) if text(a)], abstract=text(abstract),
                published_at=iso(published), updated_at=iso(updated), status=status,
                document_type=document_type, doi=doi_of(doi), arxiv_id=arxiv_of(arxiv_id),
                pdf_url=safe_url(pdf_url), venue=venue or source, code_links=code or [], **extra)

def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def first_author(authors: list[str]) -> str:
    if not authors:
        return ""
    # Require matching whole first author, not only surname (avoids many false merges).
    return norm(authors[0].replace(",", " "))

class Store:
    def __init__(self, payload: dict, config: dict):
        self.papers = {p["id"]: p for p in payload.get("papers", [])}
        self.config = config
        self.index: dict[str, str] = {}
        for pid, paper in self.papers.items():
            for a in paper.get("aliases", []):
                self.index[a] = pid

    def aliases(self, r: dict) -> list[str]:
        keys = ["url:" + r["url"]] if r.get("url") else []
        if r.get("doi"):
            keys.append("doi:" + r["doi"])
        if r.get("arxiv_id"):
            keys.append("arxiv:" + r["arxiv_id"])
        if r.get("authors") and len(norm(r["title"])) > 20:
            keys.append("titleauthor:" + norm(r["title"]) + "|" + first_author(r["authors"]))
        return keys

    def ingest(self, records: list[dict], seen: str) -> tuple[int, int]:
        added = changed = 0
        for r in records:
            if not r.get("title") or not r.get("url"):
                continue
            score, tags, hits = rank(r["title"], r.get("abstract", ""), self.config)
            aliases = self.aliases(r)
            existing = {self.index[a] for a in aliases if a in self.index}
            # Existing DOI/arXiv is stronger than a title match. Merge bridged entries.
            pid = next((self.index[a] for a in aliases if a in self.index and a.startswith(("doi:", "arxiv:"))), None)
            pid = pid or next(iter(sorted(existing)), None)
            if pid is None and score < self.config["site"]["minimum_score"]:
                continue
            if pid is None:
                pid = hashlib.sha256((aliases[0] if aliases else r["title"]).encode()).hexdigest()[:16]
                self.papers[pid] = dict(id=pid, first_seen_at=seen, record_changed_at=seen, aliases=[], records={})
                added += 1
            p = self.papers[pid]
            # Never merge merely on a fuzzy title; only observed stable identifiers or exact title+author.
            for other_id in existing - {pid}:
                other = self.papers.pop(other_id)
                p["records"].update(other.get("records", {}))
                p["aliases"] = list(set(p["aliases"] + other.get("aliases", [])))
                p["first_seen_at"] = min(p["first_seen_at"], other["first_seen_at"])
                for alias in other.get("aliases", []):
                    self.index[alias] = pid
            rkey = r["source"] + "|" + ("arxiv:" + r["arxiv_id"] if r.get("arxiv_id") and r["source"] == "arXiv" else r["url"])
            if p["records"].get(rkey) != r:
                if p["records"]:
                    changed += 1
                p["record_changed_at"] = seen
                p["records"][rkey] = r
            p["aliases"] = sorted(set(p["aliases"] + aliases))
            for alias in p["aliases"]:
                self.index[alias] = pid
            self.summarize(p)
        return added, changed

    def summarize(self, p: dict) -> None:
        recs = list(p["records"].values())
        priority = {"preprint": 1, "under_review": 0, "accepted": 2, "published": 3}
        recs.sort(key=lambda r: (priority.get(r["status"], 0), r.get("updated_at", ""), r.get("published_at", "")), reverse=True)
        lead = recs[0]
        for key in ["title", "authors", "status", "venue", "url", "document_type"]:
            p[key] = lead.get(key)
        if p["document_type"] == "unspecified":
            p["document_type"] = next((r["document_type"] for r in recs if r["document_type"] != "unspecified" and r["status"] == p["status"]), "unspecified")
        abstract_rec = max(recs, key=lambda r: len(r.get("abstract", "")))
        p["abstract"] = abstract_rec.get("abstract", "")
        p["abstract_source"] = abstract_rec["url"] if p["abstract"] else ""
        p["abstract_note"] = abstract_rec.get("abstract_note", "来源摘要；未做机器翻译")
        p["doi"] = next((r["doi"] for r in recs if r.get("doi")), "")
        p["arxiv_id"] = next((r["arxiv_id"] for r in recs if r.get("arxiv_id")), "")
        p["pdf_url"] = next((r["pdf_url"] for r in recs if r.get("pdf_url")), "")
        # First appearance and formal-publication dates are deliberately separate.
        p["published_at"] = lead.get("published_at", "")
        p["preprint_at"] = min((r["published_at"] for r in recs if r["status"] == "preprint" and r.get("published_at")), default="")
        p["source_updated_at"] = max((r.get("updated_at", "") for r in recs), default="")
        p["sources"] = sorted(set(r["source"] for r in recs))
        p["families"] = sorted(set(r["family"] for r in recs))
        p["source_urls"] = list(dict.fromkeys(r["url"] for r in recs))
        p["code_links"] = sorted(set(u for r in recs for u in r.get("code_links", []) if safe_url(u)))
        p["code_status"] = "candidate" if p["code_links"] else "unknown"
        p["code_evidence"] = ""
        p["score"], p["topics"], p["matched_terms"] = rank(p["title"], p["abstract"], self.config)

    def apply_overrides(self, overrides: list[dict]) -> None:
        for patch in overrides:
            pid = self.index.get(patch.get("match", "")) or (patch.get("match") if patch.get("match") in self.papers else None)
            if not pid:
                continue
            p = self.papers[pid]
            if patch.get("document_type"):
                p["document_type"] = patch["document_type"]
            if patch.get("official_code") and safe_url(patch.get("evidence_url")) and safe_url(patch.get("code_url")):
                p["code_status"] = "verified"
                p["code_links"] = sorted(set(p["code_links"] + [patch["code_url"]]))
                p["code_evidence"] = patch["evidence_url"]
                p["code_checked_at"] = patch.get("checked_at", "")
                p["code_verification_note"] = patch.get("note", "人工依据作者/出版方声明核验；未验证可复现性")

    def payload(self) -> dict:
        return {"schema_version": 1, "papers": sorted(self.papers.values(), key=lambda p: p["id"])}
