"""사건 묶음·사건 요약·키워드 요약. fetch.py 가 매일 부르고, 결과는 site/articles.json 의 "events"·"keywords" 칸에 들어간다.

1. 묶기: 최근 EVENT_DAYS 일 기사 가운데 아직 사건에 들지 않은 것을 장 순서로 줄 세워 EVENT_CHUNK 건씩 Claude(EVENT_MODEL)에
   보내고, 같은 사건을 다룬 기사를 기존 사건에 붙이거나 2건 이상이면 새 사건으로 묶는다. 한 번 묶인 기사는 다시 묶지 않는다.
2. 요약: 새로 생겼거나 기사가 늘어난 사건만, 원문을 최대 BODY_PER_EVENT 건 받아 2~3문장으로 요약한다.
   같은 요청에서 「노사 입장·숫자 표」(당사자별 요구·제시안, 줄마다 나온 언론사)와 「강의 개념」(chapters.json 의
   장별 concepts 목록에서 0~3개)을 함께 받는다. 표의 숫자가 받은 글에 없으면 그 줄을 버리고, 목록에 없는 개념은 버린다.
   요약 방식이 바뀌면 SUM_VERSION 을 올린다. 그러면 다음 회차에 모든 사건을 한 번 다시 요약한다.
   - 원문 본문은 요약할 때만 읽고 저장하지 않는다. 저장하는 것은 모델이 쓴 요약과 묶인 기사 id 뿐이다.
   - robots.txt 가 이 도구나 Anthropic 로봇(anthropic-ai·ClaudeBot·Claude-User)을 막은 언론사는 본문을 받지 않고 제목만 쓴다.
     연합·한겨레·KBS 가 여기에 든다(2026-11-08 확인).
   - 구글 뉴스 주소는 구글 뉴스 내부 주소 풀기(batchexecute)로 원문 주소를 얻는다. 구글이 바꾸면 깨질 수 있고, 깨지면 제목만으로 요약한다.
     몰아서 부르면 구글이 429 로 막으므로(11/8 시험에서 60번쯤에 막힘) 하나씩 GOOGLE_GAP 초 간격, 한 회차 GOOGLE_MAX 번까지,
     429 가 나면 그 회차는 더 풀지 않는다. 푼 원문 주소는 기사의 "orig" 칸에 적어 다시 풀지 않는다(화면 링크도 이 주소).
2-1. 원문 주소·대표 사진: 최근 RESOLVE_DAYS 일 기사마다 한 번(chk 표시) 원문 주소(orig)를 풀고, robots.txt 가 허락하면
   원문 머리의 og:image(언론사가 링크 미리보기용으로 내놓은 대표 사진) 주소를 img 칸에 적는다. 그림은 저장하지 않고
   화면이 언론사 서버에서 바로 불러온다. 여러 기사에 똑같이 붙는 사진(언론사 기본 그림·로고)은 noimg 로 걸러 낸다.
   사건 요약이 구글 주소 풀기 몫을 먼저 쓰고, 남은 몫과 RESOLVE_SECONDS 초 안에서만 돈다. 키가 없어도 돈다.
3. 키워드: 최근 KW_DAYS 일 기사의 장 낱말을 세어 상위 KW_TOP 개에 「이번 주 무슨 일이 있었나」 2~3문장을 붙인다(매일 새로).
   순위는 키가 없어도 계산하고, 요약만 키가 있을 때 만든다.

손으로 원문 받기만 시험: SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/events.py --probe   (API 를 부르지 않는다)
"""
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "site" / "articles.json"
CHAPTERS = json.loads((ROOT / "chapters.json").read_text(encoding="utf-8"))["chapters"]
CONCEPTS = {c["num"]: c.get("concepts", []) for c in CHAPTERS}   # 장별 강의 개념 [{name, hint}]
KST = timezone(timedelta(hours=9))

