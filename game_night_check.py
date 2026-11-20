"""Calendar-driven no-game notices. Dry run unless --post is explicitly given."""
import argparse
from datetime import date, datetime, time, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from zoneinfo import ZoneInfo

import yaml

HOME = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes/profiles/rpgassistant')).expanduser()
REGISTRY = HOME / 'skills/rpg/references/session-workflow/campaigns.yaml'
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if result.returncode:
        # Do not copy command stderr into the public vault log.
        raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode})')
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f'{Path(args[0]).name} returned invalid JSON') from exc


def fetch_events(config, start, end):
    result = command(['gog', 'calendar', 'events', config['calendar']['rpg_calendar_id'],
                      '-a', config['google']['account'], '--from=' + start.isoformat(),
                      '--to=' + end.isoformat(), '--all-pages', '--json', '--no-input'])
    if not isinstance(result, dict) or not isinstance(result.get('events'), list):
        raise RuntimeError('Calendar response has no events array')
    if result.get('nextPageToken'):
        raise RuntimeError('Calendar response is incomplete')
    return result['events']


def event_start(event, tz):
    start = event.get('start', {})
    if 'dateTime' in start:
        value = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError('Calendar start has no timezone')
        return value.astimezone(tz)
    if 'date' in start:
        return datetime.combine(date.fromisoformat(start['date']), time.min, tz)
    raise ValueError('Calendar event has no start')


def identity(event, row):
    """Return match, unrelated, or conflict. Never resolve identity by weekday."""
    names = row.get('names', {})
    titles = [names.get('canonical'), names.get('session_calendar_short')]
    titles += row.get('game_night_check', {}).get('calendar_title_aliases', [])
    title = event.get('summary', '')
    named = any(re.search(r'(?<!\w)' + re.escape(n) + r'(?!\w)', title, re.I)
                for n in titles if n)
    location = row.get('calendar_location')
    located = location is not None and event.get('location') == location
    series = list(row.get('calendar_recurring_event_ids') or [])
    if row.get('calendar_recurring_event_id'):
        series.append(row['calendar_recurring_event_id'])
    series_match = event.get('recurringEventId') in series if series else False
    special = event.get('id') == row.get('session_zero_event_id') and bool(row.get('session_zero_event_id'))
    if not (named or located or series_match or special):
        return 'unrelated'
    if not named or (location is not None and not located):
        return 'conflict'
    if series and not (series_match or special):
        return 'conflict'
    return 'match'


def inspect_campaign(row, events, day, tz):
    matches = []
    for event in events:
        if event.get('status') == 'cancelled':
            continue
        match = identity(event, row)
        if match == 'conflict':
            raise ValueError('Calendar identity conflict; review campaign title/location/series mappings')
        if match == 'match':
            start = event_start(event, tz)
            if start.date() >= day:
                matches.append((start, event))
    today = [e for start, e in matches if start.date() == day]
    if today:
        return {'status': 'scheduled', 'detail': f'{len(today)} session(s) on calendar today; no notice'}
    future = sorted(((start, e) for start, e in matches if start.date() > day), key=lambda x: x[0])
    if future:
        start, event = future[0]
        if 'dateTime' not in event.get('start', {}):
            raise ValueError('Next session is all-day; cannot announce a verified session time')
        message = f'No game tonight. Our next scheduled session is <t:{int(start.timestamp())}:F>.'
        return {'status': 'no_game', 'message': message, 'next_session': start.isoformat(),
                'next_event_id': event.get('id')}
    return {'status': 'no_game', 'message': 'No game tonight. The next session is not currently on the calendar.',
            'detail': 'No next session found within the configured search horizon'}


def send_notice(channel_id, message):
    result = command(['hermes', '-p', os.environ.get('GAME_NIGHT_HERMES_PROFILE', 'rpgassistant'),
                      'send', '--to', 'discord:' + channel_id,
                      '--json', message])
    # CLI result shape must contain a message ID before delivery is marked verified.
    def find_id(obj):
        if isinstance(obj, dict):
            for key in ('message_id', 'messageId'):
                if obj.get(key):
                    return str(obj[key])
            if obj.get('id') and str(obj['id']).isdigit():
                return str(obj['id'])
            for value in obj.values():
                found = find_id(value)
                if found:
                    return found
        if isinstance(obj, list):
            for value in obj:
                found = find_id(value)
                if found:
                    return found
        return None
    message_id = find_id(result)
    if not message_id:
        raise RuntimeError('Discord command returned without a message ID; delivery unverified, do not automatically retry')
    return message_id


