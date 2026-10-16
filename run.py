#!/usr/bin/env python3
"""Run once. A scheduler (GitHub Actions/systemd) invokes this script daily."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import yaml
from radar import collect
from radar.core import Store, load_json, now_iso, save_json

ROOT = Path(__file__).resolve().parent

def build_site(store: Store, cfg: dict, state: dict, preview: bool = False) -> Path:
    papers = []
    for p in store.papers.values():
        if p.get("score", 0) < cfg["site"]["minimum_score"]:
            continue
        if cfg["site"].get("hide_nonresearch", True) and p.get("document_type") == "commentary":
            continue
        papers.append({k: v for k, v in p.items() if k not in {"records", "aliases"}})
    # Keep ordering explicit: record change is discovery time, not publication time.
    papers.sort(key=lambda p: (p.get("record_changed_at", ""), p.get("score", 0)), reverse=True)
    limit = cfg["site"].get("max_display", 12000)
    payload = {
        "meta": {"title": cfg["site"]["title"], "subtitle": cfg["site"]["subtitle"],
                 "timezone": cfg["site"]["timezone"], "generated_at": now_iso(),
                 "mode": "preview" if preview else "live", "displayed": min(len(papers), limit),
                 "total_in_store": len(store.papers), "display_truncated": len(papers) > limit,
                 "last_run": state.get("last_run", {}), "sources": state.get("sources", {})},
        "topics": [{"id": t["id"], "label": t["label"]} for t in cfg["topics"]],
        "papers": papers[:limit]
    }
    # Raw external text is never trusted as HTML/JS. Escape script delimiters in JSON.
    packed = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ROOT / "web/index.html").read_text(encoding="utf-8")
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    html = template.replace("/*__STYLE__*/", css).replace("/*__DATA__*/", "window.PAPER_DATA=" + packed + ";").replace("/*__APP__*/", js)
    out = ROOT / "site"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    save_json(out / "papers.json", payload)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out / "index.html"

def build_jobs(cfg: dict, http: collect.HTTP, state: dict):
    year = datetime.now(timezone.utc).year
    jobs = []
    def since(name: str) -> str:
        prior = state.get("sources", {}).get(name, {}).get("last_success_at")
        if prior:
            return (datetime.fromisoformat(prior) - timedelta(days=cfg["fetch"]["overlap_days"])).isoformat(timespec="seconds")
        return (datetime.now(timezone.utc) - timedelta(days=cfg["fetch"]["bootstrap_days"])).isoformat(timespec="seconds")
    sources = cfg["sources"]
    if sources["arxiv"].get("enabled"):
        jobs.append(("arXiv", lambda: collect.arxiv(http, cfg, since("arXiv"))))
    for journal in sources.get("journals", []):
        if journal.get("enabled", True):
            jobs.append((journal["name"], lambda j=journal: collect.journal(http, cfg, j, since(j["name"]))))
    spec = sources["openreview"]
    if spec.get("enabled"):
        for offset in spec["year_offsets"]:
            for template in spec["venue_templates"]:
                venue = template.format(year=year + offset)
                jobs.append((venue, lambda v=venue: collect.openreview(http, cfg, v)))
    if sources["pmlr"].get("enabled"):
        jobs.append(("PMLR", lambda: collect.pmlr(http, cfg, state)))
    spec = sources["acl"]
    if spec.get("enabled"):
        for offset in spec["year_offsets"]:
            for conference in spec["collections"]:
                collection = f"{year + offset}.{conference}"
                jobs.append((collection, lambda c=collection: collect.acl(http, cfg, c)))
    spec = sources["cvf"]
    if spec.get("enabled"):
        for offset in spec["year_offsets"]:
            for conference in spec["conferences"]:
                if conference == "ICCV" and (year + offset) % 2 == 0:
                    continue
                collection = f"{conference}{year + offset}"
                jobs.append((collection, lambda c=collection: collect.cvf(http, cfg, c, state)))
    return jobs

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="使用可溯源的少量人工样例生成离线预览，不联网、不写实时库")
    parser.add_argument("--build-only", action="store_true", help="只用已保存的库重建页面，不更新抓取时间")
    parser.add_argument("--source", action="append", default=[], help="仅运行名称包含该文本的来源，可重复")
    args = parser.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    overrides = yaml.safe_load((ROOT / "overrides.yaml").read_text(encoding="utf-8")) or []
    state = load_json(ROOT / "data/state.json", {"sources": {}})
    store = Store(load_json(ROOT / "data/store.json", {"papers": []}), cfg)
    if args.preview:
        store = Store({"papers": []}, cfg)
        fixture = load_json(ROOT / "data/preview.json", {"records": []})
        store.ingest(fixture["records"], fixture["checked_at"])
        store.apply_overrides(overrides)
        state = {"sources": {name: {"status": "untested", "note": "已实现适配器；当前环境未完成在线端到端测试。"}
                             for name, _ in build_jobs(cfg, collect.HTTP(cfg), {})},
                 "last_run": {"mode": "preview", "checked_at": fixture["checked_at"]}}
        print("Preview:", build_site(store, cfg, state, preview=True))
        return 0
    if args.build_only:
        for p in store.papers.values():
            store.summarize(p)
        store.apply_overrides(overrides)
        print("Built from stored data:", build_site(store, cfg, state))
        return 0
    http = collect.HTTP(cfg)
    state.setdefault("sources", {})
    started = now_iso()
    errors = added = changed = attempted = 0
    for name, job in build_jobs(cfg, http, state):
        if args.source and not any(s.casefold() in name.casefold() for s in args.source):
            continue
        attempted += 1
        old = state["sources"].get(name, {})
        attempted_at = now_iso()
        print(f"[{name}] collecting...", flush=True)
        try:
            result = job()
            if result.failed:
                errors += 1
            new_count, change_count = store.ingest(result.records, attempted_at)
            added += new_count
            changed += change_count
            status = "ok" if result.complete else "partial"
            if result.failed and not result.records:
                status = "error"
            if not result.available:
                status = "unavailable"
            report = dict(status=status, attempted_at=attempted_at,
                          last_success_at=attempted_at if result.complete and result.available and not result.failed else old.get("last_success_at", ""),
                          fetched=len(result.records), added=new_count, changed=change_count, note=collect.public_note(result.note))
        except Exception as exc:
            errors += 1
            report = dict(status="error", attempted_at=attempted_at, last_success_at=old.get("last_success_at", ""),
                          fetched=0, added=0, changed=0, note=collect.public_note(f"{type(exc).__name__}: {exc}"))
        state["sources"][name] = report
        print(f"[{name}] {report['status']}: {report['fetched']} metadata records. {report['note']}", flush=True)
        # Checkpoints allow a later run to recover progress after interruption.
        save_json(ROOT / "data/store.json", store.payload())
        save_json(ROOT / "data/state.json", state)
    if not attempted:
        print("No sources selected.", file=sys.stderr)
        return 2
    # Re-score old entries too, so changing config takes effect without a re-download.
    for p in store.papers.values():
        store.summarize(p)
    store.apply_overrides(overrides)
    state["last_run"] = {"started_at": started, "finished_at": now_iso(), "attempted_sources": attempted,
                         "failed_sources": errors, "added": added, "changed": changed}
    save_json(ROOT / "data/store.json", store.payload())
    save_json(ROOT / "data/state.json", state)
    print("Site:", build_site(store, cfg, state))
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