EVENT_MODEL = "claude-haiku-5-5"   # 100만 토큰당 입력 0.1달러·출력 0.5달러(지시문 10만 토큰 이하일 때)
EVENT_DAYS = 7           # 이 기간 기사만 묶는다
EVENT_KEEP = 14          # 마지막 기사가 이보다 오래된 사건은 뺀다
EVENT_CHUNK = 250        # 한 번 묶기 요청에 넣는 기사 수
EVENT_SUM_MAX = 120      # 한 번 돌 때 요약하는 사건 수 상한(기사가 많은 사건부터)
EVENT_SUM_BATCH = 5      # 한 번 요약 요청에 넣는 사건 수(표·개념까지 받으면서 8개 묶음이 max_tokens 에 걸려 11/8 5로)
SUM_VERSION = 2          # 2: 노사 입장·숫자 표와 강의 개념을 함께 받는다(2026-11-08)
BODY_PER_EVENT = 3       # 사건 하나에 받아 읽는 원문 수(표에 넣을 숫자가 언론사마다 달라서 2→3)
BODY_TRIES = 5           # 원문을 받으려고 시도하는 기사 수 상한
BODY_CHARS = 2500        # 원문에서 앞부분 몇 자만 넘기나(숫자가 둘째 문단에 많아서 1500→2500)
TABLE_ROWS = 5           # 노사 입장 표 줄 수 상한
GOOGLE_MAX = 100        # 한 회차에 구글 뉴스 주소를 푸는 횟수 상한(11/8 Actions 에서 40번 연속 429 없음 → 대표 사진·원문 링크용으로 100)
GOOGLE_GAP = 1.2        # 구글 뉴스 주소 풀기 사이 간격(초)
RESOLVE_DAYS = 3         # 원문 주소·대표 사진을 찾아 두는 기사 범위
RESOLVE_SECONDS = 150    # 구글 주소 풀기에 쓰는 시간 상한. 사진 찾기는 여기에 90초를 더한 데까지(Actions 제한 20분 안에 끝나게)
BAD_IMG = re.compile(r"logo|default|no_?image|noimg|blank|placeholder", re.I)
KW_DAYS = 7
KW_TOP = 10
KW_MIN = 3               # 이보다 적게 나온 낱말은 순위에 넣지 않는다
# 같은 뜻으로 chapters.json 에 따로 들어간 낱말을 하나로 센다. 화면도 이 표로 거른다
KW_ALIAS = {"산업재해": "산재", "노동조합 설립": "노조 설립", "단체교섭": "교섭"}
PRICE = (0.1, 0.5)

TOOL_UA = "Mozilla/5.0 (compatible; labor-news-scrap/1.0; +https://github.com/hwwwaani/labor-news-scrap)"
# 구글 뉴스 주소 풀기만 브라우저 UA 로 한다(도구 이름으로는 구글이 풀 값을 주지 않는다). fetch.py 의 RSS 받기와 같은 UA
GOOGLE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
# robots.txt 로 막힌 언론사는 구글 주소를 풀지도 않는다(풀기 상한을 아낀다). 회차 안에서 새로 막힌 곳은 _blocked 에 더해진다
SKIP_OUTLETS = ("연합뉴스", "한겨레", "KBS", "JTBC", "동아일보", "매일경제", "조선일보", "중앙일보", "Chosunbiz")
ROBOT_AGENTS = ("labor-news-scrap", "anthropic-ai", "ClaudeBot", "Claude-User")   # 하나라도 막혔으면 받지 않는다

CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "join": {"type": "array", "items": {
            "type": "object",
            "properties": {"article": {"type": "string"}, "event": {"type": "string"}},
            "required": ["article", "event"], "additionalProperties": False}},
        "new": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "articles": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "articles"], "additionalProperties": False}},
    },
    "required": ["join", "new"], "additionalProperties": False,
}
SUM_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "summary": {"type": "string"},
            "parties": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {
                "type": "object",
                "properties": {"issue": {"type": "string"},
                               "cells": {"type": "array", "items": {"type": "string"}},
                               "from": {"type": "string"}},
                "required": ["issue", "cells", "from"], "additionalProperties": False}},
            "concepts": {"type": "array", "items": {
                "type": "object",
                "properties": {"ch": {"type": "integer"}, "name": {"type": "string"}, "why": {"type": "string"}},
                "required": ["ch", "name", "why"], "additionalProperties": False}},
        },
        "required": ["id", "summary", "parties", "rows", "concepts"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}
KW_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"kw": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["kw", "summary"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}

