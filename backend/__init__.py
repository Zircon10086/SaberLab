"""SaberLab backend package."""

# Stable local-instance identity shared by /api/status and the desktop host.
# The launcher uses this marker before replacing an old listener; do not reuse
# it for another application.
APP_INSTANCE_ID = "saberlab-local-v1"
