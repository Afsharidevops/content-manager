"""Authenticated execution brokers and independent Telegram approver service."""
from __future__ import annotations
import base64, hmac, json, os, sqlite3, subprocess, tempfile, threading, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from . import approval, schema, ssh as ssh_module
from . import admin as admin_module
from .approver import ApprovalRequestStore, ApproverError, TelegramApprover, validate_submission
from .engine import DockerEngine, EngineError
from .sandbox import run_sandbox
from .store import CapabilityError, CapabilityStore

MAX_REQUEST_BYTES=256*1024; MAX_CONCURRENT=4
MODE=os.environ.get("BROKER_MODE","docker")
STATE_PATH=Path(os.environ.get("BROKER_STATE","/state/capabilities.sqlite3"))
CONTROL_SECRET_PATH=Path(os.environ.get("BROKER_CONTROL_SECRET_FILE","/run/secrets/execution-control"))
APPROVAL_REQUEST_SECRET_PATH=Path(os.environ.get("BROKER_APPROVAL_REQUEST_SECRET_FILE","/run/secrets/execution-approval-request"))
APPROVAL_PRIVATE_KEY_PATH=Path(os.environ.get("APPROVER_SIGNING_KEY_FILE","/run/secrets/execution-approval-signing-key"))
APPROVAL_PUBLIC_KEY_PATH=Path(os.environ.get("BROKER_APPROVAL_PUBLIC_KEY_FILE","/run/secrets/execution-approval-public-key"))
APPROVAL_BOT_TOKEN_PATH=Path(os.environ.get("EXECUTION_APPROVAL_BOT_TOKEN_FILE","/run/secrets/execution-approval-bot-token"))
APPROVAL_USERS_PATH=Path(os.environ.get("EXECUTION_APPROVAL_USERS_FILE","/run/secrets/execution-approval-users"))
SSH_INTEGRITY_SECRET_PATH=Path(os.environ.get("BROKER_SSH_PROFILE_INTEGRITY_SECRET_FILE","/run/secrets/execution-ssh-profile-integrity"))
APPROVER_URL=os.environ.get("BROKER_APPROVER_URL","http://execution-approver:8751")
CALLBACK_URLS={"docker":os.environ.get("APPROVER_DOCKER_CALLBACK_URL","http://execution-docker-broker:8750/approval-grant"),"ssh":os.environ.get("APPROVER_SSH_CALLBACK_URL","http://execution-ssh-broker:8750/approval-grant")}
SANDBOX_IMAGE=os.environ.get("SANDBOX_IMAGE",""); WORKSPACE_SOURCE=os.environ.get("EXECUTION_WORKSPACE",""); WORKSPACE_GENERATION=os.environ.get("EXECUTION_WORKSPACE_GENERATION","")
EGRESS_NETWORK=os.environ.get("EXECUTION_EGRESS_NETWORK","hermes-execution-egress")
FEATURES_PATH=Path(os.environ.get("EXECUTION_FEATURES_FILE","")) if os.environ.get("EXECUTION_FEATURES_FILE") else None
POLICY_GENERATION_PATH=Path(os.environ.get("EXECUTION_POLICY_GENERATION_FILE","")) if os.environ.get("EXECUTION_POLICY_GENERATION_FILE") else None

def _features():
    try: raw=FEATURES_PATH.read_text(encoding="utf-8").strip() if FEATURES_PATH else os.environ.get("EXECUTION_FEATURES","")
    except OSError: raw=os.environ.get("EXECUTION_FEATURES","")
    allowed=("local","ssh","docker")
    selected={x.strip() for x in raw.split(",") if x.strip()}
    return tuple(x for x in allowed if x in selected)

def _policy_generation():
    try: raw=POLICY_GENERATION_PATH.read_text(encoding="utf-8").strip() if POLICY_GENERATION_PATH else os.environ.get("EXECUTION_POLICY_GENERATION","0")
    except OSError: raw=os.environ.get("EXECUTION_POLICY_GENERATION","0")
    return raw if str(raw).isdigit() else "0"

_store=CapabilityStore(STATE_PATH) if MODE in ("docker","ssh") else None
if _store: _store.cancel_generation(_policy_generation())
_approval_store=ApprovalRequestStore(STATE_PATH) if MODE=="approver" else None
_engine=DockerEngine() if MODE=="docker" else None; _slots=threading.BoundedSemaphore(MAX_CONCURRENT); _context=threading.local()

