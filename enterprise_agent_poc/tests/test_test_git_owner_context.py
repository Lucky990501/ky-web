import importlib.util,os
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('owner_context_tests',Path(__file__).resolve().parents[1]/'scripts/test_git_owner_context.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class OwnerTests(unittest.TestCase):
 def setUp(self):
  self.mock=patch.object(p,'authorize',return_value={});self.mock.start()
  self.stamp=patch.object(p,'repo_stamp',return_value=('stable',));self.stamp.start()
  self.calls=[]
 def tearDown(self):self.stamp.stop();self.mock.stop()
 def result(self,args):
  self.calls.append(list(map(str,args)))
  return '\n'.join(p.REPOS['runtime'][1:])+'\n' if 'rev-parse' in args else ''
 def test_root_owner_identity(self):
  with patch.object(p,'execute',side_effect=self.result):self.assertEqual(p.identity('runtime')['owner_uid'],1000)
 def test_sudo_uid_absent(self):
  with patch.dict(os.environ,{},clear=True),patch.object(p,'execute',side_effect=self.result):self.assertEqual(p.identity('runtime')['source_commit'],p.REPOS['runtime'][1])
 def test_wrong_commit(self):
  with self.assertRaisesRegex(p.OwnerContextBlocked,'WRONG_COMMIT'):p.identity('runtime','0'*40)
 def test_wrong_tree(self):
  with self.assertRaisesRegex(p.OwnerContextBlocked,'WRONG_TREE'):p.identity('runtime',expected_tree='0'*40)
 def test_wrong_git_identity(self):
  with patch.object(p,'execute',return_value='0'*40+'\n'+'1'*40),self.assertRaisesRegex(p.OwnerContextBlocked,'EXACT_OWNER'):p.identity('runtime')
 def test_non_authorized_repo(self):
  with self.assertRaisesRegex(p.OwnerContextBlocked,'NON_AUTHORIZED'):p.identity('/tmp/attacker')
 def test_wrong_path_and_owner(self):
  self.stamp.stop()
  with patch.object(Path,'is_dir',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=0,st_mode=0o555)),self.assertRaisesRegex(p.OwnerContextBlocked,'OWNER'):p.repo_stamp('runtime')
  self.stamp.start()
 def test_symlink_repo(self):
  self.stamp.stop()
  with patch.object(Path,'is_dir',return_value=True),patch.object(Path,'is_symlink',return_value=True),self.assertRaisesRegex(p.OwnerContextBlocked,'PATH'):p.repo_stamp('runtime')
  self.stamp.start()
 def test_production_reject(self):
  self.mock.stop()
  with patch.dict(os.environ,{'APP_ENV':'production'}),self.assertRaisesRegex(p.OwnerContextBlocked,'PRODUCTION'):p.authorize()
  self.mock.start()
 def test_no_root_execution_writable_tooling(self):
  with self.assertRaisesRegex(p.OwnerContextBlocked,'WRITABLE_TOOLING'):p.native_module()
 def test_argv_fixed_executable_and_no_sudo_uid(self):
  with patch.object(p.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='safe')) as call:
   p.execute([p.GIT,'--version']);args,kw=call.call_args
   self.assertEqual(args[0],[str(p.SUDO),'-n','-u','lucky','--',str(p.GIT),'--version'])
   self.assertNotIn('SUDO_UID',kw['env']);self.assertNotIn('shell',kw)
 def test_replacement_reject(self):
  with patch.object(p,'repo_stamp',side_effect=[('before',),('after',)]),patch.object(p,'execute',side_effect=self.result),self.assertRaisesRegex(p.OwnerContextBlocked,'REPLACED'):p.identity('runtime')
 def test_dirty_source_reject(self):
  with patch.object(p,'execute',side_effect=[self.result(['rev-parse']),'dirty']),self.assertRaisesRegex(p.OwnerContextBlocked,'DIRTY'):p.identity('runtime')
 def test_guard_unchanged_paths(self):
  with patch.object(p,'execute',side_effect=self.result):p.identity('runtime')
  self.assertTrue(all(call[0]==str(p.GIT) for call in self.calls))
  self.assertFalse(any('safe.directory' in arg for call in self.calls for arg in call))
if __name__=='__main__':unittest.main()
