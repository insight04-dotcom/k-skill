---
name: telegram-push
description: Create a personal Telegram bot with BotFather and push text notifications to your own chat, group, or channel through the official Telegram Bot API.
license: MIT
metadata:
  category: messaging
  locale: ko-KR
  phase: v1
---

# Telegram Push

## What this skill does

텔레그램 공식 Bot API(`https://api.telegram.org`)로 **내 봇이 내 채팅/그룹/채널에 메시지를 푸시**한다.

- 봇 생성(@BotFather) → 토큰 저장 → chat_id 확인 → 전송까지의 1회성 셋업 안내
- 작업 완료 알림, 조회 결과 요약, 예약/모니터링 결과 등을 텔레그램으로 푸시
- 표준 라이브러리만 쓰는 helper(`scripts/telegram_push.py`) 제공. 별도 패키지 설치 없음

수신(대화형 봇, webhook)은 다루지 않는다. 보내기 전용이다.

## When to use

- "이 결과 텔레그램으로 보내줘"
- "작업 끝나면 텔레그램으로 알려줘"
- "텔레그램 알림 봇 만들어줘 / 세팅해줘"
- 다른 k-skill 결과(날씨, 미세먼지, 예매 결과 등)를 휴대폰으로 푸시하고 싶을 때

## Prerequisites

- 인터넷 연결 (`api.telegram.org` 로 나가는 HTTPS)
- `python3` 3.9+
- 텔레그램 계정
- 시크릿 2개 (`k-skill-setup` 의 credential resolution order 를 따른다)
  - `KSKILL_TELEGRAM_BOT_TOKEN` — BotFather 가 발급한 봇 토큰
  - `KSKILL_TELEGRAM_CHAT_ID` — 기본 수신 대상 chat_id (없으면 매번 `--chat-id` 지정)

## Workflow

### 1. 봇이 없으면 BotFather 로 만든다 (사용자가 직접, 1회)

봇 생성은 텔레그램 앱 안에서 사용자 본인만 할 수 있다. 아래를 그대로 안내한다.

1. 텔레그램에서 **@BotFather** (파란 인증 배지) 를 연다.
2. `/newbot` 전송
3. 봇 표시 이름 입력 (예: `내 알림 봇`)
4. 봇 username 입력 — 반드시 `bot` 으로 끝나야 한다 (예: `my_push_alarm_bot`)
5. BotFather 가 돌려준 `123456789:AA...` 형태의 **토큰**을 복사한다.

토큰은 비밀번호와 같다. 채팅창에 붙여넣게 하지 말고 사용자가 직접 secrets 파일에 넣게 한다.

```bash
mkdir -p ~/.config/k-skill
touch ~/.config/k-skill/secrets.env && chmod 0600 ~/.config/k-skill/secrets.env
# 편집기로 열어 아래 줄을 추가
# KSKILL_TELEGRAM_BOT_TOKEN=123456789:AA...
```

### 2. 토큰을 검증한다

```bash
python3 scripts/telegram_push.py whoami
```

```json
{
  "id": 123456789,
  "username": "my_push_alarm_bot",
  "name": "내 알림 봇"
}
```

### 3. chat_id 를 찾는다 (1회)

봇은 **먼저 말을 건 사용자에게만** 메시지를 보낼 수 있다.

1. 사용자가 텔레그램에서 방금 만든 봇을 열고 `/start` 를 보낸다.
   - 그룹으로 받으려면 봇을 그룹에 초대한 뒤 그룹에서 아무 메시지나 보낸다.
   - 채널로 받으려면 봇을 채널 관리자로 추가한다 (공개 채널은 `@채널username` 을 chat_id 로 바로 쓸 수 있다).
2. chat_id 를 조회한다.

```bash
python3 scripts/telegram_push.py chats
```

```json
{
  "chats": [
    { "chat_id": 987654321, "type": "private", "name": "길동 홍", "username": "gildong" }
  ]
}
```

3. 찾은 값을 secrets 파일에 기본 수신처로 저장한다.

```dotenv
KSKILL_TELEGRAM_CHAT_ID=987654321
```

그룹/슈퍼그룹 chat_id 는 음수(`-100...`)다. 부호까지 그대로 저장한다.

### 4. 메시지를 푸시한다

