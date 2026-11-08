"""장별 주간 쟁점 요약 — Claude 가 웹을 검색해 지난 7일 쟁점을 장마다 정리한다. site/articles.json 의 "weekly" 칸에 들어간다.

fetch.py 가 매일 부르고, 마지막 요약이 6일 반보다 오래됐을 때만 실제로 돈다(사실상 주 1회). 최근 WEEKLY_RECENT 개 장만.
손으로 돌리기: ANTHROPIC_API_KEY 가 있는 곳에서 python3 scripts/weekly.py --force

- 모델은 WEEKLY_MODEL, 장 하나에 웹 검색 최대 WEEKLY_SEARCHES 번. 출처는 한국 사이트만 보여 준다. 거절되면 대체 모델로 다시 돈다(fallbacks).
- 저장하는 것: 요약 글(모델이 쓴 것)과 출처 제목·주소. 검색 결과 본문이나 인용 구절은 저장하지 않는다.
- 한 장이 실패하면 그 장은 지난주 요약을 그대로 둔다.
- 검색 로봇을 막은 언론사(BLOCKED_OUTLETS)는 웹 검색으로 안 열린다. 대신 매일 모은 그 언론사 기사 제목을
  지시문에 넣고, 쓴 제목은 [번호]로 받아 출처에 붙인다. 제목만 보내고 본문은 보내지 않는다.
"""
import json
import re
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "site" / "articles.json"
CHAPTERS = json.loads((ROOT / "chapters.json").read_text(encoding="utf-8"))["chapters"]
KST = timezone(timedelta(hours=9))

