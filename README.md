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
5. 바뀐 목록을 저장소에 커밋합니다.
6. Netlify 에 올린 화면(`site/index.html`)은 그 파일을 깃허브에서 바로 읽습니다.

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
| 질문 후보 모델·건수·대상 기간 | `scripts/fetch.py` 의 `AI_MODEL`·`AI_MAX`·`AI_DAYS`, 지시문은 `ai_system()` |
| 화면 모양 | `site/index.html` → 고친 뒤 아래 「화면 다시 올리기」 |

`chapters.json` 만 고치면 다음 날 아침부터 새 기준이 붙습니다. 이미 쌓인 기사는 다시 나누지 않습니다.

## 질문 후보와 API 키

- 키는 저장소 비밀값 `ANTHROPIC_API_KEY` 에 있습니다. 개인 Claude 콘솔(「Juhwan's Individual Org」)에서 사용자가 만든 `labor-news-scrap` 키입니다.
- 키가 없거나 만료되면 질문 후보만 건너뛰고 기사 모으기는 그대로 돕니다. Actions 기록에 「질문 후보 건너뜀」이나 「키가 틀림」이 찍힙니다.
- 키를 바꿀 때는 콘솔에서 새 키를 만들어 저장소 Settings › Secrets and variables › Actions 의 같은 이름 비밀값을 고칩니다.
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
npx netlify-cli deploy --dir site --prod --site labor-news-scrap
```

## 알아 둘 것

- 구글 뉴스에서 온 기사 링크는 구글을 거쳐 원문으로 넘어갑니다. 같은 기사가 언론사 RSS 에도 있으면 언론사 주소를 씁니다.
- 장 표시는 낱말 맞추기라 틀릴 때가 있습니다. 예를 들어 노동자대회 기사의 요약에 「정년」이 나오면 6장이 붙습니다.
- 구글 뉴스는 「노동조합」 같은 검색어에 베트남 매체 번역 기사를 섞어 줍니다. `BLOCK_SOURCES` 로 거르고 있습니다.