```bash
# 기본 수신처로 전송
python3 scripts/telegram_push.py send "배포가 끝났습니다 ✅"

# 긴 본문은 stdin 으로
some-command | python3 scripts/telegram_push.py send

# 다른 대상, 무음, 링크 미리보기 끔
python3 scripts/telegram_push.py send --chat-id -1001234567890 --silent --no-preview "야간 배치 완료"

# HTML 서식 (<b>, <i>, <code>, <pre>, <a href>)
python3 scripts/telegram_push.py send --parse-mode HTML "<b>미세먼지</b> 나쁨 · <code>PM2.5 48</code>"
```

성공 응답:

```json
{
  "ok": true,
  "sent": [{ "message_id": 12, "chat_id": 987654321, "date": 1762850000 }]
}
```

helper 없이 한 줄로 보낼 때는 토큰이 shell history/프로세스 목록에 남지 않도록 환경변수로만 참조한다.

```bash
set -a; . ~/.config/k-skill/secrets.env; set +a
curl -sS "https://api.telegram.org/bot${KSKILL_TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${KSKILL_TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=배포가 끝났습니다"
```

### 5. 전송 규칙

- 메시지 1건은 4096자(UTF-16 기준)까지다. helper 는 일반 텍스트를 문단 → 줄 → 공백 경계에서 자동 분할해 순서대로 보낸다.
- `--parse-mode` 를 쓴 메시지는 분할하지 않고 거부한다 (태그가 깨지므로). 길면 서식 없이 보내거나 직접 나눠 보낸다.
- `--parse-mode HTML` 에서는 본문의 `<`, `>`, `&` 를 `&lt;`, `&gt;`, `&amp;` 로 escape 한다. `MarkdownV2` 는 escape 규칙이 까다로우므로 HTML 을 우선한다.
- `429 Too Many Requests` 는 `retry_after`(30초 이하)만큼 기다렸다가 1회 재시도한다. 같은 채팅에 초당 1건, 그룹에 분당 20건을 넘기지 않는다.
- 사용자 본인 1:1 채팅으로의 푸시는 요청받은 즉시 보낸다. **그룹/채널처럼 다른 사람이 보는 대상**은 처음 보낼 때 본문과 대상을 사용자에게 확인받는다.

## Done when

- `whoami` 가 봇 username 을 돌려준다
- `send` 가 `"ok": true` 와 `message_id` 를 돌려주고, 사용자가 텔레그램에서 메시지를 확인했다
- 토큰이 대화/로그/커밋 어디에도 노출되지 않았다

## Failure modes

- `401 Unauthorized` — 토큰이 틀렸거나 폐기됐다. BotFather `/token` 으로 재확인한다.
- `400 Bad Request: chat not found` — chat_id 가 틀렸거나 사용자가 아직 봇에게 `/start` 를 보내지 않았다.
- `403 Forbidden: bot was blocked by the user` — 사용자가 봇을 차단했다. 봇 채팅에서 차단 해제 후 `/start`.
- `403 Forbidden: bot is not a member of the channel chat` — 봇을 채널 관리자/그룹 멤버로 추가해야 한다.
- `400 Bad Request: can't parse entities` — parse mode 서식 오류. escape 를 확인하거나 parse mode 없이 보낸다.
- `chats` 결과가 비어 있음 — `/start` 를 보낸 적이 없거나, 봇에 webhook 이 설정돼 있어 `getUpdates` 를 쓸 수 없다(`409 Conflict`). webhook 을 쓰지 않는 푸시 전용 봇을 따로 만든다.
- 그룹에서 `chats` 에 안 잡힘 — 봇의 privacy mode 때문에 일반 메시지를 못 본다. 그룹에서 `/start@봇username` 을 보낸다.
- 회사망/샌드박스에서 `api.telegram.org` 가 차단돼 연결 실패가 날 수 있다.

## Notes

- 토큰이 유출되면 BotFather 에서 `/revoke` 로 즉시 폐기하고 새 토큰을 secrets 파일에 다시 넣는다.
- helper 는 오류 메시지에서 토큰을 지운다. 그래도 `curl -v` 출력이나 URL 전체를 대화에 붙이지 않는다.
- `getUpdates` 는 최근 24시간 이내 업데이트만 보관한다. chat_id 는 한 번 찾으면 바뀌지 않으므로 저장해 둔다.
- 텔레그램 봇은 전화번호로 사용자를 찾아 먼저 메시지를 보낼 수 없다. 수신자가 먼저 봇을 시작해야 한다.
- 무료 공식 API 이며 `k-skill-proxy` 를 경유하지 않는다. 토큰은 사용자 기기에만 둔다.
