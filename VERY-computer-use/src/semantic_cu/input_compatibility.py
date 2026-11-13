"""Read-only conservative MPX preflight. Exact source-audited builds only.
Inventory is installed versions, NOT a claim about all running app processes.
No X connection, screenshots, input logging, app launching or auto-upgrades.
"""
import json,re,subprocess
from pathlib import Path
FIX='https://github.com/chromium/chromium/commit/daacc045502047fd60cbaefb416bb095fea1d3bb'
KNOWN_BAD={'142.0.7444.265','142.0.7444.175','150.0.7871.250','156.0.8078.100','157.0.8103.50','157.0.8103.78','159.0.8144.0','159.0.8154.0','159.0.8154.6'}
KNOWN_FILTER_FIXED={'160.0.8158.0'}
ELECTRON={'39.6.0':'142.0.7444.265','43.8.6':'150.0.7871.250','45.3.2':'156.0.8078.100','47.0.0-nightly.20261112':'159.0.8154.0'}
def assess_chromium(version):
 status='blocked' if version in KNOWN_BAD else 'filter_fixed' if version in KNOWN_FILTER_FIXED else 'unverified'
 return dict(version=version,status=status,full_compatibility=False,evidence=FIX,
  reason='New MPX master keyboard can filter physical keyboard events' if status=='blocked' else 'Only Chromium event-filter fix verified; desktop/IME/menu behavior still requires validation')
def local_report():
 apps=[];blockers=[];errors=[]
 try:
  p=subprocess.run(['dpkg-query','-W','-f=${Version}','google-chrome-stable'],capture_output=True,text=True,timeout=2)
  if p.returncode==0:
   match=re.search(r'\d+\.\d+\.\d+\.\d+',p.stdout)
   apps.append(dict(application='Google Chrome (installed)',**assess_chromium(match.group(0) if match else 'unknown')))
 except (OSError,subprocess.TimeoutExpired) as e:errors.append(type(e).__name__)
 manifest=Path('/usr/share/code/resources/app/package.json')
 if manifest.exists():
  try:
   package=json.loads(manifest.read_text());electron=package.get('devDependencies',{}).get('electron') or package.get('nativeBuild',{}).get('target','unknown')
   apps.append(dict(application='VS Code (installed)',electron=electron,**assess_chromium(ELECTRON.get(electron,'unknown'))))
  except (OSError,ValueError) as e:errors.append(type(e).__name__)
 for app in apps:
  if app['status']!='filter_fixed':blockers.append(app['application']+': '+app['status']+' '+app['version'])
 if errors:blockers.append('Inventory incomplete: '+','.join(errors))
 if not apps:blockers.append('No audited Chromium/Electron inventory; cannot infer desktop safety')
 return dict(scope='installed-version preflight; not exhaustive runtime compatibility',applications=apps,start_blockers=blockers,
  full_compatibility=False,cursor_style=dict(shape='crosshair',color='cyan',label=None,backend='native XI2 cursor when allowed'),
  remaining_validation=['window-manager focus','GTK/Qt app state','IME candidates','menus/grabs','same-process windows','multiple monitors'],
  action_policy='no fallback to physical/core keyboard; unknown is not compatible')
if __name__=='__main__':print(json.dumps(local_report(),ensure_ascii=False,indent=2))
