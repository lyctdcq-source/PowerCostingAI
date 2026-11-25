def classify_price(delta, significant_threshold):
    deviation = abs(delta)
    if deviation <= 2:
        return '一致'
    if deviation <= 5:
        return '基本一致'
    if deviation <= significant_threshold:
        return '偏差'
    return '显著偏差'