CHAPTER_LIST = "\n".join(f"{c['num']}장 {c['title']}" for c in CHAPTERS)
CLUSTER_SYSTEM = f"""너는 대학 「고용관계론」 수업용 노동 기사 목록을 정리한다. 수업의 장은 다음과 같다.
{CHAPTER_LIST}

받은 기사 가운데 같은 사건을 다룬 것끼리 묶는다.
- 같은 사건은 당사자와 일이 같은 것이다. 예: 「서울교통공사 노조 쟁의행위 가결」과 「서울 지하철 이달 말 파업 가능성」.
  주제만 비슷한 기사(서로 다른 회사의 파업, 서로 다른 지역의 산재)는 묶지 않는다.
- 이미 있는 사건(events)과 같은 일을 다룬 기사는 join 에 그 사건 id 로 붙인다.
- 새 사건(new)은 기사가 2건 이상일 때만 만든다. name 은 누가 무엇을 했는지 15~30자로 쓴다.
- 어느 사건에도 들지 않는 기사는 돌려주지 않는다. 기사 하나는 한 사건에만 넣는다.
- id 는 받은 그대로 쓴다. 한국어로 쓴다."""
SUM_SYSTEM = """너는 대학 「고용관계론」 수업을 듣는 학생에게 노동 사건을 짧게 정리해 준다. 학생은 이것으로 토론과제에 쓸 기사를 고른다.
사건마다 묶인 기사 제목들(titles, 괄호 안은 언론사와 날짜), 받을 수 있었던 원문 앞부분(texts, from 은 언론사와 날짜),
이 사건이 걸린 장의 강의 개념 목록(concepts, 장 번호별 「이름: 설명」)이 온다. texts 가 비어 있으면 제목만 보고 쓴다.
- summary 는 2~3문장이다. 누가, 무엇을, 왜(쟁점)를 쓴다. 날짜나 숫자는 받은 글에 있을 때만 쓴다.
- parties 와 rows 는 노사 입장 표다. parties 는 맞서는 당사자 2~3곳의 짧은 이름이다(예: "노조", "회사", "정부", "원청", "하청노조").
  rows 는 2~5줄이다. issue 는 쟁점 이름 10자 안팎(예: "임금 인상률", "파업 규모", "교섭 의제"),
  cells 는 parties 와 같은 순서로 그 당사자의 요구·입장·제시안을 30자 안팎 한 줄로 쓰고, 숫자가 있으면 숫자를 넣는다.
  그 당사자의 입장이 받은 글에 없으면 그 칸은 빈 문자열이다. from 은 그 줄의 내용이 나온 글의 언론사 이름을 texts 의 from 이나 titles 괄호에서 그대로 옮긴다.
  행사 소식, 통계 발표처럼 맞서는 당사자가 없거나 받은 글에 입장이 없으면 parties 와 rows 를 빈 배열로 둔다. 짐작해서 채우지 않는다.
- concepts 는 이 사건이 수업의 어떤 개념과 닿는지 0~3개다. 받은 concepts 목록의 이름 가운데서만 고르고 name 은 목록 그대로 쓴다.
  ch 는 그 개념이 있던 장 번호, why 는 이 사건의 어느 대목이 그 개념에 해당하는지 40자 안팎이다. 억지로 고르지 않는다.
- 받은 글에 없는 사실, 평가, 전망은 쓰지 않는다. 원문 문장을 길게 옮기지 말고 네 말로 줄인다.
- id 는 받은 그대로 돌려준다. 한국어로 쓴다."""
KW_SYSTEM = """너는 대학 「고용관계론」 수업을 듣는 학생에게 이번 주 노동 소식을 낱말별로 짧게 정리해 준다.
낱말마다 그 낱말이 붙은 사건(이름과 요약)과 사건으로 묶이지 않은 기사 제목이 온다.
- summary 는 2~3문장이다. 이번 주 이 낱말로 어떤 일이 있었는지 큰 사건 이름을 들어 쓴다.
- 받은 글에 없는 사실, 평가, 전망은 쓰지 않는다. kw 는 받은 그대로 돌려준다. 한국어로 쓴다."""


# ---------- 원문 받기 ----------

_robots, _robots_lock = {}, threading.Lock()
_google = {"lock": threading.Lock(), "last": 0.0, "used": 0, "off": False}
_blocked = set()   # 이번 회차에 robots.txt 로 막힌 것을 본 언론사 이름


