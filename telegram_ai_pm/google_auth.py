"""Run locally once: python google_auth.py. Requires secrets/google_client.json."""
import json, os
from google_auth_oauthlib.flow import InstalledAppFlow
SCOPES=['https://www.googleapis.com/auth/calendar.readonly']
os.makedirs('secrets',exist_ok=True)
flow=InstalledAppFlow.from_client_secrets_file('secrets/google_client.json',SCOPES)
creds=flow.run_local_server(port=0)
with open('secrets/google_token.json','w') as f: f.write(creds.to_json())
print('Saved secrets/google_token.json; do not commit or share it.')