# 11/8 첫 실행(Opus 5.5·검색 5번, 9개 장)이 1회 약 3.76달러, 그중 77%가 검색 결과 읽기(입력)였다.
# 사용자가 「더 싸게」를 골라 시험한 결과: Sonnet 5.5 는 검색 3~5번·low/medium·사이트 제한 유무를 바꿔도
# 장 대부분에서 「기간 안 소식을 못 찾았다」고 답했다(4번 시험, 합계 약 4달러). 그래서 모델은 Opus 5.5 로 두고
# 매주 최근 WEEKLY_RECENT 개 장만 새로 만든다. 지난 장은 마지막 요약을 그대로 둔다(장마다 기간이 따로 붙는다).
WEEKLY_MODEL = "claude-opus-5-5"
WEEKLY_EFFORT = "medium"
WEEKLY_SEARCHES = 5          # 장 하나에 웹 검색 상한(검색 1,000번에 10달러)
WEEKLY_RECENT = 3            # chapters.json 의 마지막 몇 개 장만 매주 새로 만든다
PRICE = {"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.1, 0.5)}  # 100만 토큰당 $(입력, 출력)
# 출처로 보여 줄 한국 사이트(.kr 은 따로 통과). 검색 범위 제한(allowed_domains)에는 쓰지 않는다 —
# 조선·동아·한겨레·중앙·JTBC·KBS·매경·연합은 Anthropic 검색 로봇을 막아 allowed_domains 에 넣으면 400 이 났다(11/8)
KOREAN_SITES = [
    "chosun.com", "donga.com", "joongang.co.kr", "daum.net", "naver.com", "g-enews.com",
    "labortoday.co.kr", "laborplus.co.kr", "worklaw.co.kr", "lawtimes.co.kr",
    "newsis.com", "news1.kr", "newspim.com", "nocutnews.co.kr",
    "khan.co.kr", "ohmynews.com", "pressian.com", "vop.co.kr",
    "imbc.com", "sbs.co.kr", "ytn.co.kr", 
    "hankookilbo.com", "seoul.co.kr",
    "segye.com", "kmib.co.kr", "munhwa.com", "hankyung.com", 
    "sedaily.com", "mt.co.kr", "edaily.co.kr", "fnnews.com", "asiae.co.kr",
    "heraldcorp.com", "news.naver.com",
    "moel.go.kr", "korea.kr", "nlrc.go.kr", "kli.re.kr", "eslc.go.kr",
]
# 이 언론사들은 robots.txt 로 Anthropic 검색 로봇을 막는다. 이름은 articles.json 의 outlet 앞부분과 맞춘다
BLOCKED_OUTLETS = ("연합뉴스", "한겨레", "KBS", "JTBC", "동아일보", "매일경제", "조선일보", "중앙일보")
WEEKLY_HEADLINES = 60        # 장 하나에 넣는 막힌 언론사 제목 상한(최근 것부터). 9장이 한 주 50건 안팎
WEEKLY_EVERY = timedelta(days=6, hours=12)
MAX_CONTINUE = 3             # pause_turn 이어 돌리기 상한
WORKERS = 3                  # 동시에 도는 장 수

SYSTEM = ("너는 대학 「고용관계론」 수업을 듣는 학생의 주간 리서치를 돕는다. "
          "웹 검색으로 한국의 최근 노동·고용 소식을 찾아 수업의 장 하나에 맞춰 짧게 정리한다. "
          "확인한 사실만 쓰고, 한국어로 쓴다.")


def headlines(articles, ch, start):
    """이번 기간에 모은 기사 가운데 이 장이 붙은 막힌 언론사 기사(최근 것부터)."""
    out = []
    for a in articles or []:
        if not a.get("outlet", "").startswith(BLOCKED_OUTLETS):
            continue
        if not any(t.get("ch") == ch["num"] for t in a.get("tags", [])):
            continue
        try:
            if datetime.fromisoformat(a["date"]) < start:
                continue
        except (KeyError, ValueError):
            continue
        out.append(a)
    out.sort(key=lambda a: a["date"], reverse=True)
    return out[:WEEKLY_HEADLINES]


def prompt(ch, start, end, heads=()):
    extra = ""
    if heads:
        rows = "\n".join(f"[{i}] {a['title']} · {a['outlet']} · {a['date'][5:10].replace('-', '/')}"
                         for i, a in enumerate(heads, 1))
        extra = f"""

아래는 이 기간에 모은 기사 제목이다. 웹 검색으로 열리지 않는 언론사라 제목만 있고 본문은 없다.
{rows}
- 쟁점을 고를 때 이 제목들도 함께 본다. 제목에 적힌 것보다 자세한 내용은 웹 검색으로 확인한 것만 쓴다.
- 이 목록의 기사를 쓴 항목은 끝에 [번호]를 붙인다(여럿이면 [2][5])."""
    return f"""기간: {start:%Y년 %m월 %d일}부터 {end:%m월 %d일}까지(지난 7일)
장: {ch['num']}장 {ch['title']}
이 장의 주요 낱말: {', '.join(ch['keywords'][:14])}

이 기간에 한국에서 이 장과 닿는 노동·고용 쟁점을 웹에서 찾아 3~5개로 정리해 줘.
- 항목마다 「- 」로 시작하고, 무슨 일이 있었는지 한두 문장과 이 장의 어떤 개념과 닿는지 한 문장을 쓴다.
- 기간 밖의 일은 넣지 않는다. 기간 안에 찾지 못했으면 그렇다고 한 줄로만 쓴다.
- 머리말이나 맺음말 없이 항목만 쓴다.{extra}"""


def sources_of(content):
    """인용된 글을 먼저, 없으면 검색 결과에서 출처 제목·주소를 모은다(중복 제거, 최대 8개)."""
    seen, out = set(), []

    def add(url, title):
        host = (url or "").split("/")[2].lower() if (url or "").count("/") >= 2 else ""
        korean = host.endswith(".kr") or any(host == d or host.endswith("." + d) for d in KOREAN_SITES)
        if url and korean and url not in seen and len(out) < 8:
            seen.add(url)
            out.append({"title": (title or url)[:120], "url": url})

    for b in content:
        if getattr(b, "type", "") == "text":
            for c in getattr(b, "citations", None) or []:
                add(getattr(c, "url", None), getattr(c, "title", None))
    if not out:
        for b in content:
            if getattr(b, "type", "") == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                for r in b.content:
                    add(getattr(r, "url", None), getattr(r, "title", None))
    return out


MARK = re.compile(r"\s*((?:\[\d+(?:\s*,\s*\d+)*\])+)")


def take_marks(text, heads):
    """본문의 [번호] 표시를 걷어 내고, 가리킨 제목을 출처 dict 목록으로 돌려준다."""
    used = []

    def drop(m):
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        if not nums or not all(1 <= n <= len(heads) for n in nums):
            return m.group(0)          # 목록 번호가 아니면 그대로 둔다
        for n in nums:
            if n not in used:
                used.append(n)
        return ""

    text = MARK.sub(drop, text)
    return text, [{"title": f"{heads[n - 1]['title']} · {heads[n - 1]['outlet']}"[:120], "url": heads[n - 1]["url"]}
                  for n in used]


def one(client, anthropic, ch, start, end, heads=()):
    """장 하나를 요약한다. (장 번호, 결과 dict 또는 None, 쓴 양, 로그 한 줄)"""
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": WEEKLY_SEARCHES}]
    messages = [{"role": "user", "content": prompt(ch, start, end, heads)}]
    used = {"in": 0, "out": 0, "search": 0}
    try:
        for _ in range(MAX_CONTINUE + 1):
            req = dict(model=WEEKLY_MODEL, max_tokens=16000, system=SYSTEM,
                       output_config={"effort": WEEKLY_EFFORT}, tools=tools, messages=messages)
            try:
                resp = client.beta.messages.create(
                    **req, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except TypeError:   # 설치된 SDK 가 fallbacks 를 모르면 대체 모델 없이 보낸다
                resp = client.beta.messages.create(**req)
            u = resp.usage
            used["in"] += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
            used["out"] += u.output_tokens or 0
            stu = getattr(u, "server_tool_use", None)
            used["search"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
            if resp.stop_reason == "pause_turn":
                messages = [messages[0], {"role": "assistant", "content": resp.content}]
                continue
            break
    except anthropic.AuthenticationError:
        return ch["num"], None, used, f"{ch['num']}장 실패(키가 틀림)"
    except anthropic.RateLimitError:
        return ch["num"], None, used, f"{ch['num']}장 실패(요청 한도)"
    except anthropic.APIStatusError as e:
        return ch["num"], None, used, f"{ch['num']}장 실패({e.status_code}: {str(getattr(e, 'message', e))[:300]})"
    except anthropic.APIConnectionError:
        return ch["num"], None, used, f"{ch['num']}장 실패(연결)"
    if resp.stop_reason not in ("end_turn",):
        return ch["num"], None, used, f"{ch['num']}장 건너뜀({resp.stop_reason})"
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
    items = [l for l in text.splitlines() if l.strip().startswith(("- ", "• "))]
    if len(items) < 2:   # 「찾지 못했다」류 답이 지난 요약을 덮지 않게 한다
        return ch["num"], None, used, f"{ch['num']}장 항목 부족(지난 요약 유지): {text[:120]!r}"
    text, from_heads = take_marks(text, heads)
    sources = (from_heads[:4] + sources_of(resp.content))[:10]
    return ch["num"], {"text": text, "sources": sources}, used, f"{ch['num']}장 완료(제목 {len(heads)}건 중 {len(from_heads)}건 씀)"


def maybe_run(now, log, old, articles=None, force=False):
    """old 는 지난 요약(dict). 새로 만들었으면 새 요약을, 아니면 old 를 그대로 돌려준다."""
    old = old or {}
    last = old.get("generated")
    if not force and last and now - datetime.fromisoformat(last) < WEEKLY_EVERY:
        return old
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.append("주간 요약 건너뜀(키 없음)")
        return old
    try:
        import anthropic
    except ImportError:
        log.append("주간 요약 건너뜀(anthropic 미설치)")
        return old
    client = anthropic.Anthropic()
    start, end = now - timedelta(days=7), now
    targets = CHAPTERS[-WEEKLY_RECENT:]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda ch: one(client, anthropic, ch, start, end, headlines(articles, ch, start)), targets))
    chapters = dict(old.get("chapters", {}))
    total = {"in": 0, "out": 0, "search": 0}
    notes, ok = [], 0
    for num, res, used, note in results:
        for k in total:
            total[k] += used[k]
        notes.append(note)
        if res:
            chapters[str(num)] = dict(res, range=f"{start:%m/%d}~{end:%m/%d}", model=WEEKLY_MODEL)
            ok += 1
    # 대략 비용(모델 표값 + 검색 $10/1,000번). 기록용 추정이다
    pin, pout = PRICE.get(WEEKLY_MODEL, (4, 20))
    cost = total["in"] / 1e6 * pin + total["out"] / 1e6 * pout + total["search"] / 1000 * 10
    log.append(f"주간 요약 {ok}/{len(targets)}장 (입력 {total['in']:,} · 출력 {total['out']:,} 토큰 · 검색 {total['search']}번 · 약 ${cost:.2f})")
    if any("완료" not in n for n in notes):
        log.append("주간 요약 문제: " + ", ".join(n for n in notes if "완료" not in n))
    if not ok:
        return old
    return {
        "generated": now.isoformat(timespec="minutes"),
        "range": f"{start:%m/%d}~{end:%m/%d}",
        "model": WEEKLY_MODEL,
        "chapters": chapters,
    }


if __name__ == "__main__":
    data = json.loads(STORE.read_text(encoding="utf-8"))
    log = []
    data["weekly"] = maybe_run(datetime.now(KST), log, data.get("weekly"), data.get("articles"), force="--force" in sys.argv)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")
    print(" · ".join(log) or "할 일 없음(지난 요약이 아직 최근)")
