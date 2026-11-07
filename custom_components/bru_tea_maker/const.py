"""Constants for the BRU Tea Maker integration."""

from datetime import timedelta
import logging

DOMAIN = "bru_tea_maker"
LOGGER = logging.getLogger(__package__)

STATUS_UUID = "aae28f01-71b5-42a1-8c3c-f9cf6ac969d0"
COMMAND_UUID = "aae28f02-71b5-42a1-8c3c-f9cf6ac969d0"

# BRU only reports its status when asked. Its prompts (place cup, add water)
# come and go within seconds, so it is asked more often while awake.
STANDBY_UPDATE_INTERVAL = timedelta(seconds=5)
AWAKE_UPDATE_INTERVAL = timedelta(seconds=1)

# Settings for the next brew, keyed like the number entities.
DEFAULT_SETTINGS = {"temperature": 90, "water_amount": 250, "brew_time": 180}
# Name the tea maker records for brews started from Home Assistant.
BREW_NAME = "Home Assistant"

STATUS_STANDBY = 1
STATUS_BREWING = 5

# First status byte.
STATUS_NAMES = {
    1: "standby",
    2: "function_menu",
    3: "machine_setup",
    4: "machine_setup",
    5: "brewing",
    6: "dispensing",
    7: "alarm_timer",
    8: "cold_tea",
    9: "split_cup",
    10: "multi_steeping",
    11: "info_message",
    12: "warning_message",
    13: "error_message",
    14: "hot_tea",
    15: "test_mode",
    16: "test_mode",
    17: "test_mode",
    18: "test_mode",
    19: "test_mode",
    20: "test_mode",
    21: "drying",
    22: "descaling",
    23: "lid_reminder",
    24: "bluetooth_menu",
}

# Second status byte while brewing.
BREW_PHASES = {
    25: "filling",
    26: "steeping",
    27: "rinse_delay",
    28: "serving",
    29: "rinsing",
    30: "enjoy",
    31: "cold_water",
    32: "hot_water",
}

# Second status byte while a prompt is on the screen.
MESSAGE_NONE = "none"
MESSAGES = {
    120: "place_cup",
    121: "remove_cup",
    122: "close_lid",
    123: "open_lid",
    124: "add_water",
    125: "insert_chamber",
    126: "no_nozzle",
    127: "error",
    128: "continue",
    129: "cancel",
    130: "next_cup",
    131: "cup_removed",
    132: "place_descaling",
    133: "check_engine",
}