def _get(url, data=None, headers=None, timeout=12, limit=1_500_000, ua=TOOL_UA):
    req = Request(url, data=data, headers={"User-Agent": ua, **(headers or {})})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        charset = r.headers.get_content_charset()
    if not charset:
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
        charset = m.group(1).decode() if m else "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def robots_ok(url):
    """이 도구와 Anthropic 로봇 모두 robots.txt 로 허락된 주소인가. robots.txt 를 못 읽으면(404 제외) 받지 않는다."""
    host = urlparse(url).netloc.lower()
    with _robots_lock:
        rp = _robots.get(host, "?")
    if rp == "?":
        rp = RobotFileParser()
        try:
            rp.parse(_get(f"{urlparse(url).scheme}://{host}/robots.txt", timeout=8).splitlines())
        except HTTPError as e:
            rp = True if e.code == 404 else None
        except Exception:
            rp = None
        with _robots_lock:
            _robots[host] = rp
    if rp is True:
        return True
    if rp is None:
        return False
    return all(rp.can_fetch(agent, url) for agent in ROBOT_AGENTS)


def decode_google(gurl):
    """구글 뉴스 RSS 주소(news.google.com/rss/articles/…)를 원문 주소로 바꾼다. 못 하면 None. 한 번에 하나씩만 부른다."""
    with _google["lock"]:
        if _google["off"] or _google["used"] >= GOOGLE_MAX:
            return None
        wait = _google["last"] + GOOGLE_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _google["used"] += 1
        try:
            return _decode(gurl)
        except HTTPError as e:
            if e.code == 429:
                _google["off"] = True
            return None
        except Exception:
            return None
        finally:
            _google["last"] = time.time()


def _decode(gurl):
    gid = urlparse(gurl).path.rsplit("/", 1)[-1]
    page = _get(f"https://news.google.com/articles/{gid}", timeout=8, ua=GOOGLE_UA)
    sg = re.search(r'data-n-a-sg="([^"]+)"', page).group(1)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page).group(1)
    inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                            "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], gid, int(ts), sg]
    freq = json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])
    body = _get("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                data=urlencode({"f.req": freq}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=8, ua=GOOGLE_UA)
    url = json.loads(json.loads(body.split("\n\n", 1)[1])[0][2])[1]
    return url if isinstance(url, str) and url.startswith("http") else None


def page_text(page):
    """기사 페이지에서 본문으로 보이는 글을 뽑는다(문단 <p> 위주, 없으면 본문 칸)."""
    page = re.sub(r"(?is)<(script|style|noscript|header|footer|nav|aside|figure)[^>]*>.*?</\1>", " ", page)
    paras = [html.unescape(re.sub(r"<[^>]+>", "", p)).strip() for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)]
    text = " ".join(p for p in paras if len(p) > 40)
    if len(text) < 200:
        m = re.search(r'(?is)<(div|article|section)[^>]+(?:id|class)="[^"]*(article|news_body|newsct|content|view|body)[^"]*"[^>]*>(.*?)</\1>', page)
        if m:
            text = html.unescape(re.sub(r"<[^>]+>", " ", m.group(3)))
    return re.sub(r"\s+", " ", text).strip()


def page_image(page, url):
    """기사 머리의 og:image 주소. 없거나 로고·기본 그림처럼 보이면 None."""
    for tag in re.findall(r"<meta\b[^>]*>", page[:200_000], re.I):
        if re.search(r"""(?:property|name)\s*=\s*["']og:image(?::url|:secure_url)?["']""", tag, re.I):
            m = re.search(r"""content\s*=\s*["']([^"']+)["']""", tag, re.I)
            if m:
                img = urljoin(url, html.unescape(m.group(1)).strip())
                if img.startswith("http") and len(img) < 600 and not BAD_IMG.search(img):
                    return img
    return None


