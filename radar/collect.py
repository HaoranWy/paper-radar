from __future__ import annotations
import os
import calendar
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from .core import (arxiv_of, classify, code_links, doi_of, iso, make_record, now_iso, rank, text)

def public_note(value: object, limit: int = 1500) -> str:
    """Remove contact credentials from diagnostics before persisting public metadata."""
    message = str(value)
    contact = os.getenv("CROSSREF_MAILTO", "")
    if contact:
        for variant in {contact, quote(contact, safe=""), quote_plus(contact)}:
            message = message.replace(variant, "[redacted]")
    message = re.sub(r"([?&](?:mailto|api_key|apikey|access_token|token)=)[^&\s)]+",
                     r"\1[redacted]", message, flags=re.IGNORECASE)
    return message[:limit]


@dataclass
class Result:
    records: list[dict] = field(default_factory=list)
    complete: bool = True
    note: str = ""
    available: bool = True
    failed: bool = False  # Preserve partial records while still failing source-health.

class HTTP:
    """Serial, per-host throttled requests; no CAPTCHA/paywall bypass."""
    def __init__(self, config: dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config["fetch"]["user_agent"]
        self.last: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser | None] = {}

    def get(self, url: str, params: dict | None = None, html_page: bool = False) -> requests.Response:
        host = urlparse(url).netloc
        if html_page:
            origin = f"{urlparse(url).scheme}://{host}"
            if origin not in self.robots:
                try:
                    rr = self.get(origin + "/robots.txt")
                    parser = RobotFileParser()
                    parser.parse(rr.text.splitlines())
                    self.robots[origin] = parser
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 404:
                        self.robots[origin] = None
                    else:
                        raise RuntimeError(f"无法确认 robots.txt，跳过 HTML 抓取: {origin}") from exc
            parser = self.robots[origin]
            if parser is not None and not parser.can_fetch(self.session.headers["User-Agent"], url):
                raise RuntimeError(f"robots.txt 不允许此页面: {url}")
        delay = 3.2 if "arxiv.org" in host else 1.0
        for attempt in range(self.config["fetch"]["retries"]):
            time.sleep(max(0, delay - (time.monotonic() - self.last.get(host, 0))))
            self.last[host] = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=self.config["fetch"]["timeout_seconds"])
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry = response.headers.get("Retry-After", "")
                    time.sleep(min(90, float(retry)) if retry.isdigit() else min(45, 2 ** (attempt + 1)))
                    response.raise_for_status()
                response.raise_for_status()
                if len(response.content) > 100_000_000:
                    raise RuntimeError(f"响应过大，停止处理: {host}")
                return response
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    if exc.response.status_code not in {429, 500, 502, 503, 504}:
                        raise
                if attempt + 1 == self.config["fetch"]["retries"]:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("request failed")

def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()

def values(node: ET.Element, name: str) -> list[str]:
    return ["".join(x.itertext()).strip() for x in node.iter() if local(x.tag) == name.lower()]

def first(node: ET.Element, *names: str) -> str:
    for name in names:
        vv = values(node, name)
        if vv:
            return vv[0]
    return ""

