def is_anomaly(price, previous, threshold):
    if previous is None or previous <= 0:
        return False
    return abs((price - previous) / previous * 100) > threshold
