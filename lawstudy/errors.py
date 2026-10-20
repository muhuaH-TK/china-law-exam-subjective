"""只解释恢复动作，不自动重放写入或创建任务。"""
import json
from .store import ConflictError


def error_result(exc):
    result = {'error': str(exc), 'retry_automatically': False}
    if isinstance(exc, json.JSONDecodeError):
        result.update(code='invalid_json', line=exc.lineno, column=exc.colno, position=exc.pos,
                      instruction='请求不是合法JSON；AI用json.dump/json.dumps重新序列化完整对象，不手工拼接、不改用户原答。')
    elif isinstance(exc, ConflictError):
        result.update(code='version_conflict', instruction='先读取context最新版本并合并实际修改，再受控重试一次；宿主任务创建结果未知时先查询找回，不能重建。')
    elif isinstance(exc, PermissionError):
        result.update(code='permission_denied', instruction='按正常审批处理具体文件或工具权限，不停用安全控制；未获准则保留进度并报告受阻。')
    elif '规格' in str(exc) or '指纹' in str(exc):
        result.update(code='task_spec_mismatch', instruction='查询真实宿主规格，查看差异后更新原任务并回读；禁止只改后台指纹或创建重复任务。')
    elif '引导' in str(exc) or '设置摘要' in str(exc):
        result.update(code='onboarding_required', instruction='读取experience.onboarding，只询问未回答或已变化的项目；测试也须真实回答，展示完整摘要后等确认。')
    elif '授权' in str(exc) or '允许' in str(exc) or '原话' in str(exc):
        result.update(code='authorization_required', instruction='等待用户真实授权或选择；不能为通过校验自行改为允许，也不把拒绝改成同意。')
    else:
        result.update(code='invalid_request' if isinstance(exc, (ValueError, KeyError, TypeError)) else 'backend_error',
                      instruction='核对错误和当前停靠点，修正请求后再执行；不盲目重试、不伪造完成或回执。')
    return result
