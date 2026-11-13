"""One native 10x10 yellow square per owned pair; no overlay windows.
No overlay windows. Never targets a physical/core input device.
"""
import ctypes as C,json,os,re,select,subprocess,sys

def validate_holder(name,window):
 if not isinstance(name,str) or not re.fullmatch(r'SemanticCU-[0-9]+-[0-9a-f]{8}',name):raise ValueError('Owned pair name required')
 if type(window) is not int or window<=1:raise ValueError('Observed target window required')

class CursorHolder:
 def __init__(self,name,window):
  validate_holder(name,window);self.process=None;self.name=name;self.window=window
 def start(self):
  self.process=subprocess.Popen([sys.executable,'-m',__name__,self.name,str(self.window)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ,SEMANTIC_CU_CURSOR_PARENT=str(os.getpid())))
  if not select.select([self.process.stdout],[],[],3)[0]:raise RuntimeError('Cursor initialization timed out')
  if self.process.stdout.readline().strip()!='READY':raise RuntimeError('Cursor initialization failed')
 def close(self):
  p=self.process
  if p is None:return
  if p.stdin and not p.stdin.closed:
   try:p.stdin.close()
   except (BrokenPipeError,OSError):pass
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
  p.stdout.close();p.stderr.close();self.process=None

def hold(name,window):
 from .auxiliary_guard import arm_parent_death
 from . import x11_pair as n
 arm_parent_death(int(os.environ['SEMANTIC_CU_CURSOR_PARENT']));validate_holder(name,window)
 n.NAME=name;t=n.Twin();cursor=0
 class Color(C.Structure):
  _fields_=[('pixel',n.L),('red',C.c_ushort),('green',C.c_ushort),('blue',C.c_ushort),('flags',C.c_byte),('pad',C.c_byte)]
 try:
  # Explicit monochrome bitmap avoids theme/ARGB substitution: recoloring
  # an XCreateFontCursor theme cursor does not reliably change its pixels.
  width,height=10,10
  # X bitmap rows are padded to bytes: 10 opaque bits in each 2-byte row.
  square=bytes([255,3])*height
  create=n.bind(t.x,'XCreateBitmapFromData',n.L,n.P,n.L,C.c_char_p,n.U,n.U)
  source=create(t.d,t.root,square,width,height)
  mask=create(t.d,t.root,square,width,height)
  fg=Color(0,65535,65535,0,7,0);bg=Color(0,65535,65535,0,7,0)
  try:
   if not source or not mask:raise RuntimeError('Could not allocate cursor bitmap')
   cursor=n.bind(t.x,'XCreatePixmapCursor',n.L,n.P,n.L,n.L,C.POINTER(Color),C.POINTER(Color),n.U,n.U)(t.d,source,mask,C.byref(fg),C.byref(bg),0,0)
  finally:
   free=n.bind(t.x,'XFreePixmap',n.I,n.P,n.L)
   if source:free(t.d,source)
   if mask:free(t.d,mask)
  if not cursor:raise RuntimeError('Could not create cursor')
  class XError(C.Structure):
   _fields_=[('type',n.I),('display',n.P),('resource',n.L),('serial',n.L),('code',C.c_ubyte),('request',C.c_ubyte),('minor',C.c_ubyte)]
  errors=[]
  callback_type=C.CFUNCTYPE(n.I,n.P,C.POINTER(XError))
  @callback_type
  def on_error(display,event):
   # Descendant windows can disappear while a GUI redraws. Ignore only
   # BadWindow; other protocol errors abort initialization after XSync.
   if event.contents.code!=3:errors.append(int(event.contents.code))
   return 0
  n.bind(t.x,'XSetErrorHandler',n.P,callback_type)(on_error)
  define=n.bind(t.xi,'XIDefineCursor',n.I,n.P,n.I,n.L,n.L)
  define(t.d,t.pointer,t.root,cursor)
  # Child input windows can otherwise override the parent's cursor.
  pending=[window];seen=set()
  query=n.bind(t.x,'XQueryTree',n.I,n.P,n.L,C.POINTER(n.L),C.POINTER(n.L),C.POINTER(C.POINTER(n.L)),C.POINTER(n.U))
  while pending and len(seen)<512:
   current=pending.pop()
   if current in seen:continue
   seen.add(current);define(t.d,t.pointer,current,cursor)
   root,parent=n.L(),n.L();children=C.POINTER(n.L)();count=n.U()
   if query(t.d,current,C.byref(root),C.byref(parent),C.byref(children),C.byref(count)):
    pending.extend(children[i] for i in range(min(count.value,max(0,512-len(seen)-len(pending)))))
    if children:n.bind(t.x,'XFree',n.I,n.P)(children)
  t.sync(t.d,0)
  if errors:raise RuntimeError('Cursor X protocol errors: '+str(errors))
  print('READY',flush=True)
  # Supervisor removes the pair first, then closes this pipe. No per-action
  # redefinition, cursor free, or connection churn while device is active.
  sys.stdin.buffer.read()
 finally:
  if cursor:n.bind(t.x,'XFreeCursor',n.I,n.P,n.L)(t.d,cursor);t.sync(t.d,0)
  t.close()

if __name__=='__main__':hold(sys.argv[1],int(sys.argv[2]))
