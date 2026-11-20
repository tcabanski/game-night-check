# Game Night Check

A small, deterministic Python utility that posts "No game tonight" to a campaign's Discord scheduling channel when its usual weekday arrives without a session on Google Calendar. It includes the next scheduled session using Discord's localized long date-and-time format.

No AI model is required. No scheduler is installed or enabled by this project.

## Requirements

- Linux or another Unix-like system with Python 3.11+ (uses `fcntl` for locking).
- An authenticated `gog` CLI with Google Calendar access.
- A configured Hermes CLI with a Discord bot that can post to the destination channels.
- An external campaign registry and weekday-named campaign directories.

## Setup and run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python game_night_check.py --registry /path/to/private/campaigns.yaml
```

The default is a dry run. It reads Calendar and updates the local run log but never sends Discord messages.

Use `--date YYYY-MM-DD` to preview another day against the calendar's current state. Historical previews are not historical calendar snapshots.

Actual delivery requires `--post`, global `posting_enabled: true`, and per-campaign `enabled: true` with a numeric scheduling channel ID. `--date` cannot be combined with `--post`.

The default registry path is `$HERMES_HOME/skills/rpg/references/session-workflow/campaigns.yaml`, falling back to `~/.hermes/profiles/rpgassistant/skills/rpg/references/session-workflow/campaigns.yaml`. Prefer an explicit `--registry` for other installations. Discord delivery uses the `rpgassistant` Hermes profile unless `GAME_NIGHT_HERMES_PROFILE` overrides it.

## Integration setup

### 1. Obsidian (or ordinary Markdown folders)

Obsidian itself does not need to be running. The utility accesses local directories directly, without an Obsidian plugin or API. Create an `RPG` folder in your vault with a `Campaigns` subfolder. Each participating campaign needs its own directory, for example:

```text
RPG/
  AI/
  Campaigns/
    5 Friday - Example Campaign/
    7 Sunday - Another Campaign/
    7a Sunday - A Second Sunday Campaign/
```

Numbers represent Monday=1 through Sunday=7. Optional lowercase suffixes distinguish campaigns sharing a weekday. The title and spacing must follow the shown pattern. Set `obsidian_campaigns_root` and `obsidian_rpg_root` in your private registry to these local paths, and set each campaign's `obsidian_campaign_folder` to its exact directory name. The utility creates the `AI` log directory if necessary. No session notes or other campaign documents are required, and existing notes are not modified.

Only active, registered campaigns with matching real folders participate. Folder names determine the usual weekday, not the registry's cadence or an event's original recurring date. If a game moves to a different day, the old weekday can receive a no-game notice pointing to that moved session.

### 2. Google Calendar

Install the `gog` CLI and authenticate the Google account that can read your game calendar. Follow your installed version's `gog auth --help`; authentication is external to this utility. Use `gog calendar --help` to find the calendar-listing command for your version, and record the selected calendar ID and Google account in your private registry. The utility currently reads one shared calendar/account per registry, not a separate calendar for every campaign.

Create individual or recurring timed session events. The event title must include the configured canonical campaign name, short name, or an explicitly configured title alias as a distinct phrase. A title such as `Example Campaign #12: Session Title` works. When `calendar_location` is configured, the event Location must match it exactly. Optional recurring-series IDs further constrain identity; standalone replacement events will not match a series-restricted campaign unless configured appropriately. A separate session-zero event can be allowed by its exact ID.

The calendar is authoritative. Delete or cancel only the skipped occurrence of a recurring game, or move the existing occurrence to its new date/time. An ordinary placeholder still on today's calendar counts as a session: the utility cannot infer cancellation from title text, a description, an RSVP, or an off-week pattern. Cancelled events are ignored. Ensure the Calendar event timezone and registry timezone are intentional, particularly across daylight saving changes.

Before enabling posting, run a dry check and confirm that today's sessions and each next session are identified correctly. Conflicting identities are errors to fix, not a reason to weaken matching. Listing Calendar requires read access only; this program never edits it.

### 3. Discord through Hermes

Install and configure Hermes with a Discord bot using the current instructions at https://hermes-agent.nousresearch.com/docs. Configure the intended Hermes profile outside this repository. The bot needs access to each scheduling channel and permission to view the channel and send messages. This utility does not create a bot, invite it, create channels, or configure credentials.

Create or select one scheduling channel per campaign, such as `example-scheduling`. Enable Discord Developer Mode, copy the channel's ID, and verify it belongs to the intended server and category. Store its name and numeric ID under the campaign's `game_night_check` settings. Do not substitute the session-notes channel ID. Keep all tokens in Hermes's credential configuration, never in the campaign registry or source tree.