def append_log(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text() if path.exists() else ''
    sections = re.split(r'(?m)^## Run ', previous)[1:]
    body = '## Run ' + report['started_at'] + '\n\n'
    body += f"Date checked: {report['date']} ({report['timezone']})\n\nMode: {report['mode']}\n\n"
    for item in report['results']:
        body += '- ' + json.dumps(item, ensure_ascii=False) + '\n'
    if not report['results']:
        body += '- No campaigns with a matching weekday folder.\n'
    text = '# Game Night Check Log\n\nThe five most recent runs, newest first.\n\n' + body
    text += ''.join('\n## Run ' + section for section in sections[:4])
    fd, temp = tempfile.mkstemp(prefix='.game-night-check-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def run(config, day, post=False, fetcher=fetch_events, sender=send_notice):
    tz = ZoneInfo(config['calendar'].get('timezone', 'America/Chicago'))
    settings = config.get('game_night_check', {})
    root = Path(config['local_paths']['obsidian_campaigns_root']).expanduser()
    report = {'started_at': datetime.now(tz).isoformat(), 'date': day.isoformat(),
              'timezone': str(tz), 'mode': 'post' if post else 'dry-run', 'results': []}
    folders = {p.name for p in root.iterdir() if p.is_dir()}
    rows = config.get('campaigns', {})
    by_folder = {}
    for slug, row in rows.items():
        folder = row.get('obsidian_campaign_folder')
        if folder:
            by_folder.setdefault(folder, []).append((slug, row))
    candidates = []
    for folder in sorted(folders):
        m = re.match(r'^([1-7])[a-z]?\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+-\s+.+$', folder, re.I)
        if not m:
            continue
        if int(m[1]) - 1 != WEEKDAYS.index(m[2].capitalize()):
            report['results'].append({'campaign': folder, 'status': 'error', 'detail': 'Folder weekday and number disagree'})
            continue
        if int(m[1]) != day.isoweekday():
            continue
        entries = by_folder.get(folder, [])
        if len(entries) != 1:
            report['results'].append({'campaign': folder, 'status': 'skipped', 'detail': 'Missing or ambiguous registry mapping'})
            continue
        slug, row = entries[0]
        if row.get('status') != 'active' or row.get('workflow_mode') == 'recording_trash_only':
            report['results'].append({'campaign': slug, 'status': 'skipped', 'detail': 'Campaign is not active for this workflow'})
            continue
        if row.get('game_night_check', {}).get('enabled') is False:
            report['results'].append({'campaign': slug, 'status': 'skipped', 'detail': 'Explicitly disabled'})
            continue
        candidates.append((slug, row))
    if not candidates:
        return report
    try:
        start = datetime.combine(day, time.min, tz)
        days = int(settings.get('lookahead_days', 366))
        if days < 2 or days > 730:
            raise ValueError('lookahead_days must be between 2 and 730')
        end = datetime.combine(day + timedelta(days=days), time.min, tz)
        events = fetcher(config, start, end)
    except Exception as exc:
        report['results'].append({'status': 'error', 'detail': str(exc), 'scope': 'Calendar lookup; no notices sent'})
        return report
    for slug, row in candidates:
        try:
            item = {'campaign': slug, **inspect_campaign(row, events, day, tz)}
            target = row.get('game_night_check', {})
            channel = str(target.get('scheduling_channel_id') or '')
            item['channel_name'] = target.get('scheduling_channel_name')
            if item['status'] == 'no_game':
                if not post:
                    item['status'] = 'would_post' if channel.isdigit() else 'preview_unconfigured'
                elif settings.get('posting_enabled') is not True:
                    item.update(status='skipped', detail='Global posting_enabled is not true')
                elif target.get('enabled') is not True or not channel.isdigit():
                    item.update(status='skipped', detail='Campaign requires enabled: true and a numeric scheduling_channel_id')
                else:
                    item['message_id'] = sender(channel, item['message'])
                    item['status'] = 'posted'
            report['results'].append(item)
        except Exception as exc:
            report['results'].append({'campaign': slug, 'status': 'error', 'detail': str(exc)})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=REGISTRY)
    parser.add_argument('--post', action='store_true', help='Allow delivery, subject to registry opt-ins')
    parser.add_argument('--date', type=date.fromisoformat, help='Dry-run date override (YYYY-MM-DD)')
    args = parser.parse_args()
    if args.post and args.date:
        parser.error('--date cannot be combined with --post')
    config = yaml.safe_load(args.registry.read_text())
    tz = ZoneInfo(config['calendar'].get('timezone', 'America/Chicago'))
    day = args.date or datetime.now(tz).date()
    root = Path(config['local_paths']['obsidian_rpg_root']).expanduser()
    log = root / 'AI/Game Night Check Log.md'
    # Serialize runs, including log retention and any deliveries.
    with (Path(__file__).parent / '.run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = run(config, day, args.post)
        append_log(log, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if any(x['status'] == 'error' for x in report['results']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
