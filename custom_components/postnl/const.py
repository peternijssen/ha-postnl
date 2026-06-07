from homeassistant.const import Platform

DOMAIN = "postnl"

PLATFORMS = [
    Platform.SENSOR
]

POLL_INTERVAL = 300  # seconds (5 minutes)
