def matching_guidance():
    """实际加入在线LLM配对提示词。"""
    return '优先核对物资主体、电压等级、型号、芯数、截面和工程层级。不得仅凭数量相同建立对应。不能确定时保留未匹配。'


def quantity_tolerance():
    """仅离线演示使用此数量容差；在线复核使用原有引擎。"""
    return 0.01
