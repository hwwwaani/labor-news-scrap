# 고용관계론 기사 스크랩

고용관계론 1~9장과 닿는 노동 기사를 매일 아침 모아 장별로 보여 주는 페이지입니다.
장마다 있는 자율 선택 토론과제(최근 기사 하나를 골라 질문 셋에 답하기)의 후보를 고를 때 씁니다.
사이트는 후보만 보여 주고, 어떤 기사를 쓸지와 입장은 사람이 정합니다.

- 주소: https://labor-news-scrap.netlify.app (장을 골라 공유하려면 `…/#ch=8` 처럼 붙입니다)
- 저장소: https://github.com/hwwwaani/labor-news-scrap (공개. 화면이 목록 파일을 여기서 읽어서 공개여야 합니다)
  hwwwaan 이 아니라 hwwwaani 계정입니다. 크롬 깃허브가 hwwwaani 로 로그인돼 있어 실행 설정 파일을 웹으로 올리려고 옮겼습니다(2026-11-07 사용자 결정).
- Netlify: 개인 계정(dlwnghks9456)의 `labor-news-scrap`, 깃허브와 잇지 않고 CLI 로만 올립니다

## 어떻게 돌아가나요

1. 깃허브 Actions 가 매일 07:00(한국 시각)에 `scripts/fetch.py` 를 돌립니다.
2. 매일노동뉴스·참여와혁신 RSS 전체와, 장마다 정한 검색어로 구글 뉴스 최근 7일치를 받습니다.
3. 제목이 같은 기사는 하나로 합치고, 제목·요약에 나온 낱말로 장을 최대 셋까지 붙여 `site/articles.json` 에 쌓습니다.
4. 최근 10일 기사 가운데 아직 없는 것에 Claude(Haiku 5.5)로 「몇 장과 닿는지」 한 줄과 토론 질문 셋을 붙입니다(한 번에 최대 200건).
5. 최근 7일 기사 가운데 같은 사건을 다룬 것을 Claude(Haiku 5.5)가 묶어 「사건」으로 만들고, 새로 생기거나 기사가 늘어난 사건마다 원문 앞부분을 최대 2건 받아 2~3문장으로 요약합니다. 원문 본문은 요약할 때만 읽고 저장하지 않습니다. robots.txt 로 이 도구나 Anthropic 로봇을 막은 언론사(연합·한겨레·KBS·조선비즈 등)는 원문을 받지 않고 제목만 씁니다. 이어서 최근 7일 장 낱말을 세어 상위 10개에 「이번 주 무슨 일이 있었나」 2~3문장을 붙입니다. `scripts/events.py`, 결과는 `articles.json` 의 `events`·`keywords` 칸.
6. 지난 요약이 6일 반보다 오래됐으면(사실상 주 1회) Claude(Opus 5.5)가 웹을 검색해 **최근 3개 장**의 「이번 주 쟁점」을 3~5개씩 정리하고 한국 사이트 출처를 붙입니다. 지난 장은 마지막 요약을 그대로 두고, 장마다 기간이 따로 표시됩니다. 항목이 둘 미만인 답(「못 찾았다」류)은 버리고 지난 요약을 지킵니다. 검색 로봇을 막은 언론사(연합·한겨레·KBS·JTBC·동아·매경·조선·중앙)는 웹 검색으로 안 열리므로, 그 주에 모은 그 언론사 기사 제목을 함께 넘기고 쓴 제목은 출처에 붙입니다(본문은 넘기지 않습니다). `scripts/weekly.py`, 결과는 `articles.json` 의 `weekly` 칸.
7. 바뀐 목록을 저장소에 커밋합니다.
8. Netlify 에 올린 화면(`site/index.html`)은 그 파일을 깃허브에서 바로 읽습니다.

**Netlify 는 매일 다시 배포하지 않습니다.** 이 계정은 크레딧 요금제(월 1,000)라 배포할 때마다 크레딧이 빠지고,
다른 사이트들과 같은 크레딧을 씁니다. 그래서 화면은 한 번만 올리고, 매일 바뀌는 목록만 깃허브에서 읽게 했습니다.

