import sqlite3,time,uuid
class GenerationRepository:
 def __init__(self,path):self.path=path;path.parent.mkdir(parents=True,exist_ok=True);self.init()
 def conn(self):c=sqlite3.connect(self.path,timeout=10,isolation_level=None);c.row_factory=sqlite3.Row;c.execute("PRAGMA busy_timeout=10000");return c
 def init(self):
  with self.conn() as c:c.execute("CREATE TABLE IF NOT EXISTS gateway_generations (generation_id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL UNIQUE,request_fingerprint TEXT NOT NULL,upstream_task_id TEXT,state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,last_error_code TEXT,last_error_message TEXT,output_name TEXT)")
 def create_or_get(self,key,fp):
  now=time.time()
  with self.conn() as c:
   c.execute("BEGIN IMMEDIATE");r=c.execute("SELECT * FROM gateway_generations WHERE idempotency_key=?",(key,)).fetchone()
   if r:c.execute("COMMIT");return dict(r),True
   gid=str(uuid.uuid4());c.execute("INSERT INTO gateway_generations(generation_id,idempotency_key,request_fingerprint,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",(gid,key,fp,"accepted",now,now));c.execute("COMMIT");return self.get(gid),False
 def get(self,gid):
  with self.conn() as c:r=c.execute("SELECT * FROM gateway_generations WHERE generation_id=?",(gid,)).fetchone();return dict(r) if r else None
 def update(self,gid,**f):
  f["updated_at"]=time.time()
  with self.conn() as c:c.execute("UPDATE gateway_generations SET "+",".join(k+"=?" for k in f)+" WHERE generation_id=?",(*f.values(),gid))
