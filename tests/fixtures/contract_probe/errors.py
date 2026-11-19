from stapel_core.django.api.errors import register_service_errors

ERR_400_PROBE_CODE = "error.400.probe_code"
ERR_409_PROBE_TAKEN = "error.409.probe_taken"
ERR_423_PROBE_LOCKED = "error.423.probe_locked"
ERR_403_PROBE_CLOSED = "error.403.probe_closed"

register_service_errors({
    ERR_400_PROBE_CODE: "The code is wrong.",
    ERR_409_PROBE_TAKEN: "Already taken.",
    ERR_423_PROBE_LOCKED: "Locked for {minutes} minutes.",
    ERR_403_PROBE_CLOSED: "Closed.",
}, owner="contract_probe", language="en")
