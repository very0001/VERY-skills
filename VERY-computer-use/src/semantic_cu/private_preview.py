"""Read-only preview transport; no keyboard/mouse injection APIs.
Writer runs only on private :100..:199. Viewer displays latest frame, never queues.
"""
import argparse,fcntl,json,mmap,os,re,struct,time,subprocess
from pathlib import Path

HEADER=struct.Struct('<QQII')
class FrameStore:
 def __init__(self,path,width,height,create=False):
  if not 0<width<=4096 or not 0<height<=4096:raise ValueError('Invalid geometry')
  self.width,self.height=width,height;self.size=HEADER.size+width*height*3
  self.fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|(os.O_CREAT|os.O_EXCL if create else 0),0o600)
  try:
   if create:os.ftruncate(self.fd,self.size)
   if os.fstat(self.fd).st_size!=self.size:raise ValueError('Frame size mismatch')
   self.buffer=mmap.mmap(self.fd,self.size)
  except BaseException:os.close(self.fd);raise
 def write(self,data,timestamp):
  if len(data)!=self.width*self.height*3:raise ValueError('Wrong RGB byte count')
  fcntl.flock(self.fd,fcntl.LOCK_EX)
  try:
   previous=HEADER.unpack(self.buffer[:HEADER.size])[0]
   seq=(previous//2+1)*2
   self.buffer[:HEADER.size]=HEADER.pack(seq-1,timestamp,self.width,self.height)
   self.buffer[HEADER.size:]=data
   self.buffer[:8]=struct.pack('<Q',seq)
  finally:fcntl.flock(self.fd,fcntl.LOCK_UN)
 def read(self):
  try:fcntl.flock(self.fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
  except BlockingIOError:return None
  try:
   seq,stamp,w,h=HEADER.unpack(self.buffer[:HEADER.size])
   if not seq or seq%2:return None
   if (w,h)!=(self.width,self.height):raise ValueError('Corrupt frame header')
   return seq,stamp,self.buffer[HEADER.size:]
  finally:fcntl.flock(self.fd,fcntl.LOCK_UN)
 def close(self):self.buffer.close();os.close(self.fd)
 def __enter__(self):return self
 def __exit__(self,*_):self.close()

def writer(path,width,height,duration):
 from PIL import ImageGrab,ImageDraw
 import ctypes as C
 from .x11_pair import bind,P,I,L,U
 name=os.environ.get('DISPLAY','')
 if not re.fullmatch(r':1[0-9]{2}',name):raise ValueError('Writer requires a private display')
 xlib=C.CDLL('libX11.so.6');d=bind(xlib,'XOpenDisplay',P,C.c_char_p)(name.encode())
 if not d:raise RuntimeError('Cannot open private display')
 root=bind(xlib,'XDefaultRootWindow',L,P)(d)
 query=bind(xlib,'XQueryPointer',I,P,L,C.POINTER(L),C.POINTER(L),C.POINTER(I),C.POINTER(I),C.POINTER(I),C.POINTER(I),C.POINTER(U))
 count=0;costs=[];started=time.monotonic()
 try:
  with FrameStore(path,width,height) as f:
   while time.monotonic()-started<duration:
    t=time.monotonic();im=ImageGrab.grab(xdisplay=name).convert('RGB')
    if im.size!=(width,height):raise ValueError('Private source geometry changed')
    rr,child=L(),L();rx,ry,wx,wy=I(),I(),I(),I();mask=U()
    query(d,root,C.byref(rr),C.byref(child),C.byref(rx),C.byref(ry),C.byref(wx),C.byref(wy),C.byref(mask))
    draw=ImageDraw.Draw(im)
    # Cyan cross with dark outline; no text/name/label.
    x,y=rx.value,ry.value
    for color,line in [('#12343b',5),('#00e5ff',2)]:
     draw.line((x-8,y,x+8,y),fill=color,width=line);draw.line((x,y-8,x,y+8),fill=color,width=line)
    f.write(im.tobytes(),time.monotonic_ns());count+=1;costs.append((time.monotonic()-t)*1000)
    time.sleep(max(0,.05-(time.monotonic()-t)))
 finally:bind(xlib,'XCloseDisplay',I,P)(d)
 Path(str(path)+'.writer.json').write_text(json.dumps(dict(frames=count,seconds=time.monotonic()-started,capture_ms_average=sum(costs)/len(costs) if costs else None)))

def viewer(path,width,height,duration,x=62,y=27):
 import gi
 gi.require_version('Gtk','3.0');gi.require_version('Gdk','3.0')
 from gi.repository import Gtk,Gdk,GdkPixbuf,GLib
 f=FrameStore(path,width,height);window=Gtk.Window(title='辅助工作区（只读）')
 window.set_accept_focus(False);window.set_focus_on_map(False)
 window.set_skip_taskbar_hint(True);window.set_skip_pager_hint(True)
 window.set_resizable(False);window.move(x,y)
 image=Gtk.Image();window.add(image)
 # Never forward input, never grab, never activate/focus another window.
 window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK|Gdk.EventMask.SCROLL_MASK)
 for event in ['button-press-event','button-release-event','scroll-event']:
  window.connect(event,lambda *_:True)
 window.connect('destroy',Gtk.main_quit)
 state=dict(last=0,frames=0,ages=[],started=time.monotonic())
 def tick():
  if time.monotonic()-state['started']>=duration:window.destroy();return False
  frame=f.read()
  if frame and frame[0]!=state['last']:
   seq,stamp,data=frame
   pix=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data),GdkPixbuf.Colorspace.RGB,False,8,width,height,width*3)
   image.set_from_pixbuf(pix)
   state['last']=seq;state['frames']+=1;state['ages'].append((time.monotonic_ns()-stamp)/1e6)
  return True
 window.show_all();GLib.timeout_add(25,tick)
 try:Gtk.main()
 finally:
  f.close();elapsed=time.monotonic()-state['started'];ages=sorted(state['ages'])
  Path(str(path)+'.viewer.json').write_text(json.dumps(dict(frames=state['frames'],seconds=elapsed,fps=state['frames']/elapsed,age_ms_p95=ages[int((len(ages)-1)*.95)] if ages else None,accept_focus=False,focus_on_map=False)))

def host_preview(config_path, duration):
 config=json.loads(Path(config_path).read_text())
 frame=Path(config['frame'])
 with FrameStore(frame,1200,1300,create=True):pass
 children=[]
 try:
  for mode,env in [('writer',config['source_env']),('viewer',config['viewer_env'])]:
   children.append(subprocess.Popen(['/usr/bin/python3','-m','semantic_cu.private_preview',mode,str(frame),'--duration',str(duration)],env=env))
  deadline=time.monotonic()+duration+2
  while all(p.poll() is None for p in children) and time.monotonic()<deadline:time.sleep(.1)
 finally:
  for p in children:
   if p.poll() is None:p.terminate()
  for p in children:
   try:p.wait(timeout=3)
   except subprocess.TimeoutExpired:p.kill();p.wait()
  frame.unlink(missing_ok=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['writer','viewer','host']);p.add_argument('path');p.add_argument('--width',type=int,default=1200);p.add_argument('--height',type=int,default=1300);p.add_argument('--duration',type=float,default=30);p.add_argument('--x',type=int,default=62);p.add_argument('--y',type=int,default=27)
 a=p.parse_args()
 if a.mode=='host':host_preview(a.path,a.duration)
 elif a.mode=='writer':writer(a.path,a.width,a.height,a.duration)
 else:viewer(a.path,a.width,a.height,a.duration,a.x,a.y)
