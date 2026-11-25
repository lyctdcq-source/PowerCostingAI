def include_file(relative_path):
    """决定文件是否进入清洗。"""
    return True


def include_sheet(file_name, sheet_name, is_estimation):
    if not is_estimation:
        return True
    if not any(word in sheet_name for word in ('设备汇总', '材料汇总', '装置性材料')):
        return False
    if any(word in sheet_name for word in ('价差', '配合比', '单公里')):
        return False
    if sheet_name == '乙供设备汇总表' and '临时施工' in file_name:
        return False
    return True


def transform_record(record, is_estimation):
    """规范业务字段；来源与序号由宿主保留。"""
    return record
