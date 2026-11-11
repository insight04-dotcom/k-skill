import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "telegram-push" / "scripts" / "telegram_push.py"


def load_module():
    spec = importlib.util.spec_from_file_location("telegram_push", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tp = load_module()
TOKEN = "123456:TEST-TOKEN"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, json.loads(body.decode("utf-8"))))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")


def ok(result):
    return 200, {"ok": True, "result": result}


class SplitMessageTest(unittest.TestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(tp.split_message("안녕하세요"), ["안녕하세요"])

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(tp.split_message("  \n "), [])

    def test_prefers_line_boundaries_and_keeps_every_chunk_within_limit(self):
        text = "\n".join(f"line {index:03d}" for index in range(50))
        chunks = tp.split_message(text, limit=40)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(tp.utf16_length(chunk), 40)
            self.assertFalse(chunk.startswith("\n"))
        self.assertEqual("\n".join(chunks), text)

    def test_hard_splits_unbroken_text(self):
        chunks = tp.split_message("가" * 25, limit=10)
        self.assertEqual(chunks, ["가" * 10, "가" * 10, "가" * 5])

    def test_counts_utf16_code_units_for_emoji(self):
        chunks = tp.split_message("😀" * 6, limit=4)
        self.assertEqual(chunks, ["😀😀", "😀😀", "😀😀"])


class SettingsTest(unittest.TestCase):
    def test_resolution_order_explicit_env_then_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = pathlib.Path(tmp) / "secrets.env"
            secrets.write_text(
                "# comment\nexport KSKILL_TELEGRAM_CHAT_ID='-100123'\n"
                "KSKILL_TELEGRAM_BOT_TOKEN=replace-me\n",
                encoding="utf-8",
            )
            name = tp.CHAT_ID_ENV
            self.assertEqual(tp.resolve_setting(name, "1", {name: "2"}, secrets), "1")
            self.assertEqual(tp.resolve_setting(name, None, {name: "2"}, secrets), "2")
            self.assertEqual(tp.resolve_setting(name, None, {}, secrets), "-100123")
            self.assertIsNone(tp.resolve_setting(tp.TOKEN_ENV, None, {}, secrets))
            self.assertIsNone(tp.resolve_setting(name, None, {}, pathlib.Path(tmp) / "missing"))


class TelegramBotTest(unittest.TestCase):
    def test_send_message_posts_expected_payload(self):
        transport = FakeTransport([ok({"message_id": 7, "chat": {"id": 42}, "date": 1})])
        bot = tp.TelegramBot(TOKEN, transport=transport)

        sent = bot.send_message("42", "배포 완료", silent=True, disable_preview=True, thread_id=3)

        self.assertEqual(sent, [{"message_id": 7, "chat_id": 42, "date": 1}])
        url, payload = transport.calls[0]
        self.assertEqual(url, f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual(
            payload,
            {
                "chat_id": "42",
                "text": "배포 완료",
                "disable_notification": True,
                "link_preview_options": {"is_disabled": True},
                "message_thread_id": 3,
            },
        )

    def test_long_plain_message_is_sent_in_order_as_multiple_messages(self):
        transport = FakeTransport([ok({"message_id": n, "chat": {"id": 1}}) for n in (1, 2)])
        bot = tp.TelegramBot(TOKEN, transport=transport)
        text = "a" * 4096 + "\n" + "b" * 10

        sent = bot.send_message(1, text)

        self.assertEqual([item["message_id"] for item in sent], [1, 2])
        self.assertEqual([call[1]["text"] for call in transport.calls], ["a" * 4096, "b" * 10])

    def test_long_formatted_message_is_rejected_before_any_request(self):
        transport = FakeTransport([])
        bot = tp.TelegramBot(TOKEN, transport=transport)
        with self.assertRaises(tp.TelegramPushError):
            bot.send_message(1, "<b>" + "a" * 5000 + "</b>", parse_mode="HTML")
        self.assertEqual(transport.calls, [])

    def test_empty_message_is_rejected(self):
        bot = tp.TelegramBot(TOKEN, transport=FakeTransport([]))
        with self.assertRaises(tp.TelegramPushError):
            bot.send_message(1, "   ")

    def test_rate_limit_is_retried_once_after_retry_after(self):
        sleeps = []
        transport = FakeTransport(
            [
                (429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                       "parameters": {"retry_after": 2}}),
                ok({"message_id": 9, "chat": {"id": 1}}),
            ]
        )
        bot = tp.TelegramBot(TOKEN, transport=transport, sleep=sleeps.append)

        sent = bot.send_message(1, "hi")

        self.assertEqual(sleeps, [2])
        self.assertEqual(sent[0]["message_id"], 9)

    def test_api_error_surfaces_description_without_token(self):
        transport = FakeTransport(
            [(403, {"ok": False, "error_code": 403,
                    "description": f"Forbidden: bot{TOKEN} was blocked by the user"})]
        )
        bot = tp.TelegramBot(TOKEN, transport=transport)
        with self.assertRaises(tp.TelegramPushError) as caught:
            bot.send_message(1, "hi")
        self.assertIn("403", str(caught.exception))
        self.assertIn("blocked by the user", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_connection_error_never_leaks_token(self):
        def transport(url, body, timeout):
            raise OSError(f"cannot reach {url}")

        bot = tp.TelegramBot(TOKEN, transport=transport)
        with self.assertRaises(tp.TelegramPushError) as caught:
            bot.get_me()
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_list_chats_deduplicates_and_names_chats(self):
        transport = FakeTransport(
            [
                ok(
                    [
                        {"message": {"chat": {"id": 42, "type": "private", "first_name": "길동",
                                              "last_name": "홍", "username": "gildong"}}},
                        {"message": {"chat": {"id": 42, "type": "private", "first_name": "길동"}}},
                        {"channel_post": {"chat": {"id": -1001, "type": "channel", "title": "알림"}}},
                        {"poll": {}},
                    ]
                )
            ]
        )
        chats = tp.TelegramBot(TOKEN, transport=transport).list_chats()
        self.assertEqual([chat["chat_id"] for chat in chats], [42, -1001])
        self.assertEqual(chats[1]["name"], "알림")


class CliTest(unittest.TestCase):
    def run_cli(self, argv, env, responses, stdin=""):
        transport = FakeTransport(responses)

        def factory(token, timeout):
            return tp.TelegramBot(token, transport=transport, timeout=timeout)

        with mock.patch.dict(tp.os.environ, env, clear=True), \
                mock.patch.object(tp, "DEFAULT_SECRETS_FILE", pathlib.Path("/nonexistent/secrets.env")), \
                mock.patch.object(tp.sys, "stdin", io.StringIO(stdin)):
            return tp.run(argv, bot_factory=factory), transport

    def test_send_reads_stdin_and_uses_default_chat_id(self):
        env = {tp.TOKEN_ENV: TOKEN, tp.CHAT_ID_ENV: "42"}
        result, transport = self.run_cli(
            ["send"], env, [ok({"message_id": 1, "chat": {"id": 42}})], stdin="빌드 성공\n"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(transport.calls[0][1]["chat_id"], "42")
        self.assertEqual(transport.calls[0][1]["text"], "빌드 성공\n")

    def test_missing_token_and_chat_id_raise_actionable_errors(self):
        with self.assertRaisesRegex(tp.TelegramPushError, tp.TOKEN_ENV):
            self.run_cli(["whoami"], {}, [])
        with self.assertRaisesRegex(tp.TelegramPushError, "chat_id"):
            self.run_cli(["send", "hi"], {tp.TOKEN_ENV: TOKEN}, [])

    def test_chats_gives_hint_when_no_updates(self):
        result, _ = self.run_cli(["chats"], {tp.TOKEN_ENV: TOKEN}, [ok([])])
        self.assertEqual(result["chats"], [])
        self.assertIn("/start", result["hint"])


if __name__ == "__main__":
    unittest.main()
