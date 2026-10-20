"""仅用于合成隔离测试：明确模拟用户跳过项目并同意正式练习。"""
from lawstudy.experience import onboarding_action, project_choice
from lawstudy.onboarding import CONFIG_CHOICES, TEXT_CHOICES, setup_state


def confirm_formal(service):
    """单测fixture显式模拟每项回答；发行运行时绝不调用此测试工具。"""
    data, version = service.store.snapshot()
    missing = {}
    if not data['config']['year']:missing['year'] = 2027
    if data['config']['participation'] == 'undecided':missing['participation'] = 'participating'
    if missing:service.configure(missing, version, '合成用户明确选择2027年参加，仅用于隔离程序检查')
    if project_choice(data)['status'] == 'pending':
        onboarding_action(service, 'project', {'status':'skipped','user_instruction':'合成用户明确跳过桌面项目'}, service.store.snapshot()[1])
    for field in CONFIG_CHOICES + TEXT_CHOICES:
        data, version = service.store.snapshot()
        value = data['config'][field] if field in CONFIG_CHOICES else '合成用户明确暂不提供／不安装；仅用于隔离程序检查'
        onboarding_action(service, 'answer', {'field':field,'value':value,'user_instruction':'合成用户逐项确认：'+field}, version)
    data, version = service.store.snapshot()
    onboarding_action(service, 'start_test' if data.get('instance_kind') == 'test' else 'start_formal',
        {'summary_hash':setup_state(data)['summary_hash'],'user_instruction':'合成用户确认完整设置摘要并开始隔离样本'}, version)
