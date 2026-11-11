# 텔레그램 메시지 푸시 가이드

## 이 기능으로 할 수 있는 일

- 내 텔레그램 봇으로 내 1:1 채팅, 그룹, 채널에 텍스트 알림 푸시
- 작업 완료 알림이나 다른 k-skill 조회 결과를 휴대폰으로 받기
- 긴 본문 자동 분할, 무음 전송, HTML 서식, 포럼 topic 지정

보내기 전용입니다. 대화형 봇/webhook 수신은 범위 밖입니다.

## 먼저 필요한 것

- 인터넷 연결 (`api.telegram.org`)
- `python3` 3.9+
- 텔레그램 계정
- `KSKILL_TELEGRAM_BOT_TOKEN`, `KSKILL_TELEGRAM_CHAT_ID` ([공통 설정 가이드](../setup.md))

## 1회 셋업

1. 텔레그램에서 **@BotFather** → `/newbot` → 표시 이름 → `bot` 으로 끝나는 username 입력
2. 발급된 토큰을 `~/.config/k-skill/secrets.env` 에 저장 (퍼미션 `0600`)

   ```dotenv
   KSKILL_TELEGRAM_BOT_TOKEN=123456789:AA...
   ```

3. 토큰 확인

   ```bash
   python3 scripts/telegram_push.py whoami
   ```

4. 텔레그램에서 내 봇에게 `/start` 전송 (그룹은 봇 초대 후 메시지 1건, 채널은 봇을 관리자로 추가)
5. chat_id 조회 후 저장

   ```bash
   python3 scripts/telegram_push.py chats
   ```

   ```dotenv
   KSKILL_TELEGRAM_CHAT_ID=987654321
   ```

## 보내기

```bash
python3 scripts/telegram_push.py send "배포가 끝났습니다 ✅"
some-command | python3 scripts/telegram_push.py send
python3 scripts/telegram_push.py send --chat-id -1001234567890 --silent --no-preview "야간 배치 완료"
python3 scripts/telegram_push.py send --parse-mode HTML "<b>미세먼지</b> 나쁨"
```

```json
{
  "ok": true,
  "sent": [{ "message_id": 12, "chat_id": 987654321, "date": 1762850000 }]
}
```

| 옵션 | 설명 |
| --- | --- |
| `--chat-id` | 대상 chat_id 또는 `@채널username`. 생략하면 `KSKILL_TELEGRAM_CHAT_ID` |
| `--parse-mode` | `HTML` / `MarkdownV2` / `Markdown` |
| `--silent` | 알림음 없이 전송 |
| `--no-preview` | 링크 미리보기 끔 |
| `--thread-id` | 포럼 그룹의 topic id |

## 동작 규칙

- 4096자(UTF-16 기준)를 넘는 일반 텍스트는 문단 → 줄 → 공백 경계에서 나눠 순서대로 보냅니다.
- parse mode 를 쓴 메시지는 분할하지 않고 거부합니다.
- `429` 는 `retry_after`(30초 이하)만큼 기다렸다 1회 재시도합니다.
- 그룹/채널처럼 다른 사람이 보는 대상은 처음 보낼 때 본문과 대상을 확인받습니다.

## 자주 나는 오류

| 응답 | 원인 / 조치 |
| --- | --- |
| `401 Unauthorized` | 토큰 오류 또는 폐기됨 |
| `400 chat not found` | chat_id 오류, 또는 봇에게 `/start` 를 보낸 적 없음 |
| `403 bot was blocked by the user` | 봇 차단 해제 후 `/start` |
| `403 bot is not a member` | 봇을 그룹 멤버/채널 관리자로 추가 |
| `409 Conflict` (`chats`) | 봇에 webhook 이 걸려 있음. 푸시 전용 봇을 따로 생성 |

## 보안

- 토큰은 비밀번호입니다. 대화창·커밋·로그에 남기지 않습니다.
- 유출되면 BotFather `/revoke` 로 폐기 후 재발급합니다.
- 공식 Bot API 를 직접 호출하며 `k-skill-proxy` 를 거치지 않습니다.

## 참고

- Telegram Bot API: https://core.telegram.org/bots/api
- BotFather 안내: https://core.telegram.org/bots/features#botfather