def _read(path):
    try:return path.read_text(encoding="utf-8").strip()
    except OSError:return ""
def _enabled(f): return f in _features() and ((MODE=="docker" and f in ("local","docker")) or (MODE=="ssh" and f=="ssh"))
def _runtime():
    if MODE not in ("docker","ssh","approver","admin"): return "Invalid broker mode."
    if MODE=="admin": return None if admin_module.configured() else "The execution admin key is unavailable."
    if not _read(APPROVAL_REQUEST_SECRET_PATH): return "The independent approval request secret is unavailable."
    if MODE=="approver":
        if not APPROVAL_PRIVATE_KEY_PATH.is_file(): return "The independent approval signing key is unavailable."
        return None if _telegram and _telegram.configured() else "The dedicated execution approval bot token and numeric users are required."
    if not APPROVAL_PUBLIC_KEY_PATH.is_file(): return "The independent approval verification key is unavailable."
    if not _read(CONTROL_SECRET_PATH): return "The broker control secret is unavailable."
    if MODE=="docker" and "local" in _features() and (not SANDBOX_IMAGE or not WORKSPACE_SOURCE or not WORKSPACE_GENERATION or not Path(WORKSPACE_SOURCE).is_absolute()): return "Local execution requires a pinned image and an absolute, generation-sealed workspace."
    return None

def _refresh_protected_ids():
    """Bind protected Docker names to immutable IDs so renames cannot expose them."""
    if MODE!="docker":return
    container_ids=set();network_ids=set()
    for name in approval.PROTECTED_CONTAINERS:
        try:container_ids.add(_engine.resolve_container(name)["id"])
        except (EngineError,OSError,KeyError):continue
    for name in approval.PROTECTED_NETWORKS:
        try:network_ids.add(_engine.resolve_network(name)["id"])
        except (EngineError,OSError,KeyError):continue
    approval.set_protected_container_ids(container_ids)
    approval.set_protected_network_ids(network_ids)

def _sign(body):
    with tempfile.TemporaryDirectory() as directory:
        body_path=Path(directory)/"decision.json";signature_path=Path(directory)/"decision.sig"
        body_path.write_bytes(body)
        try:subprocess.run(["openssl","pkeyutl","-sign","-inkey",str(APPROVAL_PRIVATE_KEY_PATH),"-rawin","-in",str(body_path),"-out",str(signature_path)],check=True,capture_output=True,timeout=10)
        except (OSError,subprocess.SubprocessError) as exc:raise ApproverError("The approval decision could not be signed.") from exc
        return base64.b64encode(signature_path.read_bytes()).decode()

def _verify(body,signature):
    try:decoded=base64.b64decode(signature,validate=True)
    except (ValueError,base64.binascii.Error):return False
    with tempfile.TemporaryDirectory() as directory:
        body_path=Path(directory)/"decision.json";signature_path=Path(directory)/"decision.sig"
        body_path.write_bytes(body);signature_path.write_bytes(decoded)
        try:return subprocess.run(["openssl","pkeyutl","-verify","-pubin","-inkey",str(APPROVAL_PUBLIC_KEY_PATH),"-rawin","-in",str(body_path),"-sigfile",str(signature_path)],capture_output=True,timeout=10).returncode==0
        except (OSError,subprocess.SubprocessError):return False

def _submit(data):
    req=urllib.request.Request(APPROVER_URL+"/request",data=json.dumps(data).encode(),method="POST",headers={"Content-Type":"application/json","X-Approval-Secret":_read(APPROVAL_REQUEST_SECRET_PATH)})
    try:
        with urllib.request.urlopen(req,timeout=20) as r: result=json.loads(r.read())
    except (urllib.error.URLError,OSError,json.JSONDecodeError) as e:return f"The independent approver is unavailable: {type(e).__name__}."
    return result.get("error") if result.get("status")!="pending" else None

