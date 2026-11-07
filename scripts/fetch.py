#!/usr/bin/env python3
"""고용관계론 기사 스크랩 — 노동 기사 RSS 를 모아 장별로 나누고 site/articles.json 에 쌓는다.

python3 scripts/fetch.py            # 모아서 저장
python3 scripts/fetch.py --dry-run  # 저장하지 않고 몇 건 들어오는지만

- 깃허브 Actions 가 매일 아침 7시(한국)에 돌리고, 바뀐 site/articles.json 을 커밋한다.
  화면(site/index.html)은 그 파일을 깃허브에서 바로 읽으므로 Netlify 는 다시 배포하지 않는다.
- 기사 모으기는 표준 라이브러리만 쓴다. 질문 후보만 anthropic SDK 를 쓰고, Actions 가 설치한다.
- 기사 본문과 RSS 요약은 저장하지 않는다(저작권). 제목·언론사·날짜·링크·장 표시만 남긴다.
  요약은 장을 고르고 질문 후보를 만들 때만 읽고 버린다.
- 장 핵심어와 구글뉴스 검색어는 chapters.json 에서 고친다. 이미 쌓인 기사는 다시 나누지 않는다.
- 질문 후보: 환경변수 ANTHROPIC_API_KEY 가 있을 때만, 최근 AI_DAYS 일 기사 가운데 아직 없는 것에
  Claude(AI_MODEL)로 「몇 장과 닿는지」 한 줄과 토론 질문 셋을 붙인다(기사의 "ai" 칸). 키가 없으면 건너뛴다.
"""
import html
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "site" / "articles.json"
CHAPTERS = json.loads((ROOT / "chapters.json").read_text(encoding="utf-8"))["chapters"]
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
KEEP_DAYS = 45           # 이보다 오래된 기사는 목록에서 뺀다. 하루 120건 안팎이 쌓여 늘리면 화면이 무거워진다

AI_MODEL = "claude-haiku-5-5"   # 질문 후보용 작은 모델(사용자가 받아들인 안, 2026-11-07)
AI_DAYS = 10             # 이보다 오래된 기사에는 질문 후보를 붙이지 않는다
AI_MAX = 200             # 한 번 돌 때 질문 후보를 붙이는 기사 수 상한(비용 상한)
AI_BATCH = 20            # 한 번 요청에 넣는 기사 수
AI_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "link": {"type": "string"},
                    "questions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "link", "questions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}
# 구글뉴스가 한국어로 번역해 끼워 넣는 외국 매체(베트남 노동조합 기사 등). 출처 주소에 이 글자가 있으면 뺀다
BLOCK_SOURCES = (".vn/", ".vn", "vietnam", "laodong", "mezha.net", "indexbox")
MAX_TAGS = 3             # 기사 하나에 붙이는 장 수

# 노동 전문 매체라 기사 전부를 받는다. 장에 안 걸리면 「장 미분류」로 남는다.
FEEDS = [
    ("매일노동뉴스", "https://www.labortoday.co.kr/rss/allArticle.xml"),
    ("참여와혁신", "https://www.laborplus.co.kr/rss/allArticle.xml"),
]


def google_url(q):
    return ("https://news.google.com/rss/search?q=" + quote(f"{q} when:7d")
            + "&hl=ko&gl=KR&ceid=KR:ko")


def get(url):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"})
    with urlopen(req, timeout=20) as r:
        return r.read()


def text_of(el, tag):
    node = el.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    d = None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                d = datetime.strptime(s, fmt)
                break
            except ValueError:
                pass
        if d is None:
            return None
        if d.tzinfo is None:            # 한국 매체 RSS 는 시간대 없이 한국 시각을 준다
            d = d.replace(tzinfo=KST)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(KST)


def title_key(t):
    return re.sub(r"[\W_]+", "", t).lower()


def classify(title, body, hint=None):
    """장마다 걸린 말을 세어 점수가 높은 장부터 MAX_TAGS 개. 제목에 걸리면 2점, 요약에만 걸리면 1점.
    hint 는 구글뉴스 검색어를 건 장 — 본문에서 걸렸을 수 있어 1점을 더하고, 걸린 말이 없으면 검색어를 대신 적는다."""
    hint = hint or {}
    scored = []
    for ch in CHAPTERS:
        hits = [k for k in ch["keywords"] if k in title or k in body]
        score = sum(2 if k in title else 1 for k in hits)
        if ch["num"] in hint:
            score += 1
            if not hits:
                hits = [hint[ch["num"]].replace('"', "")]
        if score:
            hits.sort(key=lambda k: (k not in title, -len(k)))
            scored.append((score, ch["num"], hits[:3]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"ch": n, "kw": kw} for _, n, kw in scored[:MAX_TAGS]]


