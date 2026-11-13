"""Explicit short-lived pair transactions. May temporarily block physical app input.
No automatic retry; old interfaces unchanged; supervisor owns cursor lifecycle.
"""
import json,os,subprocess,sys,time
from .auxiliary_guard import AuxInput

def validate(actions,window):
 if type(window) is not int or window<=1:raise ValueError('Explicit target window required')
 if not isinstance(actions,list) or not 1<=len(actions)<=128:raise ValueError('1..128 actions required')
 total=0
 for a in actions:
  if not isinstance(a,dict):raise ValueError('Action must be an object')
  op=a.get('op');fields={'move':{'op','x','y'},'click':{'op','x','y','button'},'text':{'op','text'},'scroll':{'op','x','y','direction','steps'}}
  if op not in fields or set(a)!=fields[op]:raise ValueError('Unknown or incomplete action')
  if op in ('move','click','scroll'):
   if any(type(a[k]) is not int or not 0<=a[k]<=8192 for k in ('x','y')):raise ValueError('Invalid coordinates')
  if op=='click' and (type(a['button']) is not int or not 1<=a['button']<=3):raise ValueError('Button must be 1..3')
  if op=='scroll' and (a['direction'] not in ('up','down','left','right') or type(a['steps']) is not int or not 1<=a['steps']<=10):raise ValueError('Invalid scroll')
  if op=='text':
   s=a['text']
   if not isinstance(s,str) or not s or any(not(32<=ord(c)<127 or c in '\n\t') for c in s):raise ValueError('Mapped ASCII text only; no IME state changes')
   total+=len(s)
 if total>4096:raise ValueError('At most 4096 characters per bounded transaction')

def budget(actions,max_seconds):
 if type(max_seconds) not in (int,float) or not 5<=max_seconds<=120:raise ValueError("Budget must be 5..120 seconds")
 estimate=3+sum(len(a.get("text",""))*.022+a.get("steps",0)*.02+.02 for a in actions)
 if estimate>max_seconds:raise ValueError("Batch exceeds time budget; split explicitly before running")
 return max_seconds

class BatchFailure(RuntimeError):
 def __init__(self,message,cleanup,transaction):
  super().__init__(message);self.cleanup=cleanup;self.transaction=transaction

def execute(owner,actions,window,max_seconds):
 # The lease holder creates and supervises this native worker. Its deadline,
 # cancellation, and parent-death machinery applies to the whole batch.
 return owner.run('batch',dict(actions=actions,window=window,max_seconds=max_seconds))

def run_batch(actions,window,*,acknowledge_keyboard_interruption=False,max_seconds=120,owner=None,cancel_event=None):
 if os.environ.get('SEMANTIC_CU_ENABLE_AUX') != '1':raise PermissionError('Auxiliary input is disabled by default; explicit SEMANTIC_CU_ENABLE_AUX=1 is required')
 if acknowledge_keyboard_interruption is not True:raise PermissionError('Explicit acknowledgement of temporary physical-keyboard interruption required')
 validate(actions,window);budget(actions,max_seconds)
 if cancel_event is not None and cancel_event.is_set():raise RuntimeError('Batch cancelled before creation')
 a=owner if owner is not None else AuxInput(session_seconds=max_seconds)
 previous_seconds=a.session_seconds;a.session_seconds=max_seconds
 start=time.monotonic();ready=None;done=None;failure=None
 try:
  a.start();ready=time.monotonic()
  if cancel_event is not None and cancel_event.is_set():raise RuntimeError('Batch cancelled')
  result=execute(a,actions,window,max_seconds);done=time.monotonic()
 except BaseException as exc:failure=exc
 finally:
  cleanup_start=time.monotonic()
  try:
   cleanup=a.close()
   if cleanup.get('active') is not False:raise RuntimeError('Cleanup not confirmed')
  except BaseException as exc:
   raise BatchFailure('Cleanup failed: '+str(exc),{'confirmed':False,'cleanup_pending':True},a.name) from exc
  finally:a.session_seconds=previous_seconds
  finished=time.monotonic()
 if failure is not None:raise BatchFailure(str(failure),cleanup,a.name) from failure
 return dict(result=result,cleanup=cleanup,milliseconds=dict(create=round((ready-start)*1000,2),operations=round((done-ready)*1000,2),remove=round((finished-cleanup_start)*1000,2),total=round((finished-start)*1000,2)),keyboard_recovery='device pair removed; application recovery timing requires user verification')

def native(payload):
 from . import x11_pair as n
 from .auxiliary_events import keyboard,pointer,raw_input
 validate(payload['actions'],payload['window']);n.NAME=payload['name'];t=n.Twin();completed=0
 try:
  for a in payload['actions']:
   op=a['op'];args={k:v for k,v in a.items() if k!='op'}
   if op=='move':t.move(a['x'],a['y'])
   elif op=='text':keyboard(t,payload['window'],text=a['text'])
   elif op=='scroll':pointer(t,'scroll',args)
   else:
    args.update(window_id=payload['window'])
    try:raw_input(t,'button_down',args)
    finally:raw_input(t,'button_up',{'button':a['button'],'window_id':payload['window']})
   completed+=1
  return dict(completed=completed)
 finally:t.close()

if __name__=='__main__':
 if sys.argv[1:]!=['--native']:raise SystemExit('Internal native worker only; use run_batch with explicit acknowledgement')
 from .auxiliary_guard import arm_parent_death
 arm_parent_death(int(os.environ['SEMANTIC_CU_TRANSACTION_PARENT']))
 print(json.dumps(native(json.load(sys.stdin))))
