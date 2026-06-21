from enum import StrEnum

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


class ParcelStatus(StrEnum):
    REGISTERED = "registered"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    AT_PICKUP_POINT = "at_pickup_point"
    DELIVERED = "delivered"
    RETURNING = "returning"
    PROBLEM = "problem"
    UNKNOWN = "unknown"
