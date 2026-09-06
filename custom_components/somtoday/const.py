"""Constants for the SOMtoday integration."""

DOMAIN = "somtoday"

CONF_SCHOOL_UUID = "school_uuid"
CONF_SCHOOL_NAME = "school_name"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

AUTH_URL = "https://inloggen.somtoday.nl/oauth2/token"
AUTHORIZE_URL = "https://inloggen.somtoday.nl/oauth2/authorize"
REDIRECT_URI = "somtoday://nl.topicus.somtoday.leerling/oauth/callback"

# SOMtoday removed its public organisations endpoint in April 2025.  This is
# the snapshot linked by the maintainers of the unofficial API documentation.
# Config flow always offers a manual UUID fallback, so setup does not depend on
# this third-party file remaining available.
SCHOOLS_URLS = (
    "https://raw.githubusercontent.com/NONtoday/leerling-source/refs/heads/main/organisaties.json",
    "https://github.com/NONtoday/leerling-source/raw/refs/heads/main/organisaties.json",
)

# Current public client used by the native SOMtoday Leerling application.
CLIENT_ID = "somtoday-leerling-native"
SCOPE = "openid"

DEFAULT_SCAN_INTERVAL_MINUTES = 10
