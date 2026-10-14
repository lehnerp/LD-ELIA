import os, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as dashboard

class AnalysisTests(unittest.TestCase):
    def parse(self, rows):
        return dashboard.parse_csv(('Date (UTC),Username,IP address,Country,Status,isInteractive,Risk level (aggregate),Risk state\n'+rows).encode())
    def test_local_exclusion_and_confirmation(self):
        d=self.parse('2026-10-01T08:00Z,a,192.0.2.1,CZ,Success,true,high,atRisk\n2026-10-01T09:00Z,a,192.0.2.2,SK,Success,true,high,confirmedCompromised\n')
        self.assertEqual([e['label'] for e in d['events']],['Excluded','Confirmed'])
    def test_72h_context_and_foreign_change(self):
        d=self.parse('2026-09-27T08:00Z,a,192.0.2.1,CZ,Success,true,none,none\n2026-10-01T08:00Z,a,192.0.2.1,CZ,Success,true,none,none\n2026-10-01T08:30Z,A,198.51.100.1,US,Success,true,none,none\n')
        self.assertEqual(d['events'][-1]['contextCount'],2)
        self.assertEqual(d['events'][-1]['label'],'Suspicious')
    def test_unknown_and_noninteractive(self):
        d=self.parse('2026-10-01T08:00Z,a,192.0.2.1,,Unknown,true,,\n2026-10-01T09:00Z,a,192.0.2.2,US,Success,false,high,atRisk\n')
        self.assertEqual(d['nonInteractive'],1)
        self.assertEqual(d['events'][0]['label'],'Review')
    def test_failure_burst(self):
        rows=''.join(f'2026-10-01T08:0{i}Z,a,192.0.2.1,US,Failure,true,,\n' for i in range(5))+'2026-10-01T08:10Z,a,192.0.2.1,US,Success,true,,\n'
        self.assertEqual(self.parse(rows)['events'][-1]['label'],'Suspicious')
    def test_cross_user_failure_pattern(self):
        rows=''.join(f'2026-10-01T08:{i:02d}Z,user{i%3},198.51.100.8,US,Failure,true,,\n' for i in range(10))
        event=self.parse(rows)['events'][-1]
        self.assertEqual(event['label'],'Suspicious')
        self.assertIn('Cross-user failure pattern',[x['name'] for x in event['signals']])
    def test_old_failures_expire(self):
        rows=''.join(f'2026-10-01T08:0{i}Z,a,192.0.2.1,US,Failure,true,,\n' for i in range(5))+'2026-10-01T09:00Z,a,192.0.2.1,US,Success,true,,\n'
        self.assertNotIn('Success after failures',[x['name'] for x in self.parse(rows)['events'][-1]['signals']])
    def test_new_country_is_weak_signal(self):
        d=self.parse('2026-10-01T08:00Z,a,192.0.2.1,CZ,Success,true,,\n2026-10-02T08:00Z,a,192.0.2.2,DE,Success,true,,\n')
        self.assertEqual(d['events'][-1]['score'],25)
        self.assertEqual(d['events'][-1]['label'],'Review')
    def test_device_novelty_and_score_cap(self):
        events=[dict(id='1',time='2026-10-01T08:00:00+00:00',user='a',ip='192.0.2.1',country='CZ',status='Success',risk='',riskState='',device='old'),dict(id='2',time='2026-10-01T08:30:00+00:00',user='a',ip='192.0.2.2',country='US',status='Success',risk='high',riskState='atRisk',device='new')]
        dashboard.analyze(events)
        self.assertIn('New device in window',[s['name'] for s in events[1]['signals']])
        self.assertEqual(events[1]['score'],100)
    def test_future_events_do_not_change_earlier_score(self):
        rows='2026-10-01T08:00Z,a,192.0.2.1,US,Failure,true,,\n'
        before=self.parse(rows)['events'][0]['score']
        rows+=''.join(f'2026-10-01T08:{i:02d}Z,user{i%3},192.0.2.1,US,Failure,true,,\n' for i in range(1,12))
        self.assertEqual(self.parse(rows)['events'][0]['score'],before)
    def test_upload_limit_and_session_required(self):
        self.assertEqual(dashboard.app.config['MAX_CONTENT_LENGTH'],300*1024*1024)
        self.assertEqual(dashboard.app.test_client().post('/api/verdict',json={}).status_code,403)
    def test_investigating_retains_automatic_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard.DB=Path(folder)/'case.sqlite3'
            c=dashboard.app.test_client();c.get('/')
            with c.session_transaction() as sess: token=sess['csrf']
            import io
            c.post('/api/import',data={'file':(io.BytesIO((dashboard.ROOT/'sample.csv').read_bytes()),'sample.csv')},headers={'X-CSRF-Token':token})
            bob=next(e['id'] for e in c.get('/api/data').json['events'] if e['user']=='bob@example.com')
            c.post('/api/verdict',json={'id':bob,'verdict':'Investigating','note':'Checking event evidence'},headers={'X-CSRF-Token':token})
            e=next(e for e in c.get('/api/data').json['events'] if e['id']==bob)
            self.assertEqual(e['label'],'Investigating')
            self.assertEqual(e['autoLabel'],'Confirmed')
            self.assertEqual(e['autoScore'],100)
            self.assertTrue(any(s['name']=='Confirmed by Entra' for s in e['signals']))
    def test_coordinates_import_and_validation(self):
        raw='Date (UTC),Username,IP address,Country,Latitude,Longitude\n2026-10-01T08:00Z,a,192.0.2.1,CZ,50.1,14.4\n2026-10-01T09:00Z,a,192.0.2.2,US,999,0\n'
        events=dashboard.parse_csv(raw.encode())['events']
        self.assertEqual(events[0]['latitude'],50.1)
        self.assertEqual(events[0]['longitude'],14.4)
        self.assertEqual(events[0]['coordinateSource'],'CSV coordinates')
        self.assertIsNone(events[1]['latitude'])
        self.assertEqual(dashboard.coordinates(0,0),(0.0,0.0))
        self.assertEqual(dashboard.coordinates('NaN',12),(None,None))
    def test_existing_json_locations_get_coordinates(self):
        e=dict(id='1',time='2026-10-01T08:00:00+00:00',user='a',ip='192.0.2.1',country='CZ',status='Success',risk='',riskState='',location='{"countryOrRegion":"CZ","geoCoordinates":{"latitude":50.1,"longitude":14.4}}')
        dashboard.analyze([e]);self.assertEqual(e['latitude'],50.1)
        self.assertEqual(e['longitude'],14.4)
    def test_api_csrf_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard.DB=Path(folder)/'test.sqlite3'
            c=dashboard.app.test_client(); c.get('/')
            self.assertEqual(c.post('/api/verdict',json={}).status_code,403)
            with c.session_transaction() as s: token=s['csrf']
            import io
            raw=Path(dashboard.ROOT/'sample.csv').read_bytes()
            self.assertEqual(c.post('/api/import',data={'file':(io.BytesIO(raw),'sample.csv')},headers={'X-CSRF-Token':token}).status_code,200)
            self.assertEqual(len(c.get('/api/data').json['events']),5)
            target=next(e['id'] for e in c.get('/api/data').json['events'] if e['ip']=='198.51.100.21')
            self.assertEqual(c.post('/api/verdict',json={'id':target,'verdict':'Confirmed','note':'Verified with incident evidence'},headers={'X-CSRF-Token':token}).status_code,200)
            self.assertTrue(next(e for e in c.get('/api/data').json['events'] if e['id']==target)['analyst'])
            self.assertEqual(next(e for e in c.get('/api/data').json['events'] if e['id']==target)['score'],100)
            c.post('/api/verdict',json={'id':target,'verdict':'Dismissed','note':'Verified legitimate VPN'},headers={'X-CSRF-Token':token})
            event=next(e for e in c.get('/api/data').json['events'] if e['id']==target)
            self.assertEqual(event['score'],0)
            self.assertEqual(len(event['decisionHistory']),2)

