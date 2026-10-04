"""Run ONCE on your own laptop (it has internet; the build sandbox does not).

Downloads each link-only source from the corpus manifest ONE time (polite: 1 request per
page, 2-second pause, no crawling), turns it into plain text with a SOURCE/RETRIEVED header,
and saves it to data/supplement/<doc_id>.txt. The pipeline then reads these automatically.

  python3 tools/fetch_links.py            # all link-only sources
  python3 tools/fetch_links.py D032 D033  # only some
Standard library only: no installs needed.
"""
import csv
import html.parser
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "starter", "corpus", "corpus_manifest.csv")
OUT = os.path.join(ROOT, "data", "supplement")
UA = "Mozilla/5.0 (CiteMap hackathon research; one request per page)"


class Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.skip += 1
        if tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr", "section"): self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip: self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip(): self.out.append(d.strip() + " ")


def to_text(body: bytes, ctype: str, path_hint: str) -> str:
    if "pdf" in ctype or path_hint.lower().endswith(".pdf"):
        tmp = os.path.join(OUT, "_tmp.pdf")
        open(tmp, "wb").write(body)
        try:
            return subprocess.run(["pdftotext", "-layout", tmp, "-"], capture_output=True, text=True, timeout=60).stdout
        except Exception:
            return ""
        finally:
            os.remove(tmp)
    p = Text(); p.feed(body.decode("utf-8", errors="replace"))
    t = "".join(p.out)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()


def main(only):
    os.makedirs(OUT, exist_ok=True)
    rows = list(csv.DictReader(open(MANIFEST, newline="", encoding="utf-8")))
    # 'check-terms' rows: the site's terms of use were not cleared by the organisers, so we never download them
    skipped = [r["doc_id"] for r in rows if r["capture"] == "check-terms"]
    if skipped:
        print(f"skipping {len(skipped)} 'check-terms' sources (terms of use not cleared): {', '.join(skipped)}")
    todo = [r for r in rows if r["capture"] not in ("yes", "check-terms") and (not only or r["doc_id"] in only)]
    report = []
    for r in todo:
        url = r["url"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as resp:
                body, ctype = resp.read(), resp.headers.get("Content-Type", "")
            text = to_text(body, ctype, url)
            words = len(text.split())
            if words < 40:
                raise ValueError(f"only {words} words (blocked or script-rendered page)")
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            with open(os.path.join(OUT, r["doc_id"] + ".txt"), "w", encoding="utf-8") as f:
                f.write(f"SOURCE: {url}\nRETRIEVED: {ts}\n\n{text}\n")
            report.append((r["doc_id"], "ok", words, url))
            print(f"OK    {r['doc_id']}  {words:6d} words  {url}")
        except Exception as e:
            report.append((r["doc_id"], "failed", 0, f"{url}  ({e})"))
            print(f"FAIL  {r['doc_id']}  {url}  ({e})")
        time.sleep(2)
    with open(os.path.join(OUT, "_fetch_report.csv"), "w", newline="") as f:
        csv.writer(f).writerows([("doc_id", "status", "words", "url")] + report)
    ok = sum(1 for x in report if x[1] == "ok")
    print(f"\n{ok}/{len(report)} pages saved to data/supplement/. Next: make all")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
