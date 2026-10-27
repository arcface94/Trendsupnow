import json,sqlite3,hashlib,uuid,os,mimetypes,urllib.parse,html,io
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime,timezone
import fitz,ezdxf
ROOT=Path(__file__).resolve().parent
# Store mutable project data outside the executable so app upgrades preserve projects.
if getattr(__import__('sys'), 'frozen', False):
 DATA=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ArchitectAIStudio' / 'data'
else:
 DATA=ROOT/'data'
DATA.mkdir(parents=True, exist_ok=True)
DB=DATA/'studio.db'; FILES=DATA/'uploads';FILES.mkdir(exist_ok=True)
def now():return datetime.now(timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def setup():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,location TEXT,kind TEXT,description TEXT,archived INTEGER DEFAULT 0,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drawings(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT NOT NULL,path TEXT NOT NULL,revision INTEGER DEFAULT 0,status TEXT DEFAULT 'DRAFT',parent_id TEXT,created TEXT NOT NULL,FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT, drawing_id TEXT,action TEXT,details TEXT,created TEXT);''')
def rows(q,args=()):
 with db() as c:return [dict(r) for r in c.execute(q,args)]
def log(c,p,d,a,details=''):c.execute('INSERT INTO audit(project_id,drawing_id,action,details,created) VALUES(?,?,?,?,?)',(p,d,a,details,now()))
def dxf_svg(path):
 doc=ezdxf.readfile(str(path));ms=doc.modelspace(); segments=[];bounds=[]
 def seg(x1,y1,x2,y2,lay):
  vals=[x1,y1,x2,y2]
  if any(abs(float(v))>1e9 for v in vals):return
  bounds.extend([(x1,y1),(x2,y2)]);segments.append((x1,y1,x2,y2,lay))
 for e in ms:
  typ=e.dxftype(); lay=e.dxf.layer
  try:
   if typ=='LINE':seg(e.dxf.start.x,e.dxf.start.y,e.dxf.end.x,e.dxf.end.y,lay)
   elif typ in ('LWPOLYLINE','POLYLINE'):
    pts=list(e.get_points('xy') if typ=='LWPOLYLINE' else [(v.dxf.location.x,v.dxf.location.y) for v in e.vertices]);pts=[(float(v[0]),float(v[1])) for v in pts]
    for a,b in zip(pts,pts[1:]):seg(*a,*b,lay)
    if e.is_closed and len(pts)>2:seg(*pts[-1],*pts[0],lay)
   elif typ=='CIRCLE':
    import math
    p=e.dxf.center;r=e.dxf.radius
    for i in range(48):
     a=i*math.tau/48;b=(i+1)*math.tau/48;seg(p.x+r*math.cos(a),p.y+r*math.sin(a),p.x+r*math.cos(b),p.y+r*math.sin(b),lay)
   elif typ=='ARC':
    import math
    p=e.dxf.center;r=e.dxf.radius;a=e.dxf.start_angle;b=e.dxf.end_angle
    if b<a:b+=360
    n=max(5,int((b-a)/5))
    for i in range(n):
     t=math.radians(a+(b-a)*i/n);u=math.radians(a+(b-a)*(i+1)/n)
     seg(p.x+r*math.cos(t),p.y+r*math.sin(t),p.x+r*math.cos(u),p.y+r*math.sin(u),lay)
  except Exception:pass
 if not bounds:return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="5" y="20" fill="white">No supported vector entities</text></svg>',[]
 xs=[p[0] for p in bounds];ys=[p[1] for p in bounds];x=min(xs);y=min(ys);w=max(max(xs)-x,1);h=max(max(ys)-y,1);pad=max(w,h)*.03;layers=sorted({e[-1] for e in segments});out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x-pad} {-max(ys)-pad} {w+2*pad} {h+2*pad}" preserveAspectRatio="xMidYMid meet">']
 for lay in layers:
  out.append(f'<g data-layer="{html.escape(lay,quote=True)}" stroke="#b9e3f2" stroke-width="{max(w,h)/1000:.5f}" fill="none">')
  for a,b,c,d,l in segments:
   if l==lay:out.append(f'<line x1="{a}" y1="{-b}" x2="{c}" y2="{-d}"/>')
  out.append('</g>')
 out.append('</svg>');return ''.join(out),layers
class Handler(BaseHTTPRequestHandler):
 def reply(self,obj,status=200,mime='application/json'):
  data=json.dumps(obj).encode() if not isinstance(obj,bytes) else obj
  self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
 def body(self):return json.loads(self.rfile.read(min(int(self.headers.get('Content-Length',0)),2_000_000)))
 def do_GET(self):
  path=urllib.parse.urlsplit(self.path).path
  try:
   if path=='/':return self.reply((ROOT/'index.html').read_bytes(),mime='text/html; charset=utf-8')
   if path=='/api/projects':return self.reply(rows('SELECT * FROM projects ORDER BY created DESC'))
   if path=='/api/drawings':return self.reply(rows('SELECT id,project_id,name,kind,sha256,revision,status,parent_id,created FROM drawings ORDER BY created DESC'))
   if path=='/api/audit':return self.reply(rows('SELECT * FROM audit ORDER BY id DESC LIMIT 300'))
   if path.startswith('/api/view/'):
    id=path.split('/')[-1];rr=rows('SELECT * FROM drawings WHERE id=?',(id,))
    if not rr:return self.reply({'error':'Not found'},404)
    r=rr[0];p=FILES/r['path'];kind=r['kind']
    if kind=='pdf':
     doc=fitz.open(str(p));page=int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('page',['0'])[0]);page=max(0,min(page,len(doc)-1));pix=doc[page].get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False);return self.reply(pix.tobytes('png'),mime='image/png')
    if kind in ('png','jpg','jpeg'):return self.reply(p.read_bytes(),mime='image/png' if kind=='png' else 'image/jpeg')
    if kind=='dxf':
     svg,layers=dxf_svg(p);return self.reply(svg.encode(),mime='image/svg+xml')
   if path.startswith('/api/layers/'):
    id=path.split('/')[-1];rr=rows('SELECT * FROM drawings WHERE id=?',(id,))
    if not rr:return self.reply({'error':'Not found'},404)
    r=rr[0];meta={'layers':[],'pages':1}
    if r['kind']=='dxf':meta['layers']=dxf_svg(FILES/r['path'])[1]
    if r['kind']=='pdf':meta['pages']=len(fitz.open(str(FILES/r['path'])))
    return self.reply(meta)
   return self.reply({'error':'Not found'},404)
  except Exception as e:return self.reply({'error':str(e)},500)
 def do_POST(self):
  path=urllib.parse.urlsplit(self.path).path
  try:
   if path=='/api/projects':
    b=self.body(); name=str(b.get('name','')).strip()
    if not name:return self.reply({'error':'Project name required'},400)
    id=str(uuid.uuid4());
    with db() as c:c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?)',(id,name,b.get('location',''),b.get('kind',''),b.get('description',''),0,now()));log(c,id,None,'PROJECT_CREATED',name)
    return self.reply({'id':id})
   if path=='/api/archive':
    b=self.body();p=b.get('project_id');
    with db() as c:c.execute('UPDATE projects SET archived=? WHERE id=?',(1 if b.get('archived') else 0,p));log(c,p,None,'PROJECT_ARCHIVE_CHANGED',str(bool(b.get('archived'))))
    return self.reply({'ok':True})
   if path=='/api/upload':
    p=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); project_id=p.get('project_id',[''])[0];name=p.get('name',[''])[0];parent=p.get('parent_id',[''])[0] or None;reason=p.get('reason',[''])[0]
    if not rows('SELECT id FROM projects WHERE id=?',(project_id,)):return self.reply({'error':'Unknown project'},400)
    kind=Path(name).suffix.lower().lstrip('.')
    if kind not in ['pdf','png','jpg','jpeg','dxf']:return self.reply({'error':'Supported: PDF, PNG, JPG, DXF'},400)
    n=int(self.headers.get('Content-Length','0'))
    if n<1 or n>40_000_000:return self.reply({'error':'File limit 40MB'},400)
    if parent:
     rr=rows('SELECT * FROM drawings WHERE id=? AND project_id=?',(parent,project_id))
     if not rr or rr[0]['status']!='APPROVED_LOCKED' or not reason.strip():return self.reply({'error':'Locked parent and change reason required'},400)
    blob=self.rfile.read(n);hash=hashlib.sha256(blob).hexdigest()
    if kind=='pdf':fitz.open(stream=blob,filetype='pdf').close()
    elif kind=='dxf':ezdxf.read(io.StringIO(blob.decode('utf-8-sig',errors='replace')))
    elif kind in ('png','jpg','jpeg'):
     from PIL import Image
     im=Image.open(io.BytesIO(blob));im.verify()
    id=str(uuid.uuid4());filename=id+'.'+kind;(FILES/filename).write_bytes(blob)
    rev=(rr[0]['revision']+1) if parent else 0
    with db() as c:c.execute('INSERT INTO drawings VALUES(?,?,?,?,?,?,?,?,?,?)',(id,project_id,Path(name).name,kind,hash,filename,rev,'DRAFT',parent,now()));log(c,project_id,id,'DRAWING_UPLOADED',f'SHA256 {hash}; parent {parent}; reason {reason}')
    return self.reply({'id':id,'sha256':hash,'revision':rev})
   if path=='/api/status':
    b=self.body();id=b.get('id');status=b.get('status')
    if status not in ('REVIEW','APPROVED_LOCKED'):return self.reply({'error':'Invalid status'},400)
    with db() as c:
     r=c.execute('SELECT * FROM drawings WHERE id=?',(id,)).fetchone()
     if not r:return self.reply({'error':'Unknown drawing'},404)
     if r['status']=='APPROVED_LOCKED':return self.reply({'error':'Locked drawing cannot change status'},409)
     if status=='APPROVED_LOCKED' and (r['status']!='REVIEW' or b.get('confirm')!='LOCK_GEOMETRY'):return self.reply({'error':'Review and confirmation required'},409)
     c.execute('UPDATE drawings SET status=? WHERE id=?',(status,id));log(c,r['project_id'],id,'STATUS_CHANGED',status)
    return self.reply({'ok':True})
   return self.reply({'error':'Not found'},404)
  except Exception as e:return self.reply({'error':str(e)},400)
 def log_message(self,*args):pass
if __name__=='__main__':
 setup();print('ARCHITECT AI STUDIO running at http://127.0.0.1:8765',flush=True);ThreadingHTTPServer(('127.0.0.1',8765),Handler).serve_forever()