def bodies_for(event, by_id, descs):
    """사건 하나에서 원문 앞부분을 최대 BODY_PER_EVENT 개 받는다. 언론사 RSS 기사(주소가 바로 원문)를 먼저 본다."""
    members = sorted((by_id[i] for i in event["ids"] if i in by_id),
                     key=lambda a: (a["via"] != "rss", -datetime.fromisoformat(a["date"]).timestamp()))
    texts, seen_outlets, tries = [], set(), 0
    for a in members:
        if len(texts) >= BODY_PER_EVENT or tries >= BODY_TRIES:
            break
        if a["outlet"] in seen_outlets or a["outlet"] in _blocked or a["outlet"].startswith(SKIP_OUTLETS):
            continue
        tries += 1
        url = a["url"] if a["via"] == "rss" else a.get("orig") or decode_google(a["url"])
        if url and a["via"] != "rss":
            a["orig"] = url
        if not url:
            continue
        if not robots_ok(url):
            _blocked.add(a["outlet"])
            continue
        try:
            page = _get(url)
        except Exception:
            continue
        a["chk"] = 1
        img = page_image(page, url)
        if img:
            a["img"] = img
        text = page_text(page)
        if len(text) >= 200:
            texts.append({"from": f"{a['outlet']} {a['date'][5:10]}", "text": text[:BODY_CHARS]})
            seen_outlets.add(a["outlet"])
        time.sleep(0.3)
    if not texts:   # 원문을 하나도 못 받았으면 이번에 받은 RSS 요약이라도
        texts = [{"from": f"{by_id[i]['outlet']} {by_id[i]['date'][5:10]}", "text": descs[i]}
                 for i in event["ids"] if descs.get(i) and i in by_id][:BODY_PER_EVENT]
    return texts


# ---------- Claude 부르기 ----------

class Stop(Exception):
    """키가 틀렸거나 요청 한도에 걸려 이번 회차는 더 부르지 않는다."""


def ask(client, anthropic, system, payload, schema, effort, used, log, what):
    try:
        resp = client.messages.create(
            model=EVENT_MODEL, max_tokens=16000, system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.AuthenticationError:
        log.append(f"{what} 중단(키가 틀림)")
        raise Stop
    except anthropic.RateLimitError:
        log.append(f"{what} 중단(요청 한도)")
        raise Stop
    except anthropic.APIStatusError as e:
        log.append(f"{what} 실패({e.status_code})")
        if e.status_code < 500:
            raise Stop
        return None
    except anthropic.APIConnectionError:
        log.append(f"{what} 실패(연결)")
        return None
    used["in"] += resp.usage.input_tokens or 0
    used["out"] += resp.usage.output_tokens or 0
    if resp.stop_reason != "end_turn":
        log.append(f"{what} 건너뜀({resp.stop_reason})")
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        log.append(f"{what} 해석 실패")
        return None


# ---------- 사건 ----------

def refresh(event, by_id):
    """묶인 기사로 날짜·장·건수를 다시 적는다. 목록에서 빠진 기사는 버린다."""
    members = sorted((by_id[i] for i in dict.fromkeys(event["ids"]) if i in by_id), key=lambda a: a["date"])
    event["ids"] = [a["id"] for a in members]
    if members:
        event["first"], event["last"] = members[0]["date"], members[-1]["date"]
        chs = Counter(t["ch"] for a in members for t in a.get("tags", []))
        event["chs"] = [c for c, _ in chs.most_common(3)]
    return event


def cluster(client, anthropic, arts, events, now, used, log):
    by_id = {a["id"]: a for a in arts}
    taken = {i for e in events for i in e["ids"]}
    cutoff = (now - timedelta(days=EVENT_DAYS)).isoformat()
    todo = [a for a in arts if a["id"] not in taken and a["date"] >= cutoff]
    todo.sort(key=lambda a: (a["tags"][0]["ch"] if a.get("tags") else 99, a["date"]))
    joined = made = 0
    for i in range(0, len(todo), EVENT_CHUNK):
        chunk = todo[i:i + EVENT_CHUNK]
        recent = {e["id"]: e for e in events if e.get("last", "") >= cutoff}
        payload = {
            "events": [{"id": e["id"], "name": e["name"]} for e in recent.values()],
            "articles": [{"id": a["id"], "title": a["title"], "outlet": a["outlet"], "date": a["date"][:10]} for a in chunk],
        }
        data = ask(client, anthropic, CLUSTER_SYSTEM, payload, CLUSTER_SCHEMA, "medium", used, log, "사건 묶기")
        if not data:
            continue
        free, done = {a["id"] for a in chunk}, set()
        for j in data.get("join", []):
            aid, e = j.get("article"), recent.get(j.get("event"))
            if aid in free and aid not in done and e is not None:
                e["ids"].append(aid)
                done.add(aid)
                refresh(e, by_id)
                joined += 1
        for n in data.get("new", []):
            ids = [x for x in dict.fromkeys(n.get("articles", [])) if x in free and x not in done]
            name = (n.get("name") or "").strip()
            if len(ids) < 2 or not name:
                continue
            done.update(ids)
            e = refresh({"id": "e" + hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:9],
                         "name": name[:40], "ids": ids, "summary": "", "sumN": 0}, by_id)
            events.append(e)
            made += 1
    log.append(f"사건 묶기: 대상 {len(todo)}건 → 기존 사건에 {joined}건, 새 사건 {made}개")