The sender uses `hermes -p rpgassistant send`; set `GAME_NIGHT_HERMES_PROFILE` if your bot uses another profile. Calendar authentication and Discord authentication are independent. Local tests mock Discord, so channel permissions and the live send-response format remain checks for your first authorized real delivery.

### 4. Enable deliberately

Begin with global `posting_enabled: false` and dry runs. Review the private log and resolve missing mappings. When ready, enable selected campaign rows, set the global opt-in, and invoke with `--post`. Omitting that flag always remains safe from Discord delivery. This repository does not install a cron job or any other schedule. If you later schedule it externally, use an environment that has the correct executables, credentials, profile, filesystem permissions, and timezone configuration. No model invocation is needed.

## Private configuration

Keep your actual registry outside this repository. The following is an illustrative schema only. Replace placeholders locally, and do not publish your real account, IDs, folder paths, or campaign mappings.

```yaml
calendar:
  rpg_calendar_id: YOUR_CALENDAR_ID
  timezone: America/Chicago
google:
  account: YOUR_GOOGLE_ACCOUNT
local_paths:
  obsidian_campaigns_root: ~/your-vault/RPG/Campaigns
  obsidian_rpg_root: ~/your-vault/RPG
game_night_check:
  posting_enabled: false
  lookahead_days: 366
campaigns:
  example-campaign:
    status: active
    obsidian_campaign_folder: "5 Friday - Example Campaign"
    names:
      canonical: Example Campaign
      session_calendar_short: Example Campaign
    calendar_location: YOUR_EXACT_CALENDAR_LOCATION
    # Optional: constrain matching to verified recurring series.
    # calendar_recurring_event_ids: [YOUR_SERIES_ID]
    game_night_check:
      enabled: true
      scheduling_channel_name: example-scheduling
      scheduling_channel_id: "YOUR_VERIFIED_NUMERIC_CHANNEL_ID"
      calendar_title_aliases: []
```

Resolve channels in the correct server/category and verify bot permissions before saving IDs. Channel names are labels; delivery uses IDs. There is no fallback to a session-notes channel.

Explicit campaign `enabled: false` excludes it even from previews. A missing campaign opt-in permits previews but never delivery. Missing global posting opt-in disables delivery.

Existing registries may also provide `calendar_recurring_event_id` (singular) and a `session_zero_event_id` exception, validated alongside the campaign title and exact location. `calendar_title_aliases` adds explicit title phrases when the canonical or short name differs from Calendar. Do not use generic weekdays as aliases.

## Behavior and safeguards

- Discover actual campaign directories named `1 Monday - ...` through `7 Sunday - ...`, including letter suffixes such as `7a Sunday - ...`. Numeric prefix and weekday must agree.
- Require one exact registry mapping and active status. Exclude `recording_trash_only` entries.
- Check the run's local calendar date in the configured timezone, not a rolling 24-hour window. Cadence is deliberately ignored: biweekly off-weeks should receive notices.
- Fetch all pages through the search horizon and inspect each event's local start date. A non-cancelled matching event starting today means no notice, even if it already happened.
- Otherwise announce the earliest future matching session with `<t:EPOCH:F>`. Discord localizes date/time and spells out the month according to the viewer's locale.
- If no future occurrence is found within the horizon, say: "No game tonight. The next session is not currently on the calendar." The log records the horizon limitation.
- A future all-day event cannot supply a verified session time and stops that campaign's notice.
- Name/location/series conflicts stop that campaign. Calendar failures or incomplete pagination stop all notices for the run. Calendar is never modified.
- Direct Hermes delivery avoids scheduler-added message headers. Require a returned message ID before marking delivery verified. Transport failures are not automatically retried because delivery might already have occurred.
- There is no duplicate suppression. A local nonblocking lock prevents overlapping executions.

## Run log

The log is written under the configured RPG root at `AI/Game Night Check Log.md`. It retains only the five newest runs, including dry runs, with atomic file replacement. Entries include the run date, mode, campaign findings, proposed messages, delivery IDs, and errors. Treat it as private operational data.

CLI/configuration failures before the check starts are reported to stderr rather than logged. Keep the program directory writable for its local `.run.lock` file.

## Tests

```sh
.venv/bin/python -m unittest -v
```

Tests use synthetic campaign data, temporary directories, and mock deliveries. They never contact Calendar or Discord. Verify live posting only during an explicitly authorized delivery, not by sending dummy messages.

## Privacy

This repository contains code, tests, and illustrative placeholders only. Keep credentials in the external CLI credential stores. Do not add your real registry, logs, `.env`, virtual environment, or calendar exports to Git. Scheduling and deployment are separate steps.
