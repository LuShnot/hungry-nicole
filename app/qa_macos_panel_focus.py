import ctypes, os, sys, json, subprocess
from pathlib import Path
sys.path.insert(1, str(Path(__file__).resolve().parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from compact_bubble import CompactBubble
objc=ctypes.CDLL('/usr/lib/libobjc.A.dylib')
objc.objc_getClass.argtypes=[ctypes.c_char_p];objc.objc_getClass.restype=ctypes.c_void_p
objc.sel_registerName.argtypes=[ctypes.c_char_p];objc.sel_registerName.restype=ctypes.c_void_p
send=ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p)(('objc_msgSend',objc))
integer=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p)(('objc_msgSend',objc))
def msg(o,s):return send(o,objc.sel_registerName(s.encode()))
def front():
    a=msg(msg(objc.objc_getClass(b'NSWorkspace'),'sharedWorkspace'),'frontmostApplication')
    return integer(a,objc.sel_registerName(b'processIdentifier'))
if sys.platform!='darwin':raise SystemExit('This check requires native macOS.')
if len(sys.argv)!=2:raise SystemExit('Usage: python app/qa_macos_panel_focus.py /Applications/ChatGPT.app')
app=QApplication([]);b=CompactBubble();b.move(30,150);b.show();b._editing_flags(False);QTest.qWait(200)
subprocess.run(['osascript','-e','on run argv','-e','tell application (item 1 of argv) to activate','-e','end run',sys.argv[1]],check=True);QTest.qWait(500)
expected=front();observations=[]
for mode in ['mini','auto','mini','auto','mini']:
    b.set_mode(mode)
    for i in range(65):
        QTest.qWait(10)
        observations.append({'mode':mode,'ms':i*10,'front':front()})
print(json.dumps({'self':os.getpid(),'expected':expected,'samples':len(observations),'changed':[x for x in observations if x['front']!=expected]},indent=2))
b.close()
sys.exit(1 if any(x['front']!=expected for x in observations) else 0)
