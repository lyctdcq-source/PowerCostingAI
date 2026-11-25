def validate_fields(fields):
    values = {row['field']: row['value'] for row in fields}
    if '接地装置套数' in values and '杆塔总基数' in values:
        if float(values['接地装置套数']) != float(values['杆塔总基数']):
            return ['接地装置套数与杆塔总基数不同，请核查图纸。']
    return []
