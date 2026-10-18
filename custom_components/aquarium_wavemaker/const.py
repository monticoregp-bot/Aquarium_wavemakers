"""Constants for Aquarium Wavemaker."""

DOMAIN = "aquarium_wavemaker"
VERSION = "0.1.0"

CONF_MASTER_ENTITY = "master_entity"  # input_boolean: ON = the schedule drives the pumps
CONF_INITIAL_PUMP = "initial_pump"
CONF_INITIAL_SERVICE = "initial_service"
CONF_INITIAL_CHANNEL = "initial_channel"

# Used if an entry has no master entity stored.
DEFAULT_MASTER_ENTITY = "input_boolean.aquarium_wavemaker"

STORAGE_VERSION = 1
FRONTEND_URL = "/aquarium_wavemaker/aquarium-wavemaker-card.js"
