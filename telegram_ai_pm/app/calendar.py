import os
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def events_for_day(day_start, day_end):
    path = os.getenv('GOOGLE_TOKEN_FILE', '/app/secrets/google_token.json')
    if not os.path.exists(path):
        return [], 'Google Calendar не подключён'
    try:
        creds = Credentials.from_authorized_user_file(path, SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds.valid:
            return [], 'Токен Google Calendar недействителен; требуется повторная авторизация'
        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        data = service.events().list(calendarId='primary', timeMin=day_start.isoformat(), timeMax=day_end.isoformat(), singleEvents=True, orderBy='startTime', maxResults=100).execute()
        events = []
        for e in data.get('items', []):
            events.append({'title': e.get('summary', 'Без названия'), 'start': e.get('start', {}).get('dateTime', e.get('start', {}).get('date')), 'end': e.get('end', {}).get('dateTime', e.get('end', {}).get('date'))})
        return events, None
    except Exception as exc:
        return [], f'Ошибка доступа к календарю: {type(exc).__name__}'