def parse_feed(content: bytes, source: str, family: str, is_arxiv: bool = False) -> tuple[list[dict], int]:
    if b"<!ENTITY" in content or b"<!DOCTYPE" in content:
        raise ValueError("XML DTD/entity is not supported")
    root = ET.fromstring(content)
    entries = [x for x in root.iter() if local(x.tag) in {"item", "entry"}]
    if not entries and local(root.tag) not in {"feed", "rss", "rdf"}:
        raise ValueError("响应不是 RSS/Atom；可能返回了登录或验证码页面")
    total = int(first(root, "totalresults") or len(entries))
    records = []
    for e in entries:
        title = first(e, "title")
        if title.casefold().strip() == "error":
            raise ValueError(first(e, "summary", "description") or "feed error")
        url, pdf_url = "", ""
        for link in [x for x in e if local(x.tag) == "link"]:
            href = link.attrib.get("href") or (link.text or "").strip()
            rel = link.attrib.get("rel", "alternate")
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = href
            elif rel == "alternate":
                url = href
        url = url or first(e, "url", "guid", "id")
        abstract = first(e, "summary", "description", "abstract", "encoded")
        authors = []
        for child in e:
            if local(child.tag) == "author":
                authors.append(first(child, "name") or text("".join(child.itertext())))
            elif local(child.tag) == "creator":
                authors.append(text("".join(child.itertext())))
        doi = doi_of(first(e, "doi", "identifier")) or doi_of(url)
        entry_type = first(e, "section", "category", "genre")
        records.append(make_record(source=source, family=family, url=url, title=title, authors=authors,
                    abstract=abstract, published=first(e, "published", "publicationdate", "pubdate", "date"),
                    updated=first(e, "updated", "modified"), status="preprint" if is_arxiv else "published",
                    document_type="research_article" if is_arxiv else classify(entry_type, title, doi),
                    doi=doi, arxiv_id=first(e, "id") if is_arxiv else "", pdf_url=pdf_url,
                    code=code_links(abstract + " " + first(e, "comment"))))
    return records, total

def source_error(exc: Exception) -> str:
    """Keep a short, redacted diagnostic, rather than an entire long query URL."""
    response = getattr(exc, "response", None)
    if response is None:
        return public_note(f"{type(exc).__name__}: {exc}", 400)
    code = response.status_code
    label = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                # Name/type only: never print tokens, cookies, or a full response body.
                label = str(error.get("name") or error.get("type") or "")
            label = label or str(payload.get("name") or "")
    except (ValueError, TypeError):
        pass
    host = urlparse(response.url or "").hostname or "upstream"
    return public_note(f"HTTP {code} from {host}" + (f" ({label})" if label else ""), 400)


def source_terms(cfg: dict) -> list[str]:
    return list(dict.fromkeys(str(term).replace('"', '').strip()
                             for topic in cfg["topics"] for term in topic["terms"]
                             if str(term).replace('"', '').strip()))


def arxiv(http: HTTP, cfg: dict, since: str) -> Result:
    """Bounded, serial batches. Keep successful batches if another request fails."""
    source = cfg["sources"]["arxiv"]
    cats = " OR ".join("cat:" + c for c in source["categories"])
    terms = source_terms(cfg)
    if not cats or not terms:
        raise ValueError("arXiv categories and topic terms must not be empty")
    batch_size = max(1, min(8, int(source.get("query_batch_size", 8))))
    page_size = max(1, min(100, int(source.get("page_size", 100))))
    max_pages = max(1, int(cfg["fetch"]["max_pages"]))
    batches = [terms[i:i + batch_size] for i in range(0, len(terms), batch_size)]
    result = Result()
    rows: dict[str, dict] = {}
    warnings = []
    errors = 0
    requested = 0
    # Avoid issuing every batch against an API that is consistently unavailable.
    consecutive_errors = 0
    for index, batch in enumerate(batches, 1):
        if consecutive_errors >= 2:
            result.complete = False
            warnings.append(f"连续两个批次请求失败；本次停止其余 {len(batches) - index + 1} 个批次，保留时间水位")
            break
        query = f'({cats}) AND (' + " OR ".join(f'all:"{t}"' for t in batch) + ")"
        print(f"[arXiv] batch {index}/{len(batches)} ({len(batch)} terms)", flush=True)
        requested += 1
        reached_end = False
        start = 0
        batch_failed = False
        for _ in range(max_pages):
            try:
                response = http.get("https://export.arxiv.org/api/query", params={
                    "search_query": query, "start": start, "max_results": page_size,
                    "sortBy": "lastUpdatedDate", "sortOrder": "descending"})
                records, total = parse_feed(response.content, "arXiv", "arXiv", True)
                if not records and start < total:
                    raise ValueError("arXiv returned an empty page before totalResults was reached")
                for record in records:
                    stamp = record["updated_at"] or record["published_at"]
                    if stamp >= since:
                        key = record.get("arxiv_id") or record["url"]
                        prior = rows.get(key)
                        if prior is None or stamp > (prior["updated_at"] or prior["published_at"]):
                            rows[key] = record
                start += len(records)
                if not records or start >= total:
                    reached_end = True
                    break
                if min(r["updated_at"] or r["published_at"] for r in records) < since:
                    reached_end = True
                    break
            except (requests.RequestException, ValueError, RuntimeError, ET.ParseError) as exc:
                errors += 1
                batch_failed = True
                warnings.append(f"批次 {index}: {source_error(exc)}")
                print(f"[arXiv] {warnings[-1]}", flush=True)
                break
        if batch_failed:
            consecutive_errors += 1
        else:
            consecutive_errors = 0
        if not reached_end:
            result.complete = False
            if not batch_failed:
                warnings.append(f"批次 {index} 达到分页上限；保留时间水位")
    result.records = list(rows.values())
    result.failed = errors > 0
    result.note = (f"分批检索 {requested}/{len(batches)}，每批最多 {batch_size} 个词，每页最多 {page_size} 条。"
                   + " | ".join(warnings))
    return result


