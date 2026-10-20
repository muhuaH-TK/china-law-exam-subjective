"""正式与测试共用的逐项引导；默认值和已有文件不代表用户作过选择。"""
from copy import deepcopy
from hashlib import sha256
import json

CONFIG_CHOICES = ('year', 'participation', 'elective', 'selected_ai', 'timezone', 'authorization', 'automation')
TEXT_CHOICES = ('materials_course', 'sources', 'auxiliary_skills')
LABELS = dict(zip(CONFIG_CHOICES + TEXT_CHOICES, (
    '备考年度', '是否参加主观题', '行政法／商法／明确未定', '使用的AI客户端', '时区',
    '在线AI、检索及资料处理范围', '每日学习与监测任务及时间', '课程与资料安排', '监测来源安排', '辅助技能选择')))


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def setup_state(data):
    records = data.get('onboarding', {})
    answers = records.get('answers', {})
    rows = []
    values = {}
    for key, label in LABELS.items():
        answer = answers.get(key)
        value = data['config'][key] if key in CONFIG_CHOICES else answer.get('value') if answer else None
        valid_value = (value is not None and (key != 'participation' or value != 'undecided'))
        ready = bool(answer and answer.get('user_instruction') and valid_value and answer.get('value') == value)
        rows.append({'field': key, 'name': label, 'ready': ready,
                     'status': 'confirmed' if ready else 'changed' if answer else 'unasked', 'value': deepcopy(value)})
        values[key] = value
    fingerprint = digest(values)
    complete = all(row['ready'] for row in rows)
    confirmed = bool(complete and records.get('confirmation', {}).get('summary_hash') == fingerprint)
    return {'steps': rows, 'missing': [r['field'] for r in rows if not r['ready']],
            'choices_complete': complete, 'summary_hash': fingerprint, 'summary': values, 'confirmed': confirmed,
            'instruction': '测试与正式均须逐项真实引导。明确未定／暂不提供／拒绝也是选择；默认值、合成预设、已导入资料均不代替回答。'}


def record_choice(data, payload):
    from .learning import required
    from .store import utc_now
    field = payload.get('field')
    if field not in LABELS:
        raise ValueError('未知引导项目')
    instruction = required(payload.get('user_instruction'), '该项用户真实回答', 10000)
    if field in CONFIG_CHOICES:
        value = deepcopy(data['config'][field])
        if 'value' not in payload or payload['value'] != value:
            raise ValueError('引导确认值须与当前设置一致；先保存设置再确认，不默认代填')
        if value is None or (field == 'participation' and value == 'undecided'):
            raise ValueError('请明确备考年度及是否参加，不能用默认未定代替询问')
    else:
        value = required(payload.get('value'), '用户对该项的选择（可明确暂不提供／拒绝）', 10000)
    row = {'value': value, 'user_instruction': instruction, 'at': utc_now()}
    state = data.setdefault('onboarding', {})
    state.setdefault('history', []).append({'field': field, **deepcopy(row)})
    state.setdefault('answers', {})[field] = row
    return {'field': field, **row}


def confirm_setup(data, payload):
    from .learning import required
    from .store import utc_now
    state = setup_state(data)
    if not state['choices_complete']:
        raise ValueError('严格引导尚未完成：' + '、'.join(LABELS[k] for k in state['missing']))
    if payload.get('summary_hash') != state['summary_hash']:
        raise ValueError('请先在聊天展示当前完整设置摘要，取得用户确认后传入对应summary_hash')
    row = {'summary_hash': state['summary_hash'], 'summary': state['summary'], 'at': utc_now(),
           'user_instruction': required(payload.get('user_instruction'), '用户对完整设置摘要的确认', 10000)}
    data.setdefault('onboarding', {})['confirmation'] = row
    data['onboarding'].setdefault('confirmations', []).append(deepcopy(row))