NUM = re.compile(r"\d+(?:[.,]\d+)*")


def clean_table(it, corpus, outlets):
    """모델이 준 노사 입장 표를 거른다. 숫자가 받은 글에 없는 줄은 버린다. (표 또는 None, 버린 줄 수)"""
    parties = [p.strip()[:20] for p in it.get("parties") or [] if p and p.strip()]
    if not 2 <= len(parties) <= 3:
        return None, 0
    flat = corpus.replace(",", "")
    rows, dropped = [], 0
    for r in it.get("rows") or []:
        cells = [(c or "").strip()[:80] for c in r.get("cells") or []]
        issue = (r.get("issue") or "").strip()[:24]
        if len(cells) != len(parties) or not issue or sum(1 for c in cells if c) < 1:
            continue
        nums = [n.replace(",", "") for c in cells for n in NUM.findall(c)]
        if any(n not in flat for n in nums):
            dropped += 1
            continue
        src = (r.get("from") or "").strip()
        src = next((o for o in outlets if o and o in src), "")
        rows.append({"issue": issue, "cells": cells, "from": src})
        if len(rows) >= TABLE_ROWS:
            break
    if not rows:
        return None, dropped
    return {"parties": parties, "rows": rows}, dropped


def clean_concepts(items, chs):
    """목록에 있는 개념만 남긴다(장 번호와 이름이 맞아야 한다). 최대 3개."""
    out, seen = [], set()
    for c in items:
        ch, name = c.get("ch"), (c.get("name") or "").strip()
        names = {k["name"] for k in CONCEPTS.get(ch, [])}
        if ch in chs and name in names and (ch, name) not in seen:
            seen.add((ch, name))
            out.append({"ch": ch, "name": name, "why": (c.get("why") or "").strip()[:80]})
    return out[:3]


def summarize(client, anthropic, events, by_id, descs, used, log):
    first = sorted((e for e in events if e.get("sumN") != len(e["ids"]) or e.get("sv") != SUM_VERSION),
                   key=lambda e: -len(e["ids"]))
    # 원문 없이 제목만으로 요약된 사건은 한 번 더 원문을 시도한다(첫날은 구글 주소 풀기 상한에 걸리므로)
    again = sorted((e for e in events if e.get("sumN") == len(e["ids"]) and e.get("sv") == SUM_VERSION
                    and e.get("src") == 0 and not e.get("again")),
                   key=lambda e: -len(e["ids"]))
    for e in again:
        e["again"] = True
    todo = (first + again)[:EVENT_SUM_MAX]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=6) as pool:
        texts = dict(zip((e["id"] for e in todo), pool.map(lambda e: bodies_for(e, by_id, descs), todo)))
    got = sum(1 for e in todo if texts[e["id"]])
    done = tables = bad_rows = 0
    for i in range(0, len(todo), EVENT_SUM_BATCH):
        batch = todo[i:i + EVENT_SUM_BATCH]
        payload = [{
            "id": e["id"], "name": e["name"],
            "titles": [f"{by_id[x]['title']} ({by_id[x]['outlet']}, {by_id[x]['date'][5:10]})" for x in e["ids"][:15]],
            "texts": texts[e["id"]],
            "concepts": {str(c): [f"{k['name']}: {k['hint']}" for k in CONCEPTS.get(c, [])] for c in e.get("chs", [])},
        } for e in batch]
        data = ask(client, anthropic, SUM_SYSTEM, payload, SUM_SCHEMA, "medium", used, log, "사건 요약")
        if not data:
            continue
        by_ev = {e["id"]: e for e in batch}
        for it in data.get("items", []):
            e, s = by_ev.get(it.get("id")), (it.get("summary") or "").strip()
            if e is not None and s:
                e.update(summary=s[:500], sumN=len(e["ids"]), src=len(texts[e["id"]]), sv=SUM_VERSION)
                corpus = " ".join([x["text"] for x in texts[e["id"]]] + [by_id[x]["title"] for x in e["ids"] if x in by_id])
                outlets = {by_id[x]["outlet"] for x in e["ids"] if x in by_id}
                table, dropped = clean_table(it, corpus, outlets)
                e.pop("table", None)
                if table:
                    e["table"] = table
                e["concepts"] = clean_concepts(it.get("concepts") or [], e.get("chs", []))
                tables += bool(table)
                bad_rows += dropped
                done += 1
    log.append(f"사건 요약 {done}개(원문을 받은 사건 {got}/{len(todo)} · 노사 입장 표 {tables}개, 숫자가 원문에 없어 버린 줄 {bad_rows})")


