"""Run unittest regression tests in a temporary app copy with external sockets blocked."""
import hashlib, json, os, pathlib, shutil, subprocess, tempfile
root = pathlib.Path(__file__).resolve().parents[1]
before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'data').rglob('*.json')}
run = pathlib.Path(tempfile.mkdtemp(prefix='ticket-governance-tests-'))
for name in ('agent','mock_api','api','data','test','frontend'):
 shutil.copytree(root/name, run/name, ignore=shutil.ignore_patterns('__pycache__','node_modules','dist'))
shutil.copy(root/'vercel.json',run/'vercel.json')
(run/'.env').write_text('')
runner = '''import socket, sys, unittest, json
sys.path.insert(0, '.')
def deny(*a, **k): raise AssertionError('External network is blocked for offline tests')
socket.socket.connect = deny
socket.create_connection = deny
suite = unittest.defaultTestLoader.discover('test')
result = unittest.TextTestRunner(verbosity=2).run(suite)
with open('results.json','w') as f: json.dump({'run':result.testsRun,'failures':[(str(t),err) for t,err in result.failures],'errors':[(str(t),err) for t,err in result.errors],'skipped':[(str(t),reason) for t,reason in result.skipped]},f,indent=2)
sys.exit(not result.wasSuccessful())
'''
(run/'run_tests.py').write_text(runner)
env = {k:v for k,v in os.environ.items() if not k.startswith(('OPENAI','VERCEL','ENABLE_LIVE_AI','DEMO_'))}
env['PYTHONDONTWRITEBYTECODE']='1'
with (run/'execution.log').open('w') as out:
 result = subprocess.run([str(root/'.venv/bin/python'), 'run_tests.py'],cwd=run,env=env,stdout=out,stderr=subprocess.STDOUT)
after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'data').rglob('*.json')}
print('Artifacts:',run)
(run/'isolation.json').write_text(json.dumps({'original_json_unchanged':before==after, 'before_sha256':before, 'after_sha256':after, 'external_socket_connections':'blocked', 'application_env_file':'empty temporary file'}, indent=2))
print('Original JSON unchanged:',before==after)
print((run/'results.json').read_text())
raise SystemExit(result.returncode)