class ContinuousCaseTests(unittest.TestCase):
    """The SQLite case file must support ongoing investigation across imports and restarts."""
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(); dashboard.DB=Path(self.folder.name)/'case.sqlite3'
        self.c=dashboard.app.test_client(); self.c.get('/')
        with self.c.session_transaction() as s: self.token=s['csrf']
    def tearDown(self): self.folder.cleanup()
    def upload(self,text,mode='merge',name='export.csv'):
        import io
        return self.c.post('/api/import',data={'file':(io.BytesIO(text.encode()),name),'mode':mode},headers={'X-CSRF-Token':self.token})
    def mark(self,id,verdict='Confirmed',note='Evidence'):
        return self.c.post('/api/verdict',json={'id':id,'verdict':verdict,'note':note},headers={'X-CSRF-Token':self.token})
    def events(self): return self.c.get('/api/data').json['events']
    H='Date (UTC),Username,IP address,Country,Status,Risk state\n'
    A='2026-10-01T08:00Z,a@x,192.0.2.1,CZ,Success,none\n2026-10-01T09:00Z,a@x,198.51.100.1,US,Success,atRisk\n'
    B='2026-10-01T09:00Z,a@x,198.51.100.1,US,Success,remediated\n2026-10-02T10:00Z,b@x,203.0.113.5,DE,Failure,none\n'
    def test_reimport_is_idempotent(self):
        first=self.upload(self.H+self.A).json; second=self.upload(self.H+self.A).json
        self.assertEqual((first['added'],second['added'],second['unchanged']),(2,0,2))
        self.assertEqual(len(self.events()),2)
    def test_overlapping_export_merges_and_keeps_marks(self):
        self.upload(self.H+self.A)
        us=next(e for e in self.events() if e['country']=='US')
        self.assertEqual(self.mark(us['id'],'Suspicious','Unexpected US login').status_code,200)
        result=self.upload(self.H+self.B,name='next-week.csv').json
        self.assertEqual((result['added'],result['updated'],result['total']),(1,1,3))
        after=next(e for e in self.events() if e['id']==us['id'])
        self.assertEqual(after['label'],'Suspicious'); self.assertEqual(after['riskState'],'remediated')
        self.assertEqual(len(after['decisionHistory']),1)
        data=self.c.get('/api/data').json
        self.assertEqual([i['filename'] for i in data['imports']],['next-week.csv','export.csv'])
    def test_genuine_duplicate_rows_are_kept(self):
        r=self.upload(self.H+'2026-10-01T08:00Z,a@x,192.0.2.1,US,Failure,none\n'*3).json
        self.assertEqual(r['added'],3)
        self.assertEqual(self.upload(self.H+'2026-10-01T08:00Z,a@x,192.0.2.1,US,Failure,none\n'*3).json['added'],0)
    def test_replace_starts_new_case(self):
        self.upload(self.H+self.A); self.mark(self.events()[1]['id'])
        r=self.upload(self.H+self.B,mode='replace').json
        self.assertEqual(r['total'],2)
        data=self.c.get('/api/data').json
        self.assertEqual(data['marked'],0); self.assertEqual(len(data['imports']),1)
    def test_invalid_mode_rejected(self):
        self.assertEqual(self.upload(self.H+self.A,mode='drop').status_code,400)
    def test_case_survives_restart(self):
        self.upload(self.H+self.A); self.mark(self.events()[1]['id'],'Investigating','Ticket 42')
        import importlib; db=dashboard.DB
        reloaded=importlib.reload(dashboard); reloaded.DB=db
        c=reloaded.app.test_client()
        self.assertEqual([e['label'] for e in c.get('/api/data').json['events']],['Excluded','Investigating'])
    def test_legacy_database_is_migrated_with_marks(self):
        import json, sqlite3
        legacy=dashboard.parse_csv((self.H+self.A).encode())
        for i,e in enumerate(legacy['events']): e['id']=str(i)
        db=sqlite3.connect(dashboard.DB)
        db.execute('CREATE TABLE datasets (id INTEGER PRIMARY KEY, payload TEXT)')
        db.execute('CREATE TABLE verdicts (event TEXT PRIMARY KEY, verdict TEXT, note TEXT)')
        db.execute('CREATE TABLE decisions (id INTEGER PRIMARY KEY, event TEXT, verdict TEXT, note TEXT, time TEXT)')
        db.execute('INSERT INTO datasets VALUES (1,?)',(json.dumps(legacy),))
        db.execute("INSERT INTO verdicts VALUES ('1','Confirmed','Old mark')")
        db.execute("INSERT INTO decisions(event,verdict,note,time) VALUES ('1','Confirmed','Old mark','2026-10-02T00:00:00+00:00')")
        db.commit(); db.close()
        dashboard.BACKUPS=Path(self.folder.name)/'backups'
        events=self.events()
        self.assertEqual(len(list(dashboard.BACKUPS.glob('data-before-upgrade-*.sqlite3'))),1)
        us=next(e for e in events if e['country']=='US')
        self.assertEqual((us['label'],us['analystNote'],len(us['decisionHistory'])),('Confirmed','Old mark',1))
        self.assertEqual(self.upload(self.H+self.A).json['added'],0)
    def test_backup_snapshot(self):
        self.assertIsNone(dashboard.backup())
        self.upload(self.H+self.A)
        dashboard.BACKUPS=Path(self.folder.name)/'backups'
        target=dashboard.backup()
        import sqlite3
        db=sqlite3.connect(target); self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],2); db.close()
        r=self.c.get('/api/backup'); self.assertEqual(r.status_code,200); self.assertTrue(r.data.startswith(b'SQLite format 3'))

