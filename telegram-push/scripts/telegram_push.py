#!/usr/bin/env python3
"""Push messages to a Telegram chat through the official Bot API.

Standard library only. The bot token is read from the environment or
``~/.config/k-skill/secrets.env`` and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_BASE = "https://api.telegram.org"
TOKEN_ENV = "KSKILL_TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "KSKILL_TELEGRAM_CHAT_ID"
DEFAULT_SECRETS_FILE = Path("~/.config/k-skill/secrets.env")
MESSAGE_LIMIT = 4096
MAX_RETRY_AFTER_SECONDS = 30
PARSE_MODES = ("HTML", "MarkdownV2", "Markdown")

Transport = Callable[[str, bytes, float], tuple[int, bytes]]


class TelegramPushError(RuntimeError):
    """User-facing failure. The message never contains the bot token."""


def load_secrets_file(path: Path | None = None) -> dict[str, str]:
    target = (path or DEFAULT_SECRETS_FILE).expanduser()
    values: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve_setting(
    name: str,
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    secrets_file: Path | None = None,
) -> str | None:
    """Resolve explicit value -> environment -> secrets file."""
    if explicit:
        return explicit
    environment = os.environ if env is None else env
    value = environment.get(name)
    if value:
        return value
    value = load_secrets_file(secrets_file).get(name)
    if value and value != "replace-me":
        return value
    return None


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split text into chunks within Telegram's per-message limit.

    Telegram counts UTF-16 code units. Prefer paragraph, then line, then
    word boundaries; hard-split only when a single token exceeds the limit.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    chunks: list[str] = []
    remaining = text
    while utf16_length(remaining) > limit:
        # Largest prefix that fits within the limit.
        cut = min(len(remaining), limit)
        while utf16_length(remaining[:cut]) > limit:
            cut -= 1
        window = remaining[:cut]
        boundary = -1
        for separator in ("\n\n", "\n", " "):
            boundary = window.rfind(separator)
            if boundary > 0:
                break
        if boundary <= 0:
            boundary = cut
        chunk = remaining[:boundary].rstrip()
        if not chunk:
            chunk = remaining[:cut]
            boundary = cut
        chunks.append(chunk)
        remaining = remaining[boundary:].lstrip("\n ")
    if remaining.strip():
        chunks.append(remaining)
    return chunks


def _urllib_transport(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class TelegramBot:
    def __init__(
        self,
        token: str,
        *,
        transport: Transport | None = None,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise TelegramPushError(f"{TOKEN_ENV} 가 설정되어 있지 않습니다.")
        self._token = token
        self._transport = transport or _urllib_transport
        self._timeout = timeout
        self._sleep = sleep

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "<redacted-token>")

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        for attempt in range(2):
            try:
                status, raw = self._transport(url, body, self._timeout)
            except (urllib.error.URLError, OSError) as error:
                raise TelegramPushError(
                    self._redact(f"Telegram API 연결 실패 ({method}): {error}")
                ) from None
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise TelegramPushError(
                    f"Telegram API 가 JSON 이 아닌 응답을 돌려줬습니다 ({method}, HTTP {status})."
                ) from None
            if isinstance(data, dict) and data.get("ok"):
                return data.get("result")
            data = data if isinstance(data, dict) else {}
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if (
                attempt == 0
                and isinstance(retry_after, (int, float))
                and 0 < retry_after <= MAX_RETRY_AFTER_SECONDS
            ):
                self._sleep(retry_after)
                continue
            description = self._redact(str(data.get("description") or "unknown error"))
            code = data.get("error_code", status)
            raise TelegramPushError(f"Telegram API 오류 ({method}, {code}): {description}")
        raise AssertionError("unreachable")

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")

    def list_chats(self) -> list[dict[str, Any]]:
        """Chats that recently talked to the bot (from getUpdates)."""
        updates = self.call("getUpdates", {"timeout": 0, "allowed_updates": []}) or []
        chats: dict[Any, dict[str, Any]] = {}
        for update in updates:
            for key in ("message", "edited_message", "channel_post", "my_chat_member"):
                chat = (update.get(key) or {}).get("chat")
                if not chat or "id" not in chat:
                    continue
                name = chat.get("title") or " ".join(
                    part for part in (chat.get("first_name"), chat.get("last_name")) if part
                )
                chats[chat["id"]] = {
                    "chat_id": chat["id"],
                    "type": chat.get("type"),
                    "name": name or None,
                    "username": chat.get("username"),
                }
        return list(chats.values())

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        parse_mode: str | None = None,
        silent: bool = False,
        disable_preview: bool = False,
        thread_id: int | None = None,
    ) -> list[dict[str, Any]]:
        chunks = split_message(text)
        if not chunks:
            raise TelegramPushError("보낼 메시지가 비어 있습니다.")
        if parse_mode and len(chunks) > 1:
            raise TelegramPushError(
                f"parse mode 를 쓰는 메시지는 {MESSAGE_LIMIT}자를 넘길 수 없습니다 "
                "(분할하면 서식 태그가 깨집니다). 메시지를 줄이거나 parse mode 없이 보내세요."
            )
        sent = []
        for chunk in chunks:
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if silent:
                payload["disable_notification"] = True
            if disable_preview:
                payload["link_preview_options"] = {"is_disabled": True}
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            result = self.call("sendMessage", payload) or {}
            sent.append(
                {
                    "message_id": result.get("message_id"),
                    "chat_id": (result.get("chat") or {}).get("id", chat_id),
                    "date": result.get("date"),
                }
            )
        return sent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Telegram Bot API 로 내 채팅/그룹/채널에 메시지를 푸시한다.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="요청 timeout(초)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("whoami", help="봇 토큰이 유효한지 확인한다 (getMe)")
    commands.add_parser("chats", help="봇에게 최근 말을 건 채팅의 chat_id 를 찾는다 (getUpdates)")

    send = commands.add_parser("send", help="메시지를 보낸다")
    send.add_argument("text", nargs="?", help="보낼 메시지. 생략하거나 '-' 이면 stdin 에서 읽는다")
    send.add_argument("--chat-id", help=f"대상 chat_id 또는 @채널명 (기본: {CHAT_ID_ENV})")
    send.add_argument("--parse-mode", choices=PARSE_MODES, help="서식 모드")
    send.add_argument("--silent", action="store_true", help="알림음 없이 보낸다")
    send.add_argument("--no-preview", action="store_true", help="링크 미리보기를 끈다")
    send.add_argument("--thread-id", type=int, help="포럼 그룹의 topic(message_thread_id)")
    return parser


def run(argv: list[str] | None = None, *, bot_factory: Callable[..., TelegramBot] = TelegramBot) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    token = resolve_setting(TOKEN_ENV)
    if not token:
        raise TelegramPushError(
            f"{TOKEN_ENV} 가 없습니다. @BotFather 에서 봇을 만들고 토큰을 "
            f"{DEFAULT_SECRETS_FILE} 에 저장하세요."
        )
    bot = bot_factory(token, timeout=args.timeout)

    if args.command == "whoami":
        me = bot.get_me() or {}
        return {"id": me.get("id"), "username": me.get("username"), "name": me.get("first_name")}

    if args.command == "chats":
        chats = bot.list_chats()
        result: dict[str, Any] = {"chats": chats}
        if not chats:
            result["hint"] = (
                "봇에게 온 최근 메시지가 없습니다. 텔레그램에서 봇에게 /start 를 보낸 뒤 다시 실행하세요."
            )
        return result

    text = args.text
    if text is None or text == "-":
        text = sys.stdin.read()
    chat_id = resolve_setting(CHAT_ID_ENV, args.chat_id)
    if not chat_id:
        raise TelegramPushError(
            f"chat_id 가 없습니다. --chat-id 를 주거나 {CHAT_ID_ENV} 를 설정하세요 "
            "(`chats` 명령으로 찾을 수 있습니다)."
        )
    sent = bot.send_message(
        chat_id,
        text,
        parse_mode=args.parse_mode,
        silent=args.silent,
        disable_preview=args.no_preview,
        thread_id=args.thread_id,
    )
    return {"ok": True, "sent": sent}


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
    except TelegramPushError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