def crossref_date(item: dict) -> str:
    for key in ["published-online", "published-print", "published", "issued"]:
        parts = item.get(key, {}).get("date-parts", [[]])[0]
        if parts:
            return "-".join(str(v) if i == 0 else f"{v:02d}" for i, v in enumerate(parts))
    return ""

def parse_crossref(item: dict, journal: dict) -> dict:
    title = " ".join(item.get("title", []))
    doi = item.get("DOI", "")
    abstract = item.get("abstract", "")
    published = crossref_date(item)
    return make_record(source=journal["name"], family=journal["family"], title=title,
                url=item.get("URL") or "https://doi.org/" + doi,
                authors=[(" ".join([a.get("given", ""), a.get("family", "")]).strip() or a.get("name", "")) for a in item.get("author", [])],
                abstract=abstract, published=published, updated=item.get("deposited", {}).get("date-time", ""),
                status="published", document_type=classify(item.get("subtype", ""), title, doi), doi=doi,
                code=code_links(abstract), metadata_provider="Crossref")

def journal(http: HTTP, cfg: dict, spec: dict, since: str) -> Result:
    result = Result()
    warnings = []
    try:
        records, _ = parse_feed(http.get(spec["rss"]).content, spec["name"], spec["family"])
        # Keep missing publication dates, explicitly unknown; do not invent today's date.
        result.records.extend(r for r in records if not r["published_at"] or r["published_at"] >= since)
    except Exception as exc:
        warnings.append("RSS失败，使用Crossref元数据: " + public_note(exc, 180))
    # Always supplement RSS. Rolling feeds alone do not guarantee missed-run recovery.
    try:
        cursor = "*"
        for page in range(cfg["fetch"]["max_pages"]):
            params: dict[str, Any] = {"filter": f"from-update-date:{since[:19]},type:journal-article", "rows": 1000, "cursor": cursor}
            if os.getenv("CROSSREF_MAILTO"):
                params["mailto"] = os.environ["CROSSREF_MAILTO"]
            data = http.get(f'https://api.crossref.org/journals/{spec["issn"]}/works', params=params).json()["message"]
            items = data.get("items", [])
            result.records.extend(parse_crossref(x, spec) for x in items)
            if len(items) < 1000:
                result.note = "; ".join(warnings)
                return result
            cursor = data.get("next-cursor")
            if not cursor:
                raise ValueError("Crossref游标缺失，不能确认增量完整性")
        result.complete = False
        warnings.append("Crossref达到分页上限，时间水位不前移")
    except Exception as exc:
        result.complete = False
        warnings.append("Crossref失败: " + public_note(exc, 180))
        if not result.records:
            raise RuntimeError("; ".join(warnings)) from exc
    result.note = "; ".join(warnings)
    return result

