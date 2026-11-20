import copy
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo
from unittest.mock import patch

import game_night_check as g

TZ = ZoneInfo('America/Chicago')
DAY = date(2026, 11, 20)
ROW = {'status': 'active', 'obsidian_campaign_folder': '5 Friday - Test Game',
       'names': {'canonical': 'Test Game'}, 'calendar_location': 'test',
       'game_night_check': {'enabled': True, 'scheduling_channel_id': '123456789',
                           'scheduling_channel_name': 'tg-scheduling'}}


def event(when='2026-11-27T18:00:00-06:00', **kwargs):
    return {'id': 'event1', 'summary': 'Test Game #2: Placeholder', 'location': 'test',
            'status': 'confirmed', 'start': {'dateTime': when}, **kwargs}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ROW['obsidian_campaign_folder']).mkdir()
        self.config = {'calendar': {'timezone': 'America/Chicago'},
                       'local_paths': {'obsidian_campaigns_root': str(self.root)},
                       'campaigns': {'test': copy.deepcopy(ROW)},
                       'game_night_check': {'posting_enabled': True}}

    def run_case(self, events, post=False):
        sent = []
        result = g.run(self.config, DAY, post, lambda *args: events,
                       lambda channel, text: sent.append((channel, text)) or 'message123')
        return result['results'][0], sent

    def test_scheduled(self):
        item, sent = self.run_case([event('2026-11-20T18:00:00-06:00')], True)
        self.assertEqual(item['status'], 'scheduled')
        self.assertEqual(sent, [])

    def test_off_week_and_long_timestamp(self):
        item, sent = self.run_case([event()])
        epoch = int(datetime(2026, 11, 27, 18, tzinfo=TZ).timestamp())
        self.assertEqual(item['status'], 'would_post')
        self.assertIn(f'<t:{epoch}:F>', item['message'])
        self.assertEqual(sent, [])

    def test_post(self):
        item, sent = self.run_case([event()], True)
        self.assertEqual(item['status'], 'posted')
        self.assertEqual(item['message_id'], 'message123')
        self.assertEqual(sent[0][0], '123456789')

    def test_no_next(self):
        item, _ = self.run_case([])
        self.assertIn('not currently on the calendar', item['message'])

    def test_cancelled_is_not_session(self):
        item, _ = self.run_case([event('2026-11-20T18:00:00-06:00', status='cancelled'), event()])
        self.assertEqual(item['status'], 'would_post')

    def test_identity_conflict(self):
        item, sent = self.run_case([event(location='wrong')], True)
        self.assertEqual(item['status'], 'error')
        self.assertFalse(sent)

    def test_series_mismatch(self):
        self.config['campaigns']['test']['calendar_recurring_event_id'] = 'correct'
        item, _ = self.run_case([event(recurringEventId='incorrect')])
        self.assertEqual(item['status'], 'error')

    def test_unrelated_event(self):
        item, _ = self.run_case([event(summary='Other Game', location='other')])
        self.assertEqual(item['status'], 'would_post')

    def test_missing_channel_no_fallback(self):
        self.config['campaigns']['test']['game_night_check'].pop('scheduling_channel_id')
        self.config['campaigns']['test']['discord_channel_id'] = '999'
        item, sent = self.run_case([], True)
        self.assertEqual(item['status'], 'skipped')
        self.assertFalse(sent)

    def test_global_disabled(self):
        self.config['game_night_check']['posting_enabled'] = False
        item, sent = self.run_case([], True)
        self.assertEqual(item['status'], 'skipped')
        self.assertFalse(sent)

    def test_campaign_opt_in_required(self):
        self.config['campaigns']['test']['game_night_check'].pop('enabled')
        item, sent = self.run_case([], True)
        self.assertEqual(item['status'], 'skipped')
        self.assertFalse(sent)

    def test_calendar_failure(self):
        def fail(*args):
            raise RuntimeError('Calendar unavailable')
        with patch.object(g, 'send_notice', side_effect=AssertionError('must not send')):
            report = g.run(self.config, DAY, True, fail)
        self.assertEqual(report['results'][0]['status'], 'error')

    def test_same_day_suffix(self):
        folder = '5a Friday - Second Game'
        (self.root / folder).mkdir()
        self.config['campaigns']['second'] = {**copy.deepcopy(ROW), 'obsidian_campaign_folder': folder}
        result = g.run(self.config, DAY, fetcher=lambda *a: [])
        self.assertEqual(len(result['results']), 2)

    def test_inactive_and_unknown(self):
        self.config['campaigns']['test']['status'] = 'ended'
        item, sent = self.run_case([], True)
        self.assertEqual(item['status'], 'skipped')
        self.assertFalse(sent)
        self.config['campaigns'] = {}
        item, _ = self.run_case([])
        self.assertEqual(item['status'], 'skipped')

    def test_local_date_not_utc_date(self):
        item, _ = self.run_case([event('2026-11-21T00:00:00Z')])
        self.assertEqual(item['status'], 'scheduled')

    def test_all_day_next_is_error(self):
        item, _ = self.run_case([event(start={'date': '2026-11-27'})])
        self.assertEqual(item['status'], 'error')

    def test_log_retains_five(self):
        path = self.root / 'AI/log.md'
        for n in range(8):
            g.append_log(path, {'started_at': str(n), 'date': str(DAY), 'timezone': str(TZ),
                                'mode': 'dry-run', 'results': []})
        content = path.read_text()
        self.assertEqual(content.count('## Run '), 5)
        self.assertIn('## Run 7', content)
        self.assertNotIn('## Run 2', content)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_fetch_rejects_partial_and_bad_shape(self):
        for payload in ({'events': [], 'nextPageToken': 'more'}, {}, []):
            with patch.object(g, 'command', return_value=payload):
                with self.assertRaises(RuntimeError):
                    g.fetch_events({'calendar': {'rpg_calendar_id': 'id'}, 'google': {'account': 'user'}},
                                   datetime.now(TZ), datetime.now(TZ))


if __name__ == '__main__':
    unittest.main()