def _prepare(p):
    if problem:=_runtime(): return {"error":problem}
    f,u,s=str(p.get("feature","")),str(p.get("user_id","")),str(p.get("session",""))
    if not _enabled(f): return {"error":f"Execution feature '{f}' is not enabled on this stack."}
    if not u.isdigit() or not s:return {"error":"A numeric Telegram user and session are required."}
    try:
        r=schema.validate(f,p.get("request"))
        if f=="local": r.update(resolved_image_id=_engine.resolve_image(SANDBOX_IMAGE),workspace_generation=WORKSPACE_GENERATION)
        elif f=="ssh": r["sealed_profile"]=ssh_module.seal_profile(ssh_module.load_profile(r["profile"],integrity_key=ssh_module.read_integrity_secret(SSH_INTEGRITY_SECRET_PATH)))
        elif f=="docker" and r["action"]=="run":
            r["resolved_image_id"]=_engine.resolve_image(r["image"])
            if r["network"]!="none":r["resolved_network_id"]=_engine.resolve_network(r["network"])["id"]
        elif f=="docker" and r.get("container"):
            x=_engine.resolve_container(r["container"]);r.update(container=x["id"],resolved_name=x["name"])
        if f=="docker":_refresh_protected_ids()
        approval.check_floor(f,r); summary=approval.render_summary(f,r); digest=approval.canonical_digest(f,r)
    except (schema.RequestError,ssh_module.ProfileError,approval.DeniedError,EngineError) as e:return {"error":str(e)}
    nonce=_store.issue(feature=f,digest=digest,request=json.dumps(r),user_id=u,session=s,generation=_policy_generation())
    data={"target":"docker" if MODE=="docker" else "ssh","feature":f,"capability":nonce,"digest":digest,"request":r,"summary":summary,"user_id":u,"session":s,"generation":_policy_generation()}
    if problem:=_submit(data):_store.cancel(nonce);return {"error":problem}
    return {"status":"prepared","capability":nonce,"digest":digest,"summary":summary}

def _send_decision(g):
    fields=("target","feature","capability","digest","user_id","session","generation","decision");data={k:str(g[k]) for k in fields};body=json.dumps(data,sort_keys=True,separators=(",",":")).encode();sig=_sign(body)
    req=urllib.request.Request(CALLBACK_URLS[g["target"]],data=body,method="POST",headers={"Content-Type":"application/json","X-Approval-Signature":sig})
    try:
        with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read()).get("status") in ("approved","denied")
    except (urllib.error.URLError,OSError,json.JSONDecodeError):return False

def _request(p):
    if problem:=_runtime():return {"error":problem}
    try:_telegram.submit(validate_submission(p,generation=_policy_generation()),ttl_seconds=300)
    except (ApproverError,approval.DeniedError,sqlite3.IntegrityError) as e:return {"error":str(e)}
    return {"status":"pending"}

def _accept(p):
    fields=("target","feature","capability","digest","user_id","session","generation","decision")
    if MODE not in ("docker","ssh") or set(p)!=set(fields):return {"error":"The approval decision is incomplete."}
    data={k:str(p[k]) for k in fields};body=json.dumps(data,sort_keys=True,separators=(",",":")).encode()
    if not _verify(body,getattr(_context,"signature","")):return {"error":"The independent approval decision is invalid."}
    target="docker" if MODE=="docker" else "ssh";f=p["feature"]
    if p["target"]!=target or p["generation"]!=_policy_generation() or not _enabled(f):return {"error":"The decision targets another broker, generation, or disabled feature."}
    try:
        args=dict(nonce=p["capability"],feature=f,digest=p["digest"],user_id=p["user_id"],session=p["session"],generation=_policy_generation())
        if p["decision"]=="approved":_store.approve(**args);return {"status":"approved"}
        if p["decision"]=="denied":_store.cancel_bound(**args);return {"status":"denied"}
    except CapabilityError as e:return {"error":str(e)}
    return {"error":"The approval decision is invalid."}