def content_value(content: dict, key: str, default: Any = "") -> Any:
    value = content.get(key, default)
    return value.get("value", default) if isinstance(value, dict) else value

def parse_openreview(note: dict, venue_id: str) -> dict | None:
    c = note.get("content", {})
    # Critically, a public submission is not an acceptance. Exact accepted venueid only.
    if content_value(c, "venueid") != venue_id:
        return None
    nid = note["id"]
    abstract = content_value(c, "abstract")
    urls = " ".join(str(content_value(c, k)) for k in ["code", "code_url", "code_of_ethics", "supplementary_material", "abstract"])
    venue_name = content_value(c, "venue") or venue_id
    return make_record(source="OpenReview", family="会议", url=f"https://openreview.net/forum?id={nid}",
                title=content_value(c, "title"), authors=content_value(c, "authors", []), abstract=abstract,
                # cdate/tcdate are creation times, not the acceptance/publication date.
                published=note.get("pdate", ""), updated=note.get("mdate", ""), status="accepted",
                document_type="research_article", venue=venue_name,
                pdf_url="https://openreview.net" + content_value(c, "pdf") if str(content_value(c, "pdf")).startswith("/") else content_value(c, "pdf"),
                doi=content_value(c, "doi"), code=code_links(urls), venue_id=venue_id,
                submitted_at=iso(note.get("cdate", "")))

def openreview_search(http: HTTP, cfg: dict, venue: str, reason: str,
                      previous: list[dict] | None = None) -> Result:
    """Documented public search endpoint; not an exhaustive venue listing.

    Search is a degraded metadata source, NOT an acceptance signal. The existing
    exact content.venueid check is still required for every record.
    """
    spec = cfg["sources"]["openreview"]
    size = max(1, min(100, int(spec.get("search_page_size", 100))))
    max_requests = max(1, int(spec.get("search_max_requests", 120)))
    max_pages = max(1, int(cfg["fetch"]["max_pages"]))
    terms = source_terms(cfg)
    if not terms:
        raise ValueError("OpenReview fallback requires nonempty topic terms")
    rows = {record["url"]: record for record in (previous or [])}
    warnings = []
    requests_used = finished_terms = 0
    failed = False
    stop = False
    previous_deny = getattr(http, "_paper_radar_openreview_search_denied", None)
    if previous_deny:
        return Result(records=list(rows.values()), complete=False, failed=True,
                      note="本次运行搜索接口已拒绝访问，不重复请求。" + previous_deny)
    for term in terms:
        if stop or requests_used >= max_requests:
            break
        start = 0
        ended = False
        for _ in range(max_pages):
            if requests_used >= max_requests:
                break
            requests_used += 1
            try:
                payload = http.get("https://api2.openreview.net/notes/search", params={
                    "term": term, "content": "all", "group": venue,
                    "source": "all", "limit": size, "offset": start}).json()
                if not isinstance(payload, dict) or not isinstance(payload.get("notes"), list):
                    raise ValueError("OpenReview search response has no notes list")
                notes = payload["notes"]
                for note in notes:
                    if not isinstance(note, dict):
                        raise ValueError("OpenReview search returned an invalid note")
                    # No replies, reviews, private items, or unaccepted submissions.
                    if note.get("replyto"):
                        continue
                    if "readers" in note and "everyone" not in note["readers"]:
                        continue
                    if record := parse_openreview(note, venue):
                        prior = rows.get(record["url"])
                        if prior is None or record["updated_at"] >= prior["updated_at"]:
                            rows[record["url"]] = record
                start += len(notes)
                if len(notes) < size:
                    ended = True
                    break
            except (requests.RequestException, ValueError, RuntimeError, KeyError, TypeError) as exc:
                failed = True
                warnings.append(f"搜索请求失败: {source_error(exc)}")
                response = getattr(exc, "response", None)
                if response is not None and response.status_code in {401, 403}:
                    http._paper_radar_openreview_search_denied = warnings[-1]
                # Avoid flooding an unavailable service; retry on the next daily run.
                stop = True
                break
        if ended:
            finished_terms += 1
        elif not stop:
            warnings.append("达到检索分页或请求预算，部分关键词未完成")
    if finished_terms < len(terms) and not warnings:
        warnings.append("达到请求预算，部分关键词未完成")
    note = (f"/notes 不可用 ({reason})；使用官方 /notes/search 主题检索。"
            f"完成 {finished_terms}/{len(terms)} 个关键词；仅保留 venueid 精确匹配的公开已录用论文。"
            "这是降级检索，不是完整会集，时间水位不前移。")
    if not rows:
        note += " 当前未检索到可确认录用的论文；不能据此断言该会议没有论文。"
    if warnings:
        note += " | " + " | ".join(warnings)
    return Result(records=list(rows.values()), complete=False, note=note, failed=failed)


