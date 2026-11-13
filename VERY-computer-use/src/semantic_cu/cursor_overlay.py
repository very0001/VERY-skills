"""Visual cursor mock only. Creates no input device and sends no input events."""
import argparse

def show(x,y,seconds):
 import gi,cairo
 gi.require_version('Gtk','3.0');gi.require_version('Gdk','3.0')
 from gi.repository import Gtk,Gdk,GLib
 screen=Gdk.Screen.get_default()
 if not screen or not screen.is_composited():raise RuntimeError('Composited display required')
 window=Gtk.Window(type=Gtk.WindowType.POPUP)
 window.set_title('');window.set_accept_focus(False);window.set_focus_on_map(False)
 window.set_app_paintable(True);window.set_decorated(False)
 window.set_skip_taskbar_hint(True);window.set_skip_pager_hint(True)
 window.set_visual(screen.get_rgba_visual());window.set_default_size(26,34);window.move(x,y)
 def draw(_,cr):
  cr.set_operator(cairo.OPERATOR_SOURCE);cr.set_source_rgba(0,0,0,0);cr.paint();cr.set_operator(cairo.OPERATOR_OVER)
  cr.move_to(2,2)
  for px,py in [(2,25),(8,19),(13,31),(18,28),(12,17),(24,17)]:cr.line_to(px,py)
  cr.close_path();cr.set_source_rgb(0,.9,1);cr.fill_preserve();cr.set_source_rgba(.02,.12,.15,1);cr.set_line_width(1.5);cr.stroke()
  return False
 window.connect('draw',draw);window.connect('destroy',Gtk.main_quit)
 window.realize()
 # Empty input region: even clicking this small drawing reaches the underlying app.
 window.get_window().input_shape_combine_region(cairo.Region(),0,0)
 window.show_all();GLib.timeout_add(int(seconds*1000),lambda:(window.destroy(),False)[1]);Gtk.main()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--x',type=int,default=400);p.add_argument('--y',type=int,default=300);p.add_argument('--seconds',type=float,default=60);a=p.parse_args()
 if not 1<=a.seconds<=120:raise SystemExit('Duration must be 1..120 seconds')
 show(a.x,a.y,a.seconds)