# ---------- 키워드 ----------

def kws_of(a):
    return {KW_ALIAS.get(k, k) for t in a.get("tags", []) for k in t.get("kw", [])}


def keywords(client, anthropic, arts, events, now, old, used, log):
    cutoff = (now - timedelta(days=KW_DAYS)).isoformat()
    recent = [a for a in arts if a["date"] >= cutoff]
    count = Counter(k for a in recent for k in kws_of(a))
    old_sum = {it["kw"]: it.get("summary", "") for it in (old or {}).get("items", [])}
    items = [{"kw": k, "n": n, "summary": old_sum.get(k, "")} for k, n in count.most_common(KW_TOP) if n >= KW_MIN]
    if client and items:
        ev_of = {i: e for e in events for i in e["ids"]}
        payload = []
        for it in items:
            mem = [a for a in recent if it["kw"] in kws_of(a)]
            # 낱말이 붙은 기사가 사건 안에 2건 이상, 사건의 30% 이상일 때만 그 낱말의 사건으로 본다
            # (11/8 첫 회에 「산재」 요약에 기사 한 건만 걸친 고용보험 부정수급 사건이 섞였다)
            hits = Counter(ev_of[a["id"]]["id"] for a in mem if a["id"] in ev_of)
            evd = {e["id"]: e for e in events}
            evs = [evd[x] for x, c in hits.most_common() if c >= 2 and c >= 0.3 * len(evd[x]["ids"])]
            in_evs = {i for e in evs for i in e["ids"]}
            payload.append({
                "kw": it["kw"], "count": it["n"],
                "events": [{"name": e["name"], "summary": e.get("summary", ""), "articles": len(e["ids"])} for e in evs[:8]],
                "other_titles": [a["title"] for a in mem if a["id"] not in in_evs][:20],
            })
        data = ask(client, anthropic, KW_SYSTEM, payload, KW_SCHEMA, "low", used, log, "키워드 요약")
        if data:
            got = {r.get("kw"): (r.get("summary") or "").strip() for r in data.get("items", [])}
            for it in items:
                if got.get(it["kw"]):
                    it["summary"] = got[it["kw"]][:500]
    return {"generated": now.isoformat(timespec="minutes"),
            "range": f"{now - timedelta(days=KW_DAYS):%m/%d}~{now:%m/%d}",
            "alias": KW_ALIAS, "items": items}


# ---------- 원문 주소·대표 사진 ----------

def resolve(arts, now, log):
    """최근 기사의 원문 주소(orig)와 대표 사진(img)을 찾아 둔다. 기사마다 한 번만 시도하고 chk 로 표시한다.
    1) 구글 뉴스 주소는 하나씩 푼다(구글이 몰아서 부르면 막는다). 2) 대표 사진은 여러 언론사를 동시에 본다."""
    t0 = time.time()
    cutoff = (now - timedelta(days=RESOLVE_DAYS)).isoformat()
    todo = sorted((a for a in arts if a["date"] >= cutoff and not a.get("chk")), key=lambda a: a["date"], reverse=True)
    links = 0
    for a in todo:
        if a["via"] == "rss" or a.get("orig"):
            continue
        if time.time() - t0 > RESOLVE_SECONDS or _google["off"] or _google["used"] >= GOOGLE_MAX:
            break                   # 못 푼 기사는 chk 없이 남겨 다음 회차에 다시
        url = decode_google(a["url"])
        if url:
            a["orig"] = url
            links += 1
        elif not _google["off"]:
            a["chk"] = 1            # 이 기사만 안 풀리는 것이면 다시 시도하지 않는다

    def photo(a):
        if time.time() - t0 > RESOLVE_SECONDS + 90:
            return 0                # 시간이 다 되면 chk 없이 남겨 다음 회차에
        url = a["url"] if a["via"] == "rss" else a["orig"]
        a["chk"] = 1
        if a["outlet"] in _blocked or a["outlet"].startswith(SKIP_OUTLETS):
            return 0
        if not robots_ok(url):
            _blocked.add(a["outlet"])
            return 0
        try:
            img = page_image(_get(url, limit=300_000, timeout=8), url)
        except Exception:
            return 0
        if img:
            a["img"] = img
            return 1
        return 0

    cand = [a for a in todo if not a.get("chk") and (a["via"] == "rss" or a.get("orig"))]
    with ThreadPoolExecutor(max_workers=6) as pool:
        imgs = sum(pool.map(photo, cand))
    left = sum(1 for a in todo if not a.get("chk"))
    log.append(f"원문 주소 {links}건·대표 사진 {imgs}건 찾음(대상 {len(todo)}건, 남은 {left}건{', 구글 429' if _google['off'] else ''}, {time.time() - t0:.0f}초)")