def openreview(http: HTTP, cfg: dict, venue: str) -> Result:
    result = Result()
    spec = cfg["sources"]["openreview"]
    size = max(1, min(1000, int(spec.get("page_size", 1000))))
    denied = getattr(http, "_paper_radar_openreview_notes_denied", None)
    if denied and spec.get("search_fallback", True):
        return openreview_search(http, cfg, venue, denied)
    for page in range(max(1, int(cfg["fetch"]["max_pages"]))):
        try:
            payload = http.get("https://api2.openreview.net/notes", params={
                "content.venueid": venue, "limit": size, "offset": page * size}).json()
            if not isinstance(payload, dict) or not isinstance(payload.get("notes"), list):
                raise ValueError("OpenReview response has no notes list")
            notes = payload["notes"]
            result.records.extend(r for n in notes if (r := parse_openreview(n, venue)))
            if len(notes) < size:
                if not result.records:
                    result.note = "未找到公开已录用条目；可能尚未公开或 venue ID 需要核对。"
                return result
        except (requests.RequestException, ValueError, RuntimeError, KeyError, TypeError) as exc:
            response = getattr(exc, "response", None)
            if (response is not None and response.status_code in {401, 403}
                    and spec.get("search_fallback", True)):
                reason = source_error(exc)
                http._paper_radar_openreview_notes_denied = reason
                print(f"[OpenReview] {reason}; trying documented public search", flush=True)
                return openreview_search(http, cfg, venue, reason, result.records)
            result.complete = False
            result.failed = True
            result.note = source_error(exc)
            return result
    result.complete = False
    result.note = "OpenReview达到分页上限；不能把当前结果视为完整会集。"
    return result


def xml_text(node: ET.Element | None) -> str:
    return text("".join(node.itertext())) if node is not None else ""

def parse_acl(content: bytes, collection: str, allowed_volumes: list[str]) -> list[dict]:
    if b"<!ENTITY" in content or b"<!DOCTYPE" in content:
        raise ValueError("XML DTD/entity is not supported")
    root = ET.fromstring(content)
    output = []
    for volume in root.findall("volume"):
        vid = volume.attrib.get("id", "")
        if vid not in allowed_volumes:
            continue
        venue = xml_text(volume.find("meta/booktitle")) or f"{collection}.{vid}"
        for paper in volume.findall("paper"):
            paper_id = paper.attrib.get("id", "")
            if not paper_id.isdigit() or int(paper_id) <= 0:
                continue
            authors = [" ".join(filter(None, [xml_text(a.find("first")), xml_text(a.find("last"))])) for a in paper.findall("author")]
            abstract = xml_text(paper.find("abstract"))
            url = f"https://aclanthology.org/{collection}-{vid}.{paper_id}/"
            year = xml_text(volume.find("meta/year")) or collection.split(".")[0]
            # ACL XML dates often have only month/year; do not treat event day as online date.
            month = xml_text(volume.find("meta/month"))
            month_names = {v.casefold(): str(i) for names in [calendar.month_name, calendar.month_abbr] for i, v in enumerate(names) if v}
            month = month_names.get(month.casefold(), month)
            published = year
            if month.isdigit() and 1 <= int(month) <= 12:
                published = f"{year}-{int(month):02d}"
            output.append(make_record(source="ACL Anthology", family="会议", url=url,
                title=xml_text(paper.find("title")), authors=authors, abstract=abstract, published=published,
                status="published", document_type="research_article", venue=venue,
                doi=xml_text(paper.find("doi")), pdf_url=url.rstrip("/") + ".pdf", code=code_links(abstract)))
    return output

