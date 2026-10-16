import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings
from integrations import StageError
from telegram_check import receive_code


class TelegramReceiveTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(telegram_token='test-only', telegram_chat='123')
        self.webhook = ({'ok': True, 'result': {'url': ''}}, {})

    def test_receives_exact_code_from_configured_chat(self):
        incoming = {'update_id': 10, 'message': {'message_id': 5, 'chat': {'id': 123}, 'text': 'CODE-TEST'}}
        with patch('telegram_check.http_json', side_effect=[self.webhook, ({'ok': True, 'result': [incoming]}, {})]):
            self.assertEqual(receive_code(self.settings, 'CODE-TEST'), {'update_id': 10, 'message_id': 5})

    def test_ignores_other_chats_and_unrelated_text(self):
        updates = [
            {'update_id': 10, 'message': {'message_id': 5, 'chat': {'id': 456}, 'text': 'CODE-TEST'}},
            {'update_id': 11, 'message': {'message_id': 6, 'chat': {'id': 123}, 'text': 'other'}},
        ]
        with patch('telegram_check.http_json', side_effect=[self.webhook, ({'ok': True, 'result': updates}, {})]) as mocked:
            self.assertIsNone(receive_code(self.settings, 'CODE-TEST'))
            self.assertNotIn('offset=', mocked.call_args.args[0])

    def test_existing_webhook_is_preserved(self):
        with patch('telegram_check.http_json', return_value=({'ok': True, 'result': {'url': 'https://existing.example/webhook'}}, {})) as mocked:
            with self.assertRaises(StageError) as caught:
                receive_code(self.settings, 'CODE-TEST')
            self.assertEqual(caught.exception.code, 'telegram_webhook_activo_no_modificado')
            self.assertEqual(mocked.call_count, 1)

    def test_invalid_updates_are_not_successful_reception(self):
        with patch('telegram_check.http_json', side_effect=[self.webhook, ({'ok': False}, {})]):
            with self.assertRaises(StageError) as caught:
                receive_code(self.settings, 'CODE-TEST')
            self.assertEqual(caught.exception.code, 'telegram_updates_invalidos')

    def test_start_code_is_accepted_for_bot_deep_links(self):
        incoming = {'update_id': 10, 'message': {'message_id': 5, 'chat': {'id': 123}, 'text': '/start CODE-TEST'}}
        with patch('telegram_check.http_json', side_effect=[self.webhook, ({'ok': True, 'result': [incoming]}, {})]):
            self.assertIsNotNone(receive_code(self.settings, 'CODE-TEST'))


if __name__ == '__main__':
    unittest.main()
