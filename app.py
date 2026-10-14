"""Entra Local Sign-in Dashboard.

Local-only Flask app. All imported sign-ins, analyst marks and decision history live in a
single SQLite case file (data.sqlite3 by default) so an investigation can continue across
imports and restarts.
"""
import argparse, csv, hashlib, io, ipaddress, json, os, re, secrets, shutil, sqlite3, sys, math, tempfile
from collections import deque, Counter
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, render_template, session, send_file

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('DASHBOARD_DB', str(ROOT/'data.sqlite3')))
BACKUPS = Path(os.environ.get('DASHBOARD_BACKUPS', str(ROOT/'backups')))
PORT = int(os.environ.get('DASHBOARD_PORT', '8585'))
SCHEMA_VERSION = 2
DEFAULT_SETTINGS = {'excludeHome': True, 'homeCountries': ['CZ', 'SK']}
# Fields kept in the case file. Scores, labels and signals are always recalculated.
RAW_FIELDS = ('id','time','user','ip','country','location','geoSource','latitude','longitude','coordinateSource','status','app','risk','riskState','auth','device','riskDetail','requestId')


def secret_key():
    """Stable session key so a restart does not invalidate the open browser tab."""
    if os.environ.get('DASHBOARD_SECRET'): return os.environ['DASHBOARD_SECRET']
    path = ROOT/'.secret_key'
    try:
        if path.exists():
            value = path.read_text(encoding='ascii').strip()
            if len(value) >= 32: return value
        value = secrets.token_hex(32)
        path.write_text(value, encoding='ascii')
        return value
    except OSError:
        return secrets.token_hex(32)


app = Flask(__name__)
app.config.update(SECRET_KEY=secret_key(), MAX_CONTENT_LENGTH=300*1024*1024, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_HTTPONLY=True)


def fingerprint_base(e):
    """Fields that identify one sign-in across overlapping exports.

    Risk state and country are deliberately excluded: Entra can update risk later and
    country may come from optional GeoIP, so a newer export updates the same event."""
    return '|'.join(str(e.get(k) or '') for k in ('time','user','ip','status','app','device')).casefold()


def assign_ids(events):
    """Stable event IDs. Exact repeats inside one export get an occurrence suffix so that
    re-importing the same file is idempotent while genuine repeats are kept."""
    seen = Counter()
    for e in events:
        base = fingerprint_base(e)
        n = seen[base]; seen[base] += 1
        e['id'] = hashlib.sha256(f'{base}#{n}'.encode('utf-8')).hexdigest()[:20]
    return events


def stored(e):
    return {k: e.get(k) for k in RAW_FIELDS if k in e}