def acl(http: HTTP, cfg: dict, collection: str) -> Result:
    root = cfg["sources"]["acl"]["raw_root"].rstrip("/")
    try:
        response = http.get(f"{root}/{collection}.xml")
        return Result(parse_acl(response.content, collection, cfg["sources"]["acl"]["volumes"]))
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return Result(available=False, note="官方集合尚未发布，或需要更新集合路径；未改用非官方投稿列表。")
        raise

def parse_article_html(content: str, url: str, source: str, venue: str, fallback_title: str = "") -> dict:
    soup = BeautifulSoup(content, "html.parser")
    def metas(name: str) -> list[str]:
        return [m.get("content", "") for m in soup.find_all("meta", attrs={"name": name})]
    def meta(name: str) -> str:
        return next(iter(metas(name)), "")
    title = meta("citation_title") or text(soup.select_one("#papertitle, h1")) or fallback_title
    abstract_node = soup.select_one("#abstract, .abstract")
    abstract = abstract_node.get_text(" ", strip=True) if abstract_node else ""
    author_names = metas("citation_author")
    if not author_names:
        author_node = soup.select_one("#authors, .authors")
        author_names = [s.strip() for s in author_node.get_text(" ", strip=True).split(",")] if author_node else []
    pdf_url = meta("citation_pdf_url")
    if not pdf_url:
        pdf_link = next((a for a in soup.find_all("a", href=True) if a["href"].lower().endswith(".pdf")), None)
        pdf_url = urljoin(url, pdf_link["href"]) if pdf_link else ""
    explicit_software = [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                         if any(s in a.get_text(" ", strip=True).lower() for s in ["software", "code", "github"])
                         and any(h in a["href"] for h in ["github.com", "gitlab.com", "codeberg.org"])]
    return make_record(source=source, family="会议", url=url, title=title, authors=author_names,
        abstract=abstract, published=meta("citation_publication_date") or meta("citation_date"),
        status="published", document_type="research_article", venue=meta("citation_conference_title") or venue,
        doi=meta("citation_doi"), pdf_url=pdf_url, code=code_links(abstract) + explicit_software)

def parse_pmlr_index(content: str, base: str) -> list[tuple[str, str, str]]:
    soup = BeautifulSoup(content, "html.parser")
    entries = []
    for paper in soup.select(".paper"):
        title_node = paper.select_one(".title")
        title = title_node.get_text(" ", strip=True) if title_node else ""
        link = next((a for a in paper.find_all("a", href=True) if re.search(r"/v\d+/[^/]+\.html$", urljoin(base, a["href"]))), None)
        if title and link:
            entries.append((title, urljoin(base, link["href"]), ""))
    return list(dict.fromkeys(entries))

def parse_cvf_index(content: str, base: str) -> list[tuple[str, str, str]]:
    soup = BeautifulSoup(content, "html.parser")
    entries = []
    for a in soup.select("dt.ptitle a[href]"):
        title = a.get_text(" ", strip=True)
        url = urljoin(base, a["href"])
        if "/html/" not in url:
            continue
        author_dd = a.find_parent("dt").find_next_sibling("dd")
        authors = author_dd.get_text(" ", strip=True) if author_dd else ""
        entries.append((title, url, authors))
    return list(dict.fromkeys(entries))

