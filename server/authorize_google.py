"""One-time Google Calendar authorisation. Run once on the machine that runs the bot.

Before running:
1. Go to https://console.cloud.google.com and create a project (any name).
2. APIs & Services -> Library -> enable "Google Calendar API".
3. APIs & Services -> OAuth consent screen -> External -> fill the app name and your
   email -> add the calendar owner's Google address under "Test users".
4. APIs & Services -> Credentials -> Create credentials -> OAuth client ID ->
   Application type "Desktop app" -> Download JSON.
5. Save that file as server/google_credentials.json.

Then:  python authorize_google.py

A browser opens; sign in as the calendar owner and allow access. The token is saved
to server/google_token.json and the bot can book from then on. Both files are
git-ignored and must never be shared.
"""

import sys

from booking import CREDENTIALS_FILE, SCOPES, TOKEN_FILE, _service


def main() -> None:
    if not CREDENTIALS_FILE.exists():
        sys.exit(f"Missing {CREDENTIALS_FILE}. Follow the steps at the top of this file.")

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {TOKEN_FILE}")

    # events.list is covered by the events-only scope and reports the calendar's name and
    # timezone; calendars.get would need a broader scope.
    res = _service().events().list(calendarId="primary", maxResults=1).execute()
    print(f"Connected to calendar: {res.get('summary')} ({res.get('timeZone')})")
    print("Set BUSINESS_TIMEZONE / the intake 'Timezone' field to match if it differs.")


if __name__ == "__main__":
    main()