## 무엇을 저장하나요

제목·언론사·날짜·원문 링크·장 표시와 AI 질문 후보만 저장합니다. 기사 본문과 RSS 요약은 장을 고르고 질문을 만들 때만 읽고 버립니다(저작권).
45일보다 오래된 기사는 목록에서 빠집니다(하루 120건 안팎이 쌓여서). 페이지는 검색엔진에 잡히지 않게 막아 두었고, 링크를 아는 사람만 봅니다.

## 고치고 싶을 때

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 장 낱말·검색어 바꾸기, 10장 덧붙이기 | `chapters.json` (같은 모양으로 한 덩어리 추가) |
| 외국 매체가 섞여 들어올 때 | `scripts/fetch.py` 의 `BLOCK_SOURCES` |
| 언론사 RSS 늘리기 | `scripts/fetch.py` 의 `FEEDS` |
| 오래 남기는 기간 | `scripts/fetch.py` 의 `KEEP_DAYS` |
| 주간 쟁점 모델·검색 수·주기 | `scripts/weekly.py` 의 `WEEKLY_MODEL`·`WEEKLY_RECENT`·`WEEKLY_SEARCHES`·`WEEKLY_EVERY`, 지시문은 `prompt()` |
| 질문 후보 모델·건수·대상 기간 | `scripts/fetch.py` 의 `AI_MODEL`·`AI_MAX`·`AI_DAYS`, 지시문은 `ai_system()` |
| 사건 묶기·사건 요약·낱말 요약 | `scripts/events.py` 의 `EVENT_*`·`BODY_*`·`GOOGLE_*`·`KW_*`, 같은 뜻 낱말 합치기는 `KW_ALIAS`, 지시문은 `CLUSTER_SYSTEM`·`SUM_SYSTEM`·`KW_SYSTEM` |
| 화면 모양 | `site/index.html` → 고친 뒤 아래 「화면 다시 올리기」 |

`chapters.json` 만 고치면 다음 날 아침부터 새 기준이 붙습니다. 이미 쌓인 기사는 다시 나누지 않습니다.

## 질문 후보와 API 키