def detail_collection(http: HTTP, cfg: dict, entries: list[tuple[str, str, str]], source: str,
                      venue: str, state: dict) -> Result:
    result = Result()
    title_filter = cfg["fetch"].get("title_prefilter", True)
    selected = [e for e in entries if not title_filter or rank(e[0], "", cfg)[0] >= cfg["site"]["minimum_score"]]
    cache = state.setdefault("detail_cache", {})
    # Cache successes per detail page. Daily runs continue the pending queue, not restart it.
    now = now_iso()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    pending = [e for e in selected if e[1] not in cache or cache[e[1]].get("checked", "") < cutoff]
    selected_urls = {e[1] for e in selected}
    result.records.extend(v["record"] for u, v in cache.items() if u in selected_urls)
    pending.sort(key=lambda e: (e[1] in cache, -rank(e[0], "", cfg)[0], e[1]))
    limit = cfg["fetch"]["max_details_per_collection"]
    errors = []
    for title, url, _ in pending[:limit]:
        try:
            content = http.get(url, html_page=True).text
            record = parse_article_html(content, url, source, venue, title)
            if not record["title"]:
                raise ValueError("详情页面标题缺失，可能页面结构已变")
            cache[url] = {"record": record, "checked": now}
            result.records.append(record)
        except Exception as exc:
            errors.append(str(exc)[:100])
    left = max(0, len(pending) - limit) + len(errors)
    result.complete = left == 0
    result.note = f"会集 {len(entries)} 篇；{'标题初筛' if title_filter else '全摘要扫描'} {len(selected)} 篇；待抓详情 {left} 篇。"
    if title_filter:
        result.note += " 仅标题初筛不保证语义召回完整。"
    if errors:
        result.note += " " + " | ".join(errors[:2])
    return result

def pmlr(http: HTTP, cfg: dict, state: dict) -> Result:
    base = "https://proceedings.mlr.press/"
    soup = BeautifulSoup(http.get(base, html_page=True).text, "html.parser")
    year = datetime.now(timezone.utc).year
    years = {str(year + x) for x in cfg["sources"]["pmlr"]["year_offsets"]}
    confs = cfg["sources"]["pmlr"]["conferences"]
    volumes: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if not re.fullmatch(r"https://proceedings\.mlr\.press/v\d+/?", href):
            continue
        parent = a.find_parent("li") or a.parent
        label = parent.get_text(" ", strip=True)
        if any(re.search(r"\b" + re.escape(c) + r"\b", label) for c in confs) and any(y in label for y in years):
            volumes[href] = label
    if not volumes:
        return Result(available=False, note="未发现所选年份/会议的官方PMLR卷；需核对索引或等待出版。")
    combined = Result()
    notes = []
    for url, venue in volumes.items():
        try:
            entries = parse_pmlr_index(http.get(url, html_page=True).text, url)
            if not entries:
                raise ValueError("PMLR卷页未解析到论文，需检查解析器")
            result = detail_collection(http, cfg, entries, "PMLR", venue, state)
            combined.records += result.records
            combined.complete &= result.complete
            notes.append(venue + ": " + result.note)
        except Exception as exc:
            combined.complete = False
            notes.append(venue + ": " + str(exc)[:160])
    combined.note = " | ".join(notes)
    return combined

def cvf(http: HTTP, cfg: dict, collection: str, state: dict) -> Result:
    url = f"https://openaccess.thecvf.com/{collection}?day=all"
    try:
        page = http.get(url, html_page=True)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return Result(available=False, note="官方论文集未上线，或地址需要核对。")
        raise
    entries = parse_cvf_index(page.text, url)
    if not entries:
        return Result(available=False, note="未解析到论文。可能尚未上线，也可能页面结构已改变；不能据此判断没有论文。")
    return detail_collection(http, cfg, entries, "CVF", collection, state)