def items_from(xml_bytes):
    root = ET.fromstring(xml_bytes)
    return root.iter("item")


def collect():
    got, log = [], []
    for outlet, url in FEEDS:
        try:
            n = 0
            for it in items_from(get(url)):
                title = strip_tags(text_of(it, "title"))
                link = text_of(it, "link")
                if not title or not link:
                    continue
                desc = strip_tags(text_of(it, "description"))
                got.append({
                    "title": title, "url": link, "outlet": outlet, "via": "rss",
                    "date": parse_date(text_of(it, "pubDate")),
                    "tags": classify(title, desc),
                    "desc": desc[:400],   # 질문 후보 만들 때만 쓰고 저장하지 않는다
                })
                n += 1
            log.append(f"{outlet} {n}건")
        except Exception as e:  # 한 곳이 막혀도 나머지는 받는다
            log.append(f"{outlet} 실패({type(e).__name__})")
    for ch in CHAPTERS:
        for q in ch.get("queries", []):
            try:
                n = 0
                for it in items_from(get(google_url(q))):
                    title = strip_tags(text_of(it, "title"))
                    src = it.find("source")
                    src_url = (src.get("url") or "").lower() if src is not None else ""
                    if any(b in src_url for b in BLOCK_SOURCES):
                        continue
                    outlet = (src.text or "").strip() if src is not None and src.text else ""
                    outlet = re.sub(r"^https?://|^www\.|/$", "", outlet).removeprefix("www.").rstrip("/")
                    if outlet and title.endswith(" - " + outlet):
                        title = title[: -len(" - " + outlet)].strip()
                    link = text_of(it, "link")
                    if not title or not link:
                        continue
                    got.append({
                        "title": title, "url": link, "outlet": outlet or "구글뉴스", "via": "google",
                        "date": parse_date(text_of(it, "pubDate")),
                        "tags": classify(title, "", {ch["num"]: q}),
                    })
                    n += 1
                log.append(f"구글 「{q}」 {n}건")
            except Exception as e:
                log.append(f"구글 「{q}」 실패({type(e).__name__})")
            time.sleep(0.6)
    return got, log


def merge_tags(a, b):
    out = {t["ch"]: t for t in a}
    for t in b:
        if t["ch"] in out:
            kw = out[t["ch"]]["kw"] + [k for k in t["kw"] if k not in out[t["ch"]]["kw"]]
            out[t["ch"]] = {"ch": t["ch"], "kw": kw[:3]}
        else:
            out[t["ch"]] = t
    order = [t["ch"] for t in a] + [t["ch"] for t in b if t["ch"] not in {x["ch"] for x in a}]
    return [out[c] for c in order][:MAX_TAGS]


def ai_system():
    chapters = "\n".join(f"{c['num']}장 {c['title']}" for c in CHAPTERS)
    return f"""너는 대학 「고용관계론」 수업의 토론 준비를 돕는다. 수업은 다음 장으로 이뤄진다.
{chapters}

학생들은 장마다 그 장과 관련된 최근 신문기사 하나를 골라 질문 세 개를 만들고 스스로 답하는 토론과제를 한다.
여기서 만드는 것은 학생이 기사를 고를 때 참고할 후보이고, 학생이 고쳐 쓰거나 버린다.

기사마다 두 가지를 만든다.
- link: 이 기사가 몇 장의 어떤 개념과 닿는지 한 줄. 예: "9장 · 원청의 사용자성과 하청 교섭"
- questions: 조별 토론에서 다룰 만한 질문 세 개. 한 문장씩 쓰고, 여러 입장이 나올 수 있게 열어 둔다.
  기사에 나온 사실에 기대어 묻고, 답이나 한쪽 입장은 쓰지 않는다.

제목과 짧은 요약만 보고 쓰므로 요약에 없는 사실을 지어내지 않는다. 기사 id 는 받은 그대로 돌려준다. 한국어로 쓴다."""