def drop_shared_images(arts, noimg):
    """두 기사 이상에 똑같이 붙은 사진은 언론사 기본 그림으로 보고 뺀다. 뺀 주소는 noimg 에 쌓아 다음에도 뺀다."""
    count = Counter(a["img"] for a in arts if a.get("img"))
    noimg = set(noimg) | {u for u, n in count.items() if n >= 2}
    for a in arts:
        if a.get("img") in noimg:
            del a["img"]
    return sorted(noimg)[-300:]


# ---------- 한 회차 ----------

def run(now, arts, descs, old_events, old_keywords, log):
    """(events 칸, keywords 칸)을 돌려준다. 키가 없거나 실패하면 지난 사건을 그대로 두고 키워드 순위만 새로 센다."""
    by_id = {a["id"]: a for a in arts}
    keep = (now - timedelta(days=EVENT_KEEP)).isoformat()
    events = [refresh(dict(e, ids=list(e["ids"])), by_id) for e in (old_events or {}).get("items", [])]
    events = [e for e in events if len(e["ids"]) >= 2 and e.get("last", "") >= keep]
    client = anthropic = None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.append("사건·키워드 요약 건너뜀(키 없음)")
    else:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except ImportError:
            log.append("사건·키워드 요약 건너뜀(anthropic 미설치)")
    used = {"in": 0, "out": 0}
    kw = None
    try:
        if client:
            cluster(client, anthropic, arts, events, now, used, log)
            summarize(client, anthropic, events, by_id, descs, used, log)
        kw = keywords(client, anthropic, arts, events, now, old_keywords, used, log)
    except Stop:
        pass
    resolve(arts, now, log)
    noimg = drop_shared_images(arts, (old_events or {}).get("noimg", []))
    if kw is None:
        kw = keywords(None, None, arts, events, now, old_keywords, used, log)
    if client:
        cost = used["in"] / 1e6 * PRICE[0] + used["out"] / 1e6 * PRICE[1]
        log.append(f"사건·키워드 (입력 {used['in']:,} · 출력 {used['out']:,} 토큰 · 약 ${cost:.3f})")
    events.sort(key=lambda e: e.get("last", ""), reverse=True)
    return {"generated": now.isoformat(timespec="minutes"), "items": events, "noimg": noimg}, kw


if __name__ == "__main__" and "--probe" in sys.argv:
    # API 없이 원문 받기만 시험한다: 같은 날 같은 장 낱말이 붙은 기사 둘씩을 가짜 사건으로 묶어 본다
    data = json.loads(STORE.read_text(encoding="utf-8"))
    arts = data["articles"]
    by_id = {a["id"]: a for a in arts}
    fake = [{"id": f"p{i}", "ids": [arts[i]["id"], arts[i + 1]["id"]]} for i in range(0, 24, 2)]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(lambda e: bodies_for(e, by_id, {}), fake))
    for e, r in zip(fake, res):
        print(len(r), [len(x["text"]) for x in r], " / ".join(f"{by_id[i]['outlet']}" for i in e["ids"]))
    print(f"원문을 하나라도 받은 사건 {sum(1 for r in res if r)}/{len(fake)} · {time.time() - t0:.0f}초")
    print("막힌 곳:", sorted(h for h, rp in _robots.items() if rp is None or (rp is not True and not all(rp.can_fetch(a, f'https://{h}/') for a in ROBOT_AGENTS))))
