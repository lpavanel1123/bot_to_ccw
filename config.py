import os
from dotenv import load_dotenv

load_dotenv()

WEBEX_BOT_TOKEN = os.getenv("WEBEX_BOT_TOKEN")
WEBEX_USER_TOKEN = os.getenv("WEBEX_USER_TOKEN")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "5"))
RESPONSE_TIMEOUT = int(os.getenv("RESPONSE_TIMEOUT", "120"))

if not WEBEX_BOT_TOKEN:
    raise EnvironmentError("WEBEX_BOT_TOKEN não definido no .env")
if not WEBEX_USER_TOKEN:
    raise EnvironmentError("WEBEX_USER_TOKEN não definido no .env")