def _execute(p):
    if problem:=_runtime():return {"error":problem}
    f=str(p.get("feature",""))
    if not _enabled(f):return {"error":f"Execution feature '{f}' is not enabled on this stack."}
    if not _slots.acquire(False):return {"error":"Too many execution operations are already running; retry this approval."}
    try:
        c=_store.consume(nonce=str(p.get("capability","")),feature=f,digest=str(p.get("digest","")),user_id=str(p.get("user_id","")),session=str(p.get("session","")),generation=_policy_generation(),wait_seconds=300);r=json.loads(c["request"]);approval.check_floor(f,r)
        if approval.canonical_digest(f,r)!=c["digest"]:return {"error":"The stored operation no longer matches its approved digest."}
        if f=="local":
            if r.get("resolved_image_id")!=_engine.resolve_image(SANDBOX_IMAGE) or r.get("workspace_generation")!=WORKSPACE_GENERATION:return {"error":"The sandbox image or workspace changed after approval."}
            result=run_sandbox(_engine,r,image=r["resolved_image_id"],workspace_source=WORKSPACE_SOURCE,egress_network=EGRESS_NETWORK);action="local"
        elif f=="ssh":
            profile=ssh_module.load_profile(r["profile"],integrity_key=ssh_module.read_integrity_secret(SSH_INTEGRITY_SECRET_PATH))
            if not ssh_module.profile_matches(profile,r.get("sealed_profile",{})):return {"error":"The SSH target, host key, or credential changed after approval."}
            result=ssh_module.run_ssh(r,profile);action="ssh:"+r["profile"]
        else:
            _refresh_protected_ids();approval.check_floor(f,r)
            if r.get("container"):
                x=_engine.resolve_container(r["container"])
                if x["id"]!=r["container"] or x["name"]!=r.get("resolved_name"):return {"error":"The Docker container identity changed after approval."}
            if r.get("resolved_network_id"):
                x=_engine.resolve_network(r["network"])
                if x["id"]!=r["resolved_network_id"]:return {"error":"The Docker network identity changed after approval."}
            result=_run_docker(r);action="docker:"+r["action"]
    except (CapabilityError,ssh_module.ProfileError,approval.DeniedError,EngineError,json.JSONDecodeError) as e:return {"error":str(e)}
    finally:_slots.release()
    _store.record(feature=f,action=action,user_id=str(p.get("user_id","")),digest=c["digest"],returncode=result.get("returncode"),duration=result.get("duration",0),out_len=len(result.get("output","")),truncated=bool(result.get("truncated")));result["status"]="executed";return result

def _redact(info):
    cfg=info.get("Config") or {};host=info.get("HostConfig") or {}
    return {"Id":info.get("Id"),"Name":info.get("Name"),"Image":info.get("Image"),"Created":info.get("Created"),"State":info.get("State"),"Config":{"Image":cfg.get("Image"),"Entrypoint":cfg.get("Entrypoint"),"Cmd":cfg.get("Cmd"),"User":cfg.get("User"),"WorkingDir":cfg.get("WorkingDir"),"EnvNames":sorted(x.split("=",1)[0] for x in cfg.get("Env",[]) if isinstance(x,str))},"HostConfig":{k:host.get(k) for k in ("ReadonlyRootfs","Privileged","NetworkMode","PidMode","IpcMode","UTSMode","UsernsMode","Memory","NanoCpus","PidsLimit")}}
def _run_docker(r):
    import time
    a=r["action"];start=time.monotonic();t=float(r.get("timeout",120))
    if a=="list":out=json.dumps(_engine.list_containers(all_containers=r["all"],timeout=t),indent=2)
    elif a=="pull":out=_engine.pull(r["image"],t)
    elif a=="inspect":out=json.dumps(_redact(_engine.inspect(r["container"],t)),indent=2)
    elif a=="logs":out=_engine.logs(r["container"],tail=r["tail"],timeout=t)
    elif a in ("start","stop","restart"):getattr(_engine,a)(r["container"],t);out=a+"ed"
    elif a=="remove":_engine.remove(r["container"],force=r["force"],volumes=r["remove_volumes"],timeout=t);out="removed"
    else:return _run_container(r,start)
    return {"returncode":0,"output":out[-schema.MAX_OUTPUT_CHARS:],"truncated":len(out)>schema.MAX_OUTPUT_CHARS,"timed_out":False,"duration":round(time.monotonic()-start,3)}