def open_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS imports (id INTEGER PRIMARY KEY, filename TEXT, time TEXT, mode TEXT,
            rows_valid INTEGER, added INTEGER, updated INTEGER, unchanged INTEGER, invalid INTEGER,
            non_interactive INTEGER, assumed_interactive INTEGER, window_start TEXT, window_end TEXT);
        CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, time TEXT NOT NULL,
            user TEXT, ip TEXT, payload TEXT NOT NULL, import_id INTEGER, updated_import_id INTEGER);
        CREATE INDEX IF NOT EXISTS events_time ON events(time);
        CREATE TABLE IF NOT EXISTS verdicts (event TEXT PRIMARY KEY, verdict TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, event TEXT, verdict TEXT, note TEXT, time TEXT);
        CREATE INDEX IF NOT EXISTS decisions_event ON decisions(event);
    ''')
    migrate(db)
    return db


@contextmanager
def connect():
    """Open, commit on success, roll back on error, and always close (Windows keeps files locked otherwise)."""
    db = open_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


def migrate(db):
    """Upgrade a v4 case file (one JSON dataset blob, row-index IDs) without losing marks."""
    row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if row and int(row[0]) >= SCHEMA_VERSION: return
    has_legacy = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='datasets'").fetchone()
    if has_legacy and not db.execute('SELECT 1 FROM events LIMIT 1').fetchone():
        try:  # keep an untouched copy of the previous-version database before upgrading it
            BACKUPS.mkdir(parents=True, exist_ok=True)
            out=sqlite3.connect(BACKUPS/f"data-before-upgrade-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3")
            db.backup(out); out.close()
        except (OSError, sqlite3.Error): pass
        legacy = db.execute('SELECT payload FROM datasets WHERE id=1').fetchone()
        if legacy:
            payload = json.loads(legacy[0])
            events = sorted(payload.get('events', []), key=lambda e: e['time'])
            old_ids = [str(e.get('id')) for e in events]
            assign_ids(events)
            mapping = dict(zip(old_ids, (e['id'] for e in events)))
            cur = db.execute('INSERT INTO imports(filename,time,mode,rows_valid,added,updated,unchanged,invalid,non_interactive,assumed_interactive,window_start,window_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('Migrated from previous version', payload.get('importedAt') or datetime.now(timezone.utc).isoformat(), 'migrate', len(events), len(events), 0, 0,
                 payload.get('invalid', 0), payload.get('nonInteractive', 0), payload.get('assumedInteractive', 0),
                 events[0]['time'] if events else None, events[-1]['time'] if events else None))
            db.executemany('INSERT OR IGNORE INTO events(id,time,user,ip,payload,import_id) VALUES (?,?,?,?,?,?)',
                [(e['id'], e['time'], e['user'], e['ip'], json.dumps(stored(e)), cur.lastrowid) for e in events])
            # Two-step rename: prefix old row-index IDs first so they cannot collide with new IDs.
            for table in ('verdicts', 'decisions'):
                db.execute(f"UPDATE {table} SET event='old:'||event")
                db.executemany(f'UPDATE {table} SET event=? WHERE event=?', [(new, 'old:'+old) for old, new in mapping.items()])
        db.execute('DROP TABLE datasets')
    db.execute("INSERT OR REPLACE INTO meta VALUES ('schema',?)", (str(SCHEMA_VERSION),))
    db.commit()


def norm(s): return re.sub(r'[^a-z0-9]', '', str(s).lower())
def date(s):
    try:
        d=datetime.fromisoformat(str(s).strip().replace('Z','+00:00'))
    except ValueError:
        try:
            from dateutil.parser import parse
            d=parse(s, dayfirst=False)
        except Exception: return None
    return (d.replace(tzinfo=timezone.utc) if not d.tzinfo else d).astimezone(timezone.utc)

COUNTRIES={'cz':'CZ','cze':'CZ','czechia':'CZ','czechrepublic':'CZ','česko':'CZ','českárepublika':'CZ','sk':'SK','svk':'SK','slovakia':'SK','slovensko':'SK','unitedstates':'US','usa':'US','unitedkingdom':'GB','germany':'DE','austria':'AT','poland':'PL','netherlands':'NL','france':'FR','russia':'RU','china':'CN'}
def country(s):
    key=str(s).strip().lower().replace(' ','')
    return COUNTRIES.get(key, key.upper() if len(key)==2 and key.isalpha() else '')

def coordinates(lat, lon):
    try:
        lat,lon=float(lat),float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180: return lat,lon
    except (TypeError,ValueError): pass
    return None,None

def location_json(value):
    try:
        result=json.loads(value)
        return result if isinstance(result,dict) else {}
    except (ValueError,TypeError): return {}

def parse_csv(raw):
    try: text=raw.decode('utf-8-sig')
    except UnicodeDecodeError: text=raw.decode('cp1250')
    try: dialect=csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
    except csv.Error: dialect=csv.excel
    reader=csv.DictReader(io.StringIO(text), dialect=dialect)
    headers={norm(h) for h in reader.fieldnames or []}
    if not headers.intersection({'date','dateutc','createddatetime','timestamp','datetime'}): raise ValueError('Missing date column. Use Date (UTC), CreatedDateTime or Timestamp.')
    if not headers.intersection({'username','userprincipalname','upn','user'}): raise ValueError('Missing user column. Use Username or UserPrincipalName.')
    events=[]; invalid=0; excluded=0; assumed=0
    geo=None
    if os.environ.get('GEOIP_DB'):
        try:
            import geoip2.database
            geo=geoip2.database.Reader(os.environ['GEOIP_DB'])
        except Exception: raise ValueError('GEOIP_DB could not be opened. Install geoip2 and supply a valid Country or City MMDB.')
    try:
        for index,row in enumerate(reader):
            if index>=100000: raise ValueError('Maximum 100,000 rows per import. Split the export.')
            r={norm(k):v or '' for k,v in row.items() if k}
            def get(*keys): return next((r[norm(k)].strip() for k in keys if r.get(norm(k),'').strip()),'')
            interactive=get('isInteractive','interactive','signInEventTypes','sign-in type')
            if norm(interactive) in ('false','no','0') or 'noninteractive' in norm(interactive): excluded+=1; continue
            if not interactive: assumed+=1
            dt=date(get('Date (UTC)','Date','createdDateTime','Timestamp','DateTime'))
            user=get('Username','UserPrincipalName','UPN','User')
            ip=get('IP address','IPAddress','IP')
            if not dt or not user: invalid+=1; continue
            try: ip=str(ipaddress.ip_address(ip))
            except ValueError: invalid+=1; continue
            loc=get('Location'); locdata=location_json(loc)
            coorddata=locdata.get('geoCoordinates') or {}
            if not isinstance(coorddata,dict): coorddata={}
            lat,lon=coordinates(get('Latitude','Location latitude','geoCoordinates.latitude') or coorddata.get('latitude'),get('Longitude','Location longitude','geoCoordinates.longitude') or coorddata.get('longitude'))
            coordinate_source='CSV coordinates' if lat is not None else ''
            cc=country(get('Country','CountryOrRegion','Country code','Location country'))
            if not cc and loc:
                try: cc=country(json.loads(loc).get('countryOrRegion',''))
                except (ValueError,AttributeError): cc=country(loc.split(',')[-1])
            source='CSV' if cc else 'Unknown'
            if geo and (not cc or lat is None):
                try:
                    resolved=geo.city(ip)
                except Exception:
                    try: resolved=geo.country(ip)
                    except Exception: resolved=None
                if resolved:
                    resolved_country=resolved.country.iso_code or ''
                    if not cc: cc=resolved_country; source='Offline GeoIP'
                    if lat is None and (not cc or cc==resolved_country) and hasattr(resolved,'location'):
                        lat,lon=coordinates(resolved.location.latitude,resolved.location.longitude)
                        if lat is not None: coordinate_source='Offline GeoIP coordinates'
            status=get('Status','Result','Sign-in status'); code=get('Sign-in error code','errorCode','ResultType')
            if code: status='Success' if code=='0' else 'Failure'
            elif norm(status) in ('success','succeeded','successful'): status='Success'
            elif norm(status) in ('failure','failed','interrupted'): status='Failure'
            else: status='Unknown'
            events.append(dict(id=str(index),time=dt.isoformat(),user=user,ip=ip,country=cc or 'Unknown',location=loc,geoSource=source,latitude=lat,longitude=lon,coordinateSource=coordinate_source,status=status,app=get('Application','appDisplayName') or 'Unknown',risk=get('Risk level (aggregate)','riskLevelAggregated','Risk level','riskLevelDuringSignIn'),riskState=get('Risk state','riskState'),auth=get('Authentication requirement','authenticationRequirement'),device=get('Device ID','deviceId'),riskDetail=get('Risk detail','riskDetail'),requestId=get('Request ID','requestId','Correlation ID','correlationId')))
    finally:
        if geo: geo.close()
    if not events: raise ValueError('No valid interactive sign-ins found. Check dates, usernames and IP addresses.')
    events.sort(key=lambda e:e['time'])
    assign_ids(events)
    analyze(events)
    return dict(events=events,invalid=invalid,nonInteractive=excluded,assumedInteractive=assumed,importedAt=datetime.now(timezone.utc).isoformat(),windowStart=events[0]['time'],windowEnd=events[-1]['time'])

def analyze(events, settings=None):
    """Deterministic triage scores, not compromise probabilities. Windows use past events only.

    settings: homeCountries (list of ISO codes) and excludeHome (bool). With excludeHome, sign-ins
    from home countries skip geographic and heuristic scoring (label Excluded). Without it they are
    scored like any other sign-in, except that being at home never adds location points."""
    settings={**DEFAULT_SETTINGS,**(settings or {})}
    home=set(settings['homeCountries']); exclude=bool(settings['excludeHome']) and bool(home)
    home_text=', '.join(sorted(home)) or 'none configured'
    users={}; ips={}
    for e in sorted(events,key=lambda e:e['time']):
        e.setdefault('device',''); e.setdefault('riskDetail','')
        if 'latitude' not in e:
            old=location_json(e.get('location','')).get('geoCoordinates') or {}
            if not isinstance(old,dict): old={}
            e['latitude'],e['longitude']=coordinates(old.get('latitude'),old.get('longitude'))
            e['coordinateSource']='CSV coordinates' if e['latitude'] is not None else ''
        state=users.setdefault(e['user'].casefold(),{'history':deque(),'failures':deque(),'success':None,'countries':Counter(),'ips':Counter(),'devices':Counter()})
        now=date(e['time']); cutoff=(now-timedelta(hours=72)).isoformat()
        history=state['history']
        while history and history[0]['time']<cutoff:
            old=history.popleft()
            for field,key in [('country','countries'),('ip','ips'),('device','devices')]:
                value=old.get(field,''); state[key][value]-=1
                if not state[key][value]: del state[key][value]
        short=(now-timedelta(minutes=30)).isoformat()
        while state['failures'] and state['failures'][0]<short: state['failures'].popleft()
        network=ips.setdefault(e['ip'],{'failures':deque(),'users':Counter()})
        while network['failures'] and network['failures'][0][0]<short:
            _,u=network['failures'].popleft(); network['users'][u]-=1
            if not network['users'][u]: del network['users'][u]
        if e['status']=='Failure':
            network['failures'].append((e['time'],e['user'].casefold())); network['users'][e['user'].casefold()]+=1
        local=exclude and e['country'] in home; signals=[]
        def signal(name,points,detail): signals.append(dict(name=name,points=points,detail=detail))
        if not local:
            if e['country']=='Unknown': signal('Location review',5,'Country unavailable')
            elif home and e['country'] not in home: signal('Location review',10,f'Outside home countries ({home_text})')
            risk=norm(e.get('risk',''))
            if risk in ('low','medium','high'): signal('Entra risk',{'low':10,'medium':35,'high':55}[risk],e['risk'])
            if norm(e.get('riskState',''))=='atrisk': signal('Entra at risk',20,'Entra risk state is atRisk')
            fail=len(state['failures'])
            if fail>=5 and e['status']=='Success': signal('Success after failures',35,f'{fail} preceding failures for user in 30 minutes')
            elif fail>=4 and e['status']=='Failure': signal('Repeated user failures',20,f'{fail+1} user failures in 30 minutes including this event')
            previous=state['success']
            if previous and e['status']=='Success' and e['country']!='Unknown' and previous['country']!=e['country'] and now-date(previous['time'])<timedelta(hours=1):
                signal('Rapid country change',30,f"Previous successful sign-in: {previous['country']} at {previous['time']}. VPNs may explain.")
            known_countries=[c for c,n in state['countries'].items() if n>0 and c!='Unknown']
            if known_countries and e['country']!='Unknown' and not state['countries'][e['country']]: signal('New country in window',10,'Not seen for this user in the preceding imported 72 hours')
            if history and not state['ips'][e['ip']]: signal('New IP in window',5,'Not seen for this user in the preceding imported 72 hours')
            if e['device'] and any(n>0 and d for d,n in state['devices'].items()) and not state['devices'][e['device']]: signal('New device in window',10,'Device ID differs from preceding imported 72-hour history')
            if len(network['failures'])>=10 and len(network['users'])>=3:
                signal('Cross-user failure pattern',40,f"IP has {len(network['failures'])} failures across {len(network['users'])} users in 30 minutes; possible password spray or shared network")
        confirmed=norm(e.get('riskState',''))=='confirmedcompromised'
        if confirmed: signal('Confirmed by Entra',100,'Explicit Entra confirmedCompromised risk state')
        score=min(100,sum(x['points'] for x in signals))
        label='Confirmed' if confirmed else 'Excluded' if local else 'Suspicious' if score>=35 else 'Review' if score>0 else 'Clear'
        e.update(score=score,autoScore=score,signals=signals,label=label,autoLabel=label,reasons=[x['name']+': '+x['detail'] for x in signals],contextCount=len(history)+1)
        history.append(e)
        for field,key in [('country','countries'),('ip','ips'),('device','devices')]: state[key][e.get(field,'')]+=1
        if e['status']=='Failure': state['failures'].append(e['time'])
        if e['status']=='Success' and e['country']!='Unknown': state['success']=e

def store_import(db, payload, filename, mode):
    """Merge (default) or replace. Returns counts. Marks on existing events are kept on merge."""
    if mode == 'replace':
        for table in ('events', 'verdicts', 'decisions', 'imports'): db.execute(f'DELETE FROM {table}')
    cur = db.execute('INSERT INTO imports(filename,time,mode,rows_valid,invalid,non_interactive,assumed_interactive,window_start,window_end) VALUES (?,?,?,?,?,?,?,?,?)',
        (filename[:255], payload['importedAt'], mode, len(payload['events']), payload['invalid'], payload['nonInteractive'],
         payload['assumedInteractive'], payload['windowStart'], payload['windowEnd']))
    import_id = cur.lastrowid
    existing = dict(db.execute('SELECT id,payload FROM events'))
    added = updated = unchanged = 0
    inserts, updates = [], []
    for e in payload['events']:
        body = json.dumps(stored(e))
        old = existing.get(e['id'])
        if old is None:
            inserts.append((e['id'], e['time'], e['user'], e['ip'], body, import_id)); added += 1
        elif old != body:
            # Keep values the new export does not supply (e.g. coordinates from an older richer export).
            merged = json.loads(old)
            merged.update({k: v for k, v in stored(e).items() if v not in ('', None)})
            merged_body = json.dumps(merged)
            if merged_body != old:
                updates.append((merged_body, import_id, e['id'])); updated += 1
            else: unchanged += 1
        else: unchanged += 1
    db.executemany('INSERT INTO events(id,time,user,ip,payload,import_id) VALUES (?,?,?,?,?,?)', inserts)
    db.executemany('UPDATE events SET payload=?, updated_import_id=? WHERE id=?', updates)
    db.execute('UPDATE imports SET added=?,updated=?,unchanged=? WHERE id=?', (added, updated, unchanged, import_id))
    return dict(added=added, updated=updated, unchanged=unchanged, invalid=payload['invalid'], nonInteractive=payload['nonInteractive'], total=db.execute('SELECT COUNT(*) FROM events').fetchone()[0])


def load_settings(db):
    row=db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
    try: saved=json.loads(row[0]) if row else {}
    except ValueError: saved={}
    return {**DEFAULT_SETTINGS,**{k:v for k,v in saved.items() if k in DEFAULT_SETTINGS}}


def clean_settings(body):
    if not isinstance(body,dict): raise ValueError('Invalid settings.')
    codes=body.get('homeCountries',[])
    if isinstance(codes,str): codes=re.split(r'[,;\s]+',codes)
    if not isinstance(codes,list) or len(codes)>50: raise ValueError('Enter up to 50 home countries.')
    home=[]
    for raw_code in codes:
        if not str(raw_code).strip(): continue
        code=country(raw_code)
        if not re.fullmatch(r'[A-Z]{2}',code): raise ValueError(f'Unknown country "{str(raw_code)[:40]}". Use two-letter codes such as CZ, SK, AT.')
        if code not in home: home.append(code)
    return dict(excludeHome=bool(body.get('excludeHome')),homeCountries=home)


def load_case(db):
    events = [json.loads(p) for (p,) in db.execute('SELECT payload FROM events ORDER BY time, seq')]
    imports = [dict(zip(('id','filename','time','mode','rowsValid','added','updated','unchanged','invalid','nonInteractive','assumedInteractive','windowStart','windowEnd'), r))
               for r in db.execute('SELECT id,filename,time,mode,rows_valid,added,updated,unchanged,invalid,non_interactive,assumed_interactive,window_start,window_end FROM imports ORDER BY id DESC')]
    return events, imports


@app.before_request
def guard():
    if request.method=='POST' and (not session.get('csrf') or request.headers.get('X-CSRF-Token') != session.get('csrf')): return jsonify(error='Invalid session token. Reload the dashboard.'),403

@app.after_request
def security(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'"
    if request.path.startswith('/api/'): response.headers['Cache-Control']='no-store'
    return response

@app.get('/')
def home():
    session.setdefault('csrf',secrets.token_hex(32))
    return render_template('index.html',token=session['csrf'])

@app.get('/api/data')
def data():
    with connect() as db:
        events, imports = load_case(db)
        verdicts={id:(v,n) for id,v,n in db.execute('SELECT event,verdict,note FROM verdicts')}
        settings=load_settings(db)
        audit={}
        for event,v,n,t in db.execute('SELECT event,verdict,note,time FROM decisions ORDER BY id DESC'):
            audit.setdefault(event,[]).append(dict(verdict=v,note=n,time=t))
    analyze(events,settings)
    for e in events:
        e['decisionHistory']=audit.get(e['id'],[])
        if e['id'] in verdicts:
            e.update(label=verdicts[e['id']][0],analystNote=verdicts[e['id']][1],analyst=True)
            if e['label']=='Confirmed': e['score']=100
            if e['label']=='Suspicious': e['score']=max(35,e['score'])
            if e['label']=='Dismissed': e['score']=0
    totals=lambda key: sum((i.get(key) or 0) for i in imports)
    return jsonify(events=events, imports=imports, invalid=totals('invalid'), nonInteractive=totals('nonInteractive'), assumedInteractive=totals('assumedInteractive'),
                   importedAt=imports[0]['time'] if imports else None, windowStart=events[0]['time'] if events else None, windowEnd=events[-1]['time'] if events else None,
                   marked=len(verdicts), caseFile=DB.name, settings=settings)

@app.get('/api/sample')
def sample():
    return send_file(ROOT/'sample-investigation.csv',mimetype='text/csv',as_attachment=True)

@app.post('/api/import')
def upload():
    f=request.files.get('file')
    if not f: return jsonify(error='Select a CSV file.'),400
    mode=request.form.get('mode','merge')
    if mode not in ('merge','replace'): return jsonify(error='Invalid import mode.'),400
    try: payload=parse_csv(f.read())
    except (ValueError,csv.Error) as ex: return jsonify(error=str(ex)),400
    with connect() as db:
        result=store_import(db,payload,f.filename or 'upload.csv',mode)
    return jsonify(ok=True,mode=mode,**result)

@app.post('/api/verdict')
def verdict():
    body=request.get_json(silent=True) or {}
    if body.get('verdict') not in ('Confirmed','Dismissed','Suspicious','Investigating','Reset'): return jsonify(error='Invalid verdict'),400
    note=str(body.get('note','')).strip()[:2000]
    if body['verdict']!='Reset' and not note: return jsonify(error='Enter investigation evidence or a reason.'),400
    event=str(body.get('id'))
    with connect() as db:
        if not db.execute('SELECT 1 FROM events WHERE id=?',(event,)).fetchone(): return jsonify(error='Event not found'),404
        db.execute('INSERT INTO decisions(event,verdict,note,time) VALUES (?,?,?,?)',(event,body['verdict'],note,datetime.now(timezone.utc).isoformat()))
        if body['verdict']=='Reset': db.execute('DELETE FROM verdicts WHERE event=?',(event,))
        else: db.execute('INSERT OR REPLACE INTO verdicts VALUES (?,?,?)',(event,body['verdict'],note))
    return jsonify(ok=True)

@app.post('/api/settings')
def save_settings():
    try: settings=clean_settings(request.get_json(silent=True))
    except ValueError as ex: return jsonify(error=str(ex)),400
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('settings',?)",(json.dumps(settings),))
    return jsonify(ok=True,settings=settings)

@app.get('/api/backup')
def download_backup():
    """Consistent snapshot of the case file (safe while the app is running)."""
    folder=tempfile.mkdtemp()
    target=Path(folder)/'case.sqlite3'
    snapshot(target)
    data=target.read_bytes(); shutil.rmtree(folder, ignore_errors=True)
    return send_file(io.BytesIO(data),mimetype='application/vnd.sqlite3',as_attachment=True,download_name=f"entra-case-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3")

@app.errorhandler(413)
def too_large(e): return jsonify(error='CSV exceeds 300 MB limit.'),413


def snapshot(target):
    with connect() as db:
        out=sqlite3.connect(target)
        try: db.backup(out)
        finally: out.close()


def backup(keep=20):
    """Timestamped copy in backups/, keeping the newest `keep`. No-op when there is no case yet."""
    if not DB.exists(): return None
    with connect() as db:
        if not db.execute('SELECT 1 FROM events LIMIT 1').fetchone(): return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    target=BACKUPS/f"data-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    snapshot(target)
    for old in sorted(BACKUPS.glob('data-*.sqlite3'))[:-keep]:
        try: old.unlink()
        except OSError: pass
    return target


def status():
    with connect() as db:
        events=db.execute('SELECT COUNT(*), MIN(time), MAX(time) FROM events').fetchone()
        imports=db.execute('SELECT COUNT(*) FROM imports').fetchone()[0]
        marks=db.execute('SELECT COUNT(*) FROM verdicts').fetchone()[0]
        decisions=db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
    if not events[0]: return f'Case file {DB}: empty (import a CSV to start).'
    return f'Case file {DB}: {events[0]} sign-ins from {imports} import(s), {events[1][:16]} to {events[2][:16]} UTC, {marks} marked event(s), {decisions} decision(s).'


def port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def serve(open_browser=False):
    url=f'http://127.0.0.1:{PORT}'
    if port_in_use(PORT):
        print(f'Port {PORT} is already in use. The dashboard is probably already running in another window.')
        if open_browser:
            import webbrowser; webbrowser.open(url); print(f'Opened {url}')
        print('If another program uses this port, set DASHBOARD_PORT to a free port and start again.')
        sys.exit(3)
    print(status()); print(f'Dashboard: {url}  (keep this window open; press Ctrl+C to stop)', flush=True)
    if open_browser:
        import threading, webbrowser
        def opener():
            import time
            for _ in range(50):
                if port_in_use(PORT): break
                time.sleep(0.2)
            webbrowser.open(url)
        threading.Thread(target=opener, daemon=True).start()
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        try: app.run(host='127.0.0.1', port=PORT, debug=False)
        except KeyboardInterrupt: print('Dashboard stopped.')
        return
    try: waitress_serve(app, host='127.0.0.1', port=PORT, threads=6, max_request_body_size=310*1024*1024, _quiet=True)
    except KeyboardInterrupt: print('Dashboard stopped.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Entra local sign-in dashboard')
    parser.add_argument('--init', action='store_true', help='create or upgrade the case database, then exit')
    parser.add_argument('--backup', action='store_true', help='write a timestamped backup to backups/, then exit')
    parser.add_argument('--status', action='store_true', help='print case summary, then exit')
    parser.add_argument('--open', action='store_true', help='open the dashboard in the default browser once it is ready')
    args=parser.parse_args()
    if args.init: print(status())
    elif args.backup:
        target=backup(); print(f'Backup written: {target}' if target else 'Nothing to back up yet.')
    elif args.status: print(status())
    else: serve(open_browser=args.open)
