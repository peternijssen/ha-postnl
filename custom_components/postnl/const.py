from homeassistant.const import Platform

DOMAIN = "postnl"

PLATFORMS = [
    Platform.SENSOR,
    Platform.IMAGE,
]

POLL_INTERVAL = 300  # seconds (5 minutes)

CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7
