"""仅用于合成隔离测试：明确模拟用户跳过项目并同意正式练习。"""
from lawstudy.experience import onboarding_action


def confirm_formal(service):
    onboarding_action(service, "project", {"status": "skipped", "user_instruction": "合成用户选择跳过桌面项目，仅验证隔离实例"}, service.store.snapshot()[1])
    onboarding_action(service, "start_formal", {"user_instruction": "合成用户明确开始本隔离样本的正式练习"}, service.store.snapshot()[1])