def _run_container(r,start):
    import time
    h={"ReadonlyRootfs":r["read_only_rootfs"],"Privileged":r["privileged"],"CapAdd":r["capabilities_add"],"CapDrop":r["capabilities_drop"] or ["ALL"],"SecurityOpt":r["security_opt"] or ["no-new-privileges:true"],"Memory":r["memory_mb"]*1048576,"NanoCpus":r["cpus"]*1000000000,"PidsLimit":r["pids_limit"],"AutoRemove":r["auto_remove"],"RestartPolicy":{"Name":r["restart_policy"]},"NetworkMode":r["network"],"Dns":r["dns"],"Mounts":[],"PortBindings":{}}
    for s,k in (("pid_mode","PidMode"),("ipc_mode","IpcMode"),("uts_mode","UTSMode"),("userns_mode","UsernsMode")):
        if r[s]=="host":h[k]="host"
    if r["devices"]:h["Devices"]=[{"PathOnHost":x,"PathInContainer":x,"CgroupPermissions":"rwm"} for x in r["devices"]]
    if r["sysctls"]:h["Sysctls"]=dict(x.split("=",1) for x in r["sysctls"] if "=" in x)
    for m in r["mounts"]:
        if m["type"]=="tmpfs":h.setdefault("Tmpfs",{})[m["target"]]="rw,noexec,nosuid,size=64m"
        else:h["Mounts"].append({"Type":m["type"],"Source":m["source"],"Target":m["target"],"ReadOnly":m["read_only"]})
    for p in r["ports"]:h["PortBindings"].setdefault(f"{p['container_port']}/{p['protocol']}",[]).append({"HostIp":p["host_ip"],"HostPort":str(p["host_port"])})
    b={"Image":r.get("resolved_image_id") or r["image"],"Env":[f"{x['name']}={x['value']}" for x in r["environment"]],"Labels":dict(x.split("=",1) for x in r["labels"] if "=" in x),"AttachStdin":False,"OpenStdin":False,"Tty":False,"HostConfig":h}
    for s,k in (("entrypoint","Entrypoint"),("command","Cmd"),("user","User"),("workdir","WorkingDir")):
        if r[s]:b[k]=r[s]
    cid=_engine.create(b,r["name"],60);_engine.start(cid,60)
    if r["detach"]:return {"returncode":0,"output":f"started detached container {cid[:12]}","container":cid[:12],"truncated":False,"timed_out":False,"duration":round(time.monotonic()-start,3)}
    try:code=_engine.wait(cid,r["timeout"]+10);timed=False
    except (EngineError,OSError,TimeoutError):_engine.kill(cid);code,timed=124,True
    try:out=_engine.logs(cid,tail=2000,timeout=60)
    except EngineError:out=""
    if r["auto_remove"]:
        try:_engine.remove(cid,force=True,volumes=False,timeout=60)
        except EngineError:pass
    return {"returncode":code,"output":out[-schema.MAX_OUTPUT_CHARS:],"container":cid[:12],"truncated":len(out)>schema.MAX_OUTPUT_CHARS,"timed_out":timed,"duration":round(time.monotonic()-start,3)}
def _cancel(p):_store.cancel(str(p.get("capability","")));return {"status":"cancelled"}
def _discover(p):
    if p.get("kind")=="ssh_profiles" and _enabled("ssh"):return {"status":"ok","profiles":ssh_module.list_profiles(integrity_key=ssh_module.read_integrity_secret(SSH_INTEGRITY_SECRET_PATH))}
    if p.get("kind")=="docker_containers" and _enabled("docker"):return {"status":"ok","containers":_engine.list_containers(all_containers=True,timeout=30)}
    return {"error":"That discovery kind is not available."}
def _health(_):
    problem=_runtime();checks={"mode":MODE,"features":list(_features())}
    if MODE=="docker":checks["engine"]="ok" if _engine.ping() else "unreachable"
    elif MODE=="ssh":checks["profiles"]=len(ssh_module.list_profiles(integrity_key=ssh_module.read_integrity_secret(SSH_INTEGRITY_SECRET_PATH)))
    elif MODE=="approver":checks["bot"]="configured" if _telegram and _telegram.configured() else "missing"
    else:checks["admin_key"]="configured" if admin_module.configured() else "missing"
    return {"status":"error","error":problem,"checks":checks} if problem else {"status":"ok","checks":checks}
