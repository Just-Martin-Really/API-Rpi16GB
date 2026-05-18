"""Pure decision logic for the LSTM control loop.

No I/O, no global state. The control loop calls these to translate a
forecast into a desired heater/cooler state, then compares with the last
sent state to decide what to actually emit.
"""


def desired_state(forecast, low, high, lookahead):
    """Map a forecast to a {"heater": "on"|"off", "cooler": "on"|"off"} dict.

    forecast  : sequence of predicted temperatures (numpy array or list).
    low, high : target band in deg C.
    lookahead : how many leading entries of `forecast` to consider.

    If the minimum predicted temperature within the lookahead window falls
    below `low`, the heater turns on. If the maximum rises above `high`,
    the cooler turns on. Otherwise both are off. Heater and cooler are
    mutually exclusive: an "on" on one always pairs with "off" on the other.
    """
    if lookahead <= 0:
        raise ValueError("lookahead must be > 0")
    if low >= high:
        raise ValueError(f"low ({low}) must be < high ({high})")
    window = list(forecast)[:lookahead]
    if not window:
        raise ValueError("forecast is empty")
    if min(window) < low:
        return {"heater": "on", "cooler": "off"}
    if max(window) > high:
        return {"heater": "off", "cooler": "on"}
    return {"heater": "off", "cooler": "off"}


def diff_state(desired, last_sent):
    """Return only the entries in `desired` that differ from `last_sent`.

    Used by the control loop to avoid re-sending the same command every
    iteration. `last_sent` may be missing keys, in which case the
    corresponding `desired` entry is always returned.
    """
    out = {}
    for key, value in desired.items():
        if last_sent.get(key) != value:
            out[key] = value
    return out