class HomeCountrySettingsTests(unittest.TestCase):
    ROWS='Date (UTC),Username,IP address,Country,Status\n'+''.join(f'2026-10-01T08:{i:02d}Z,user{i%3},192.0.2.9,CZ,Failure\n' for i in range(10))+'2026-10-01T08:20Z,user0,192.0.2.9,CZ,Success\n2026-10-01T09:00Z,user1,198.51.100.4,DE,Success\n'
    def events(self,settings=None):
        events=dashboard.parse_csv(self.ROWS.encode())['events']
        dashboard.analyze(events,settings); return events
    def test_default_excludes_cz_sk(self):
        e=self.events()
        self.assertTrue(all(x['label']=='Excluded' for x in e if x['country']=='CZ'))
    def test_exclusion_off_scores_home_spray(self):
        e=self.events({'excludeHome':False,'homeCountries':['CZ','SK']})
        cz=[x for x in e if x['country']=='CZ']
        self.assertIn('Cross-user failure pattern',[s['name'] for s in cz[9]['signals']])
        self.assertEqual(cz[9]['label'],'Suspicious')
        self.assertNotIn('Location review',[s['name'] for x in cz for s in x['signals']])
        self.assertEqual(e[0]['label'],'Clear')
    def test_other_home_country(self):
        e=self.events({'excludeHome':True,'homeCountries':['DE']})
        self.assertEqual(e[-1]['label'],'Excluded')
        self.assertIn('Location review',[s['name'] for s in e[0]['signals']])
    def test_settings_api_validates_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard.DB=Path(folder)/'s.sqlite3'
            c=dashboard.app.test_client(); c.get('/')
            with c.session_transaction() as s: token=s['csrf']
            h={'X-CSRF-Token':token}
            self.assertEqual(c.get('/api/data').json['settings'],{'excludeHome':True,'homeCountries':['CZ','SK']})
            self.assertEqual(c.post('/api/settings',json={'homeCountries':'CZ, Narnia','excludeHome':True},headers=h).status_code,400)
            r=c.post('/api/settings',json={'homeCountries':'cz, Slovakia, AT, cz','excludeHome':False},headers=h)
            self.assertEqual(r.json['settings'],{'excludeHome':False,'homeCountries':['CZ','SK','AT']})
            self.assertEqual(c.get('/api/data').json['settings']['excludeHome'],False)

if __name__=='__main__': unittest.main()