ROUTES={"/prepare":_prepare,"/execute":_execute,"/cancel":_cancel,"/discover":_discover,"/approval-grant":_accept,"/request":_request}
_telegram=TelegramApprover(token_file=APPROVAL_BOT_TOKEN_PATH,users_file=APPROVAL_USERS_PATH,store=_approval_store,decision_sender=_send_decision) if MODE=="approver" else None
class Handler(BaseHTTPRequestHandler):
    server_version="stack-execution-broker"
    def log_message(self,*_):pass
    def reply(self,n,p):b=json.dumps(p).encode();self.send_response(n);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):self.reply(200,_health({})) if self.path=="/health" else self.reply(404,{"error":"Unknown endpoint."})
    def do_POST(self):
        fn=ROUTES.get(self.path)
        if not fn:return self.reply(404,{"error":"Unknown endpoint."})
        callback=self.path=="/approval-grant" and MODE in ("docker","ssh")
        allowed=(MODE=="approver" and self.path=="/request") or callback or (MODE in ("docker","ssh") and self.path in ("/prepare","/execute","/cancel","/discover"))
        if not allowed:return self.reply(401,{"error":"Unauthorized."})
        if not callback:
            path=APPROVAL_REQUEST_SECRET_PATH if MODE=="approver" else CONTROL_SECRET_PATH;header="X-Approval-Secret" if MODE=="approver" else "X-Broker-Secret"
            if not _read(path) or not hmac.compare_digest(_read(path),self.headers.get(header,"")):return self.reply(401,{"error":"Unauthorized."})
        try:n=int(self.headers.get("Content-Length","0"))
        except ValueError:return self.reply(400,{"error":"Invalid Content-Length."})
        if n<0 or n>MAX_REQUEST_BYTES:return self.reply(413,{"error":"Request too large."})
        try:p=json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError,OSError):return self.reply(400,{"error":"Invalid JSON."})
        if not isinstance(p,dict):return self.reply(400,{"error":"Invalid request."})
        if callback:_context.signature=self.headers.get("X-Approval-Signature","")
        try:self.reply(200,fn(p))
        except Exception as e:self.reply(500,{"error":f"Broker failure: {type(e).__name__}"})
        finally:
            if hasattr(_context,"signature"):del _context.signature
class AdminHandler(BaseHTTPRequestHandler):
    server_version="stack-execution-admin"
    def log_message(self,*_):pass
    def _origin_ok(self): return admin_module.allowed_origin(self.headers.get("Origin", ""))
    def reply(self,n,p):
        b=json.dumps(p).encode();self.send_response(n);self.send_header("Content-Type","application/json")
        origin=self.headers.get("Origin","")
        if origin and admin_module.allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin",origin);self.send_header("Vary","Origin")
            self.send_header("Access-Control-Allow-Headers","Content-Type, X-Execution-Admin-Key")
            self.send_header("Access-Control-Allow-Methods","GET, PUT, POST, OPTIONS")
            if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_OPTIONS(self):
        if not self._origin_ok(): return self.reply(403,{"error":"Origin is not allowed."})
        self.reply(204,{})
    def _authorized(self):
        return self._origin_ok() and admin_module.authorized(self.headers.get("X-Execution-Admin-Key",""))
    def _json(self):
        try:n=int(self.headers.get("Content-Length","0"))
        except ValueError:raise ValueError("Invalid Content-Length")
        if n<0 or n>MAX_REQUEST_BYTES:raise ValueError("Request too large")
        try:value=json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError,OSError) as exc:raise ValueError("Invalid JSON") from exc
        if not isinstance(value,dict):raise ValueError("JSON object required")
        return value
    def do_GET(self):
        if self.path=="/health": return self.reply(200,_health({}))
        if not self._authorized(): return self.reply(401,{"error":"Unauthorized."})
        if self.path=="/admin/status": return self.reply(200,admin_module.status())
        if self.path.startswith("/admin/audit"):
            try:
                from urllib.parse import parse_qs,urlsplit
                limit=int(parse_qs(urlsplit(self.path).query).get("limit",["50"])[0])
            except (TypeError,ValueError):limit=50
            return self.reply(200,admin_module.audit(limit))
        self.reply(404,{"error":"Unknown endpoint."})
    def do_PUT(self): self._mutate()
    def do_POST(self): self._mutate()
    def _mutate(self):
        if not self._authorized(): return self.reply(401,{"error":"Unauthorized."})
        try:
            payload=self._json()
            if self.path=="/admin/features" and self.command=="PUT":result=admin_module.set_features(payload)
            elif self.path=="/admin/users" and self.command=="PUT":result=admin_module.set_users(payload)
            elif self.path=="/admin/bot-token" and self.command=="PUT":result=admin_module.replace_bot_token(payload)
            elif self.path=="/admin/rotate-control-secret" and self.command=="POST":result=admin_module.rotate_control_secret()
            else:return self.reply(404,{"error":"Unknown endpoint."})
            self.reply(200,result)
        except ValueError as exc:self.reply(422,{"error":str(exc)})
        except Exception as exc:self.reply(500,{"error":f"Execution admin failure: {type(exc).__name__}"})

def main():
    if MODE=="approver":threading.Thread(target=_telegram.run,daemon=True).start()
    handler=AdminHandler if MODE=="admin" else Handler
    default_port="8752" if MODE=="admin" else ("8751" if MODE=="approver" else "8750")
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("BROKER_PORT",default_port))),handler).serve_forever()
if __name__=="__main__":main()