def annotate(arts, descs, now, log):
    """최근 기사 가운데 질문 후보가 없는 것에 Claude 로 link·질문 셋을 붙인다. 붙인 건수를 돌려준다."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.append("질문 후보 건너뜀(키 없음)")
        return 0
    try:
        import anthropic
    except ImportError:
        log.append("질문 후보 건너뜀(anthropic 미설치)")
        return 0
    cutoff = (now - timedelta(days=AI_DAYS)).isoformat()
    todo = [a for a in arts if "ai" not in a and a["date"] >= cutoff][:AI_MAX]   # arts 는 최신순
    client = anthropic.Anthropic()
    system = ai_system()
    done = 0
    for i in range(0, len(todo), AI_BATCH):
        batch = todo[i:i + AI_BATCH]
        payload = [{"id": a["id"], "title": a["title"], "outlet": a["outlet"], "summary": descs.get(a["id"], "")}
                   for a in batch]
        try:
            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=16000,
                system=system,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
                messages=[{"role": "user", "content": "기사 목록(JSON):\n" + json.dumps(payload, ensure_ascii=False)}],
            )
        except anthropic.AuthenticationError:
            log.append("질문 후보 중단(키가 틀림)")
            break
        except anthropic.RateLimitError:
            log.append("질문 후보 중단(요청 한도)")
            break
        except anthropic.APIStatusError as e:
            log.append(f"질문 후보 묶음 실패({e.status_code})")
            if e.status_code < 500:
                break
            continue
        except anthropic.APIConnectionError:
            log.append("질문 후보 묶음 실패(연결)")
            continue
        if resp.stop_reason != "end_turn":
            log.append(f"질문 후보 묶음 건너뜀({resp.stop_reason})")
            continue
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            items = json.loads(text)["items"]
        except (ValueError, KeyError, TypeError):
            log.append("질문 후보 묶음 해석 실패")
            continue
        by_id = {a["id"]: a for a in batch}
        for it in items:
            a = by_id.get(it.get("id"))
            qs = [q.strip() for q in it.get("questions", []) if q.strip()][:3]
            if a is not None and qs:
                a["ai"] = {"link": it.get("link", "").strip()[:80], "qs": qs}
                done += 1
    log.append(f"질문 후보 {done}건(대상 {len(todo)}건)")
    return done


def main():
    dry = "--dry-run" in sys.argv
    now = datetime.now(KST)
    old = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {"articles": []}
    by_key = {title_key(a["title"]): a for a in old["articles"]}
    descs = {}   # 기사 id → 이번에 받은 RSS 요약. 질문 후보 만들 때만 쓰고 저장하지 않는다

    got, log = collect()
    if not got:
        print("받은 기사가 하나도 없어 저장하지 않습니다:", " · ".join(log))
        sys.exit(1)

    added = 0
    for g in got:
        key = title_key(g["title"])
        if len(key) < 6:
            continue
        date = g["date"] or now
        if date > now + timedelta(hours=1):
            date = now
        if key in by_key:
            a = by_key[key]
            a["tags"] = merge_tags(a["tags"], g["tags"])
            if a["via"] == "google" and g["via"] == "rss":   # 언론사 원문 주소가 있으면 그쪽을 쓴다
                a.update(url=g["url"], outlet=g["outlet"], via="rss")
        else:
            by_key[key] = {
                "id": hashlib.sha1(key.encode()).hexdigest()[:10],
                "title": g["title"], "url": g["url"], "outlet": g["outlet"], "via": g["via"],
                "date": date.isoformat(timespec="minutes"),
                "seen": now.date().isoformat(),
                "tags": g["tags"],
            }
            added += 1
        if g.get("desc"):
            descs[by_key[key]["id"]] = g["desc"]

    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    arts = sorted((a for a in by_key.values() if a["date"] >= cutoff), key=lambda a: a["date"], reverse=True)
    if dry:
        print(" · ".join(log))
        print(f"새 기사 {added}건 · 전체 {len(arts)}건 (질문 후보는 --dry-run 에서 만들지 않음)")
        return
    annotate(arts, descs, now, log)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import weekly   # 장별 주간 쟁점 요약(주 1회만 실제로 돈다)
    weekly_data = weekly.maybe_run(now, log, old.get("weekly"))
    print(" · ".join(log))
    print(f"새 기사 {added}건 · 전체 {len(arts)}건 · 질문 후보 있는 기사 {sum(1 for a in arts if 'ai' in a)}건")
    out = {
        "updated": now.isoformat(timespec="minutes"),
        "chapters": [{"num": c["num"], "title": c["title"]} for c in CHAPTERS],
        "sources": [f for f, _ in FEEDS] + ["구글 뉴스 검색"],
        "articles": arts,
        "weekly": weekly_data,
    }
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(out, ensure_ascii=False, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