- 키는 저장소 비밀값 `ANTHROPIC_API_KEY` 에 있습니다. 개인 Claude 콘솔(「Juhwan's Individual Org」)에서 사용자가 만든 `labor-news-scrap` 키입니다.
- 키가 없거나 만료되면 질문 후보만 건너뛰고 기사 모으기는 그대로 돕니다. Actions 기록에 「질문 후보 건너뜀」이나 「키가 틀림」이 찍힙니다.
- 키를 바꿀 때는 콘솔에서 새 키를 만들어 저장소 Settings › Secrets and variables › Actions 의 같은 이름 비밀값을 고칩니다.
- **비용은 Actions 기록에 찍힙니다.** 「주간 요약 3/3장 (… 약 $1.25)」처럼 나옵니다. 9개 장을 다 만든 첫 실행이 약 3.76달러였고, 최근 3개 장만이면 주 1.25달러 안팎(한 달 5달러 안팎)으로 봅니다(3장 실행은 11/14 첫 회에서 실측). 질문 후보는 그보다 훨씬 적습니다. 사건·낱말 요약은 「사건·키워드 (… 약 $0.0xx)」 줄에 찍히고, 첫 회(사건 94개 새로 묶음)가 0.048달러, 둘째 회가 0.026달러였습니다(11/8). 평소에는 하루 2센트 안팎으로 봅니다.
- **Sonnet 5.5 로는 안 됐습니다**(11/8, 시험 4번 약 4달러). 검색 수·노력 수준·사이트 제한을 바꿔도 장 대부분에서 「기간 안 소식을 못 찾았다」고 답했습니다. 또 조선·동아·한겨레·중앙·JTBC·KBS·매경·연합은 검색 로봇을 막아 `allowed_domains` 에 넣으면 400 이 납니다. 이 언론사 기사는 구글 뉴스로 매일 모이므로 그 제목을 요약 지시문에 넣는 방식으로 메웠습니다(`weekly.py` 의 `BLOCKED_OUTLETS`·`WEEKLY_HEADLINES`). 다른 언론사가 막혀 있으면 `BLOCKED_OUTLETS` 에 이름을 더합니다.
- 주간 요약을 지금 한 번 더 돌리려면 키가 있는 곳에서 `python3 scripts/weekly.py --force` 를 돌리거나, 저장소의 `weekly.generated` 를 지우고 Actions 를 돌립니다.
- 질문 후보는 제목과 RSS 요약만 보고 만든 참고용입니다. 화면 아래에 「원문 확인, 과제에 쓰면 AI 활용 내역에 밝히기」 안내를 달아 두었습니다.

## 맥에서 직접 돌려 보기

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/fetch.py --dry-run
```

이 맥의 python.org 파이썬에는 인증서 묶음이 없어서 `SSL_CERT_FILE` 를 붙여야 합니다. 깃허브 서버에서는 필요 없습니다.
`--dry-run` 을 빼면 `site/articles.json` 에 저장합니다. 저장한 것을 푸시하면 Actions 가 다음에 그 위에 이어 쌓습니다.

## 고친 것을 깃허브에 올리기

Actions 가 매일 아침 목록을 커밋하므로 **먼저 받아 온 뒤** 올립니다. 이 맥의 gh 기본 계정은 hwwwaan 이라 hwwwaani 토큰을 잠깐 빌려 씁니다.

```bash
export GH_TOKEN="$(gh auth token -u hwwwaani)"
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' pull --ff-only
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push
unset GH_TOKEN
```

`.github/workflows/` 안의 파일은 이 맥에서 푸시할 수 없습니다(gh 토큰에 workflow 권한이 없음). 실행 설정을 고칠 때는 깃허브 웹 편집기에서 고칩니다.
손으로 한 번 돌리려면 저장소 Actions 탭 › 기사 모으기 › Run workflow 를 누르거나 `gh workflow run update.yml -R hwwwaani/labor-news-scrap` (위 GH_TOKEN 을 건 채로).

## 화면 다시 올리기

`site/index.html` 을 고쳤을 때만 합니다. 한 번에 Netlify 크레딧이 듭니다.

```bash
npx netlify-cli deploy --dir site --prod --site 6fda9cbc-ca9d-4030-8715-b932656dd44d
```

`--site labor-news-scrap` 처럼 이름으로 주면 「Not Found」 가 납니다(11/8). 사이트 ID 로 줍니다.

## 알아 둘 것

- 구글 뉴스에서 온 기사 링크는 구글을 거쳐 원문으로 넘어갑니다. 같은 기사가 언론사 RSS 에도 있으면 언론사 주소를 씁니다. 사건 요약을 만들며 원문 주소를 풀어 둔 기사는(`orig` 칸) 원문으로 바로 갑니다.
- 구글 뉴스 주소를 원문 주소로 푸는 데는 구글 뉴스의 내부 주소(batchexecute)를 씁니다. 몰아서 부르면 429 로 막혀서(11/8 시험에서 60번쯤) 하나씩 1.2초 간격, 한 회차 40번까지만 풀고 429 가 나면 그 회차는 멈춥니다. 구글이 방식을 바꾸면 깨질 수 있고, 그러면 사건 요약이 제목만 보고 쓰는 것으로 내려앉습니다(Actions 기록의 「원문을 받은 사건 n/m」 이 0 으로 나옵니다).
- 원문을 하나도 못 받아 제목만으로 쓴 사건 요약은 다음 회차에 한 번 더 원문을 시도합니다. 화면에는 「AI 요약 · 제목만 보고 씀」 으로 표시됩니다.
- 장 표시는 낱말 맞추기라 틀릴 때가 있습니다. 예를 들어 노동자대회 기사의 요약에 「정년」이 나오면 6장이 붙습니다.
- 구글 뉴스는 「노동조합」 같은 검색어에 베트남 매체 번역 기사를 섞어 줍니다. `BLOCK_SOURCES` 로 거르고 있습니다.
