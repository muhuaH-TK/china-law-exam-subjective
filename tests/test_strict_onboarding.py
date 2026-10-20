"""明确模拟用户回答的隔离回归；不代表真实客户端或用户授权。"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from support import confirm_formal
from lawstudy.desktop import bootstrap, context, dispatch, service_for, create_test
from lawstudy.errors import error_result
from lawstudy.experience import onboarding_action, study_allowed
from lawstudy.mcp import call
from lawstudy.migration import migration_action, inventory
from lawstudy.onboarding import CONFIG_CHOICES, TEXT_CHOICES, setup_state
from lawstudy.paths import io_path
from lawstudy.store import ConflictError


class StrictOnboardingTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent/'.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)/'strict'
        bootstrap(self.workspace, 'codex', '合成用户选择隔离程序检查目录')
        self.service = service_for(self.workspace)[0]
        self.onboard('project', status='skipped')

    def version(self):return self.service.store.snapshot()[1]

    def onboard(self, action, **payload):
        return onboarding_action(self.service, action, {'user_instruction':'合成用户明确选择', **payload}, self.version())

    def test_defaults_do_not_count_as_answers_or_allow_start(self):
        state = setup_state(self.service.store.snapshot()[0])
        self.assertEqual(set(state['missing']), set(CONFIG_CHOICES + TEXT_CHOICES))
        with self.assertRaisesRegex(ValueError, '引导'):
            self.onboard('start_formal', summary_hash=state['summary_hash'])
        self.assertFalse(study_allowed(self.service.store.snapshot()[0]))

    def test_each_entry_requires_and_preserves_settings_instruction(self):
        version = self.version()
        for entry in ('direct', 'desktop', 'mcp'):
            with self.subTest(entry=entry), self.assertRaises((ValueError, KeyError)):
                if entry == 'direct':self.service.configure({'year':2027}, version)
                elif entry == 'desktop':dispatch(self.workspace, {'action':'configure','payload':{'year':2027},'version':version})
                else:call(self.service, 'configure_instance', {'config':{'year':2027},'version':version})
            self.assertEqual(self.version(), version)
        dispatch(self.workspace, {'action':'configure','payload':{'year':2027},'version':version,'user_instruction':'合成用户明确备考2027'})
        self.assertEqual(self.service.store.snapshot()[0]['settings_history'][-1]['user_instruction'], '合成用户明确备考2027')
        self.assertIn('year', setup_state(self.service.store.snapshot()[0])['missing'])

    def test_explicit_undecided_and_no_materials_are_valid_answers(self):
        confirm_formal(self.service)
        data = self.service.store.snapshot()[0]
        self.assertEqual(data['config']['elective'], 'undecided')
        self.assertFalse(data['materials'])
        self.assertTrue(setup_state(data)['confirmed'])

    def test_missing_one_answer_and_wrong_summary_cannot_start(self):
        confirm_formal(self.service)
        def remove(data):data['onboarding']['answers'].pop('elective')
        self.service.store.update(self.version(), remove)
        with self.assertRaisesRegex(ValueError, '引导'):
            self.onboard('start_formal', summary_hash=setup_state(self.service.store.snapshot()[0])['summary_hash'])
        self.onboard('answer', field='elective', value='undecided')
        with self.assertRaisesRegex(ValueError, '摘要'):
            self.onboard('start_formal', summary_hash='not-current')

    def test_changed_settings_require_reconfirmation_and_keep_history(self):
        confirm_formal(self.service)
        before = deepcopy(self.service.store.snapshot()[0]['onboarding']['confirmations'])
        self.service.configure({'elective':'administrative'}, self.version(), '合成用户选择行政法')
        state = setup_state(self.service.store.snapshot()[0])
        self.assertEqual(state['missing'], ['elective'])
        self.assertFalse(study_allowed(self.service.store.snapshot()[0]))
        self.assertEqual(self.service.store.snapshot()[0]['onboarding']['confirmations'], before)
        self.onboard('answer', field='elective', value='administrative')
        self.onboard('start_formal', summary_hash=setup_state(self.service.store.snapshot()[0])['summary_hash'])
        self.assertTrue(study_allowed(self.service.store.snapshot()[0]))

    def test_test_instance_is_not_exempt_from_strict_setup(self):
        original = self.service.store.path.read_bytes()
        result = create_test(self.workspace, '合成用户只要求建立独立测试')
        child = service_for(result['test_workspace'])[0]
        self.assertEqual(self.service.store.path.read_bytes(), original)
        state = context(result['test_workspace'])
        self.assertEqual(state['status'], 'needs_onboarding')
        self.assertFalse(state['experience']['test_options']['skip_materials'])
        self.assertTrue(state['experience']['onboarding']['missing'])
        self.assertTrue(all(not row['eligible'] for row in state['automation']['tasks']))
        with self.assertRaisesRegex(ValueError, '引导'):
            onboarding_action(child, 'start_test', {'user_instruction':'合成仅要求测试，未回答设置'}, child.store.snapshot()[1])

    def test_error_types_and_valid_single_quote(self):
        self.assertEqual(json.loads(json.dumps({'answer':"'构成'"}))['answer'], "'构成'")
        try:json.loads('{broken')
        except json.JSONDecodeError as exc:
            result=error_result(exc)
            self.assertEqual(result['code'], 'invalid_json')
            self.assertIn('column', result)
        self.assertEqual(error_result(ConflictError('版本变化'))['code'], 'version_conflict')
        self.assertFalse(error_result(ValueError('缺少授权'))['retry_automatically'])

    def test_long_backup_and_migration_preserve_bytes_and_identity(self):
        # Reproduce a real >260-character backup path even in short CI checkout roots.
        deep = Path(self.temp.name)
        while len(str(deep)) < 175:deep = deep/('deep-'+'x'*18)
        try:
            workspace = deep/'workspace'
            bootstrap(workspace, 'codex', '合成用户指定深层目录，检查Windows长路径')
            service, project = service_for(workspace)
            confirm_formal(service)
            raw = b'synthetic long path migration payload'
            from lawstudy.store import atomic_bytes
            original = service.store.directory/'extracted'/('a'*64+'.txt')
            atomic_bytes(original, raw)
            target = Path(self.temp.name)/'moved'
            prepared = migration_action(service, project, 'prepare', {'target_workspace':str(target),'user_instruction':'合成用户明确迁移'}, service.store.snapshot()[1])
            backed = Path(prepared['backup'])/'files/extracted'/original.name
            self.assertGreater(len(str(backed)), 260)
            self.assertEqual(io_path(backed).read_bytes(), raw)
            moved = migration_action(service, project, 'execute', {'user_instruction':'合成用户继续迁移'}, service.store.snapshot()[1])
            self.assertEqual(io_path(Path(moved['data_dir'])/'extracted'/original.name).read_bytes(), raw)
            self.assertEqual(moved['instance_id'], service.store.snapshot()[0]['instance_id'])
            self.assertTrue(io_path(original).exists())
        finally:
            top = Path(self.temp.name)/deep.relative_to(self.temp.name).parts[0]
            if io_path(top).exists():shutil.rmtree(io_path(top))
