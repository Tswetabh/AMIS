from pathlib import Path
import os, secrets, sqlite3
from functools import wraps
import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
data = Path(os.environ.get('AMIS_DATA', app.instance_path)); data.mkdir(parents=True, exist_ok=True)
key = data / 'session.key'
if not key.exists():
    with key.open('xb') as f: f.write(secrets.token_bytes(32))
app.secret_key = key.read_bytes()
app.config.update(MAX_CONTENT_LENGTH=32768, SESSION_COOKIE_SAMESITE='Lax')
towns = ['Indore', 'Dewas', 'Ujjain']
crops = ['Wheat', 'Soybean', 'Maize']
distances = [[5,40,55],[40,5,38],[55,38,5]]

def db():
    if 'db' not in g:
        g.db = sqlite3.connect(data/'market.sqlite'); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(_):
    if 'db' in g: g.db.close()

with app.app_context():
    db().executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE,hash TEXT,role TEXT,town TEXT DEFAULT 'Indore',crop TEXT DEFAULT 'Wheat',kg REAL DEFAULT 500);
    CREATE TABLE IF NOT EXISTS quotes(town TEXT,crop TEXT,price REAL,previous REAL,PRIMARY KEY(town,crop));
    CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,owner INTEGER,town TEXT,crop TEXT,kg REAL,net REAL,stamp TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS lots(id INTEGER PRIMARY KEY,owner INTEGER,crop TEXT,town TEXT,kg REAL,price REAL,grade TEXT,status TEXT DEFAULT 'Available');''')
    for crop,prices in zip(crops,[[2100,2250,2200],[4300,4250,4450],[1980,2080,2000]]):
        for town,p,change in zip(towns,prices,[1.02,1.06,.99]):
            db().execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?)',(town,crop,p,round(p/change)))
    db().executescript('''CREATE TABLE IF NOT EXISTS history(town TEXT,crop TEXT,day TEXT,price REAL,PRIMARY KEY(town,crop,day));
    INSERT INTO history SELECT town,crop,date('now','-7 days'),previous FROM quotes q WHERE NOT EXISTS(SELECT 1 FROM history h WHERE h.town=q.town AND h.crop=q.crop);
    INSERT INTO history SELECT town,crop,date('now'),price FROM quotes q WHERE (SELECT count(*) FROM history h WHERE h.town=q.town AND h.crop=q.crop)=1 AND NOT EXISTS(SELECT 1 FROM history h WHERE h.day=date('now') AND h.town=q.town AND h.crop=q.crop);
    CREATE TRIGGER IF NOT EXISTS archive_price AFTER UPDATE OF price,previous ON quotes BEGIN
    INSERT OR REPLACE INTO history VALUES(NEW.town,NEW.crop,date('now','-7 days'),NEW.previous);
    INSERT OR REPLACE INTO history VALUES(NEW.town,NEW.crop,date('now'),NEW.price); END;''')
    db().commit()

@app.before_request
def context():
    g.user=db().execute('SELECT * FROM users WHERE id=?',(session.get('uid'),)).fetchone()
    if request.method=='POST' and (not session.get('csrf') or request.form.get('csrf')!=session['csrf']): abort(400)
    session.setdefault('csrf',secrets.token_urlsafe(24))

def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if not g.user: return redirect('/login')
            if g.user['role'] not in allowed: abort(403)
            return fn(*a,**kw)
        return wrapped
    return deco

def field(k,options):
    v=request.form.get(k); return v if v in options else abort(400)

def number(k):
    try: n=float(request.form[k])
    except ValueError: abort(400)
    if not 0<n<=1000000: abort(400)
    return n

def page(view,**kw): return render_template('site.html',view=view,crops=crops,towns=towns,**kw)
app.jinja_env.filters['money']=lambda x:'₹{:,.2f}'.format(x)

@app.route('/login',methods=['GET','POST'])
@app.route('/register',methods=['GET','POST'])
def auth():
    register=request.path=='/register'
    if request.method=='POST':
        name=request.form['name'].strip(); pw=request.form['password']
        if register:
            if not 2<=len(name)<=50 or not 8<=len(pw)<=128: flash('Use a 2–50 character name and an 8–128 character password.'); return page('register')
            try:
                db().execute('INSERT INTO users(name,hash,role) VALUES(?,?,?)',(name,generate_password_hash(pw),field('role',['Farmer','Buyer']))); db().commit()
            except sqlite3.IntegrityError: flash('Choose a different username.'); return page('register')
        u=db().execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
        if u and check_password_hash(u['hash'],pw):
            session.clear(); session['uid']=u['id']; return redirect('/lots' if u['role']=='Buyer' else '/')
        flash('Username or password incorrect. Please try again.')
    return page('register' if register else 'login')

@app.post('/logout')
def logout(): session.clear(); return redirect('/')

def comparison(town,crop,kg):
    rows=[]
    for q in db().execute('SELECT * FROM quotes WHERE crop=?',(crop,)):
        km=distances[towns.index(town)][towns.index(q['town'])]; freight=km*50; gross=round(kg*q['price']/100,2)
        rows.append(dict(q,km=km,freight=freight,gross=gross,net=round(gross-freight,2),trend=round(100*(q['price']/q['previous']-1),1)))
    return sorted(rows,key=lambda r:(-r['net'],r['km']))

@app.get('/')
def home():
    u=g.user or dict(town='Indore',crop='Wheat',kg=500)
    rows=comparison(u['town'],u['crop'],u['kg']); nearest=min(rows,key=lambda r:r['km']); mode=request.args.get('mode','compare'); shown=[nearest] if mode=='nearest' else rows
    return page('desk',u=u,rows=shown,best=shown[0],mode=mode,alerts=[r for r in rows if abs(r['trend'])>=5])

@app.post('/profile')
@roles('Farmer')
def profile():
    db().execute('UPDATE users SET town=?,crop=?,kg=? WHERE id=?',(field('town',towns),field('crop',crops),number('kg'),g.user['id'])); db().commit(); return redirect('/')

@app.post('/confirm')
@roles('Farmer')
def confirm():
    u=g.user; row=next(r for r in comparison(u['town'],u['crop'],u['kg']) if r['town']==field('town',towns)); db().execute('INSERT INTO decisions(owner,town,crop,kg,net) VALUES(?,?,?,?,?)',(u['id'],row['town'],u['crop'],u['kg'],row['net'])); db().commit(); flash('Market choice saved.'); return redirect('/journal')

@app.get('/journal')
@roles('Farmer')
def journal(): return page('journal',records=db().execute('SELECT * FROM decisions WHERE owner=? ORDER BY id DESC',(g.user['id'],)).fetchall())

@app.get('/prices')
def prices():
    crop=request.args.get('crop','Wheat'); town=request.args.get('town','Indore'); day=request.args.get('day','')
    rows=db().execute("SELECT day,price FROM history WHERE crop=? COLLATE NOCASE AND town=? AND (?='' OR day=?) ORDER BY day",(crop,town,day,day)).fetchall()
    return page('prices',crop=crop,town=town,day=day,history=rows,peak=max((r['price'] for r in rows),default=1))

@app.get('/lots')
def lots():
    c=request.args.get('crop',''); t=request.args.get('town',''); rows=db().execute("SELECT lots.*,users.name FROM lots JOIN users ON owner=users.id WHERE (?='' OR crop=?) AND (?='' OR lots.town=?) AND (status='Available' OR owner=?) ORDER BY lots.id DESC".replace('OR crop=?','OR lots.crop=?'),(c,c,t,t,session.get('uid'))).fetchall(); return page('lots',lots=rows,c=c,t=t)

@app.post('/lots')
@roles('Farmer')
def post_lot():
    db().execute('INSERT INTO lots(owner,crop,town,kg,price,grade) VALUES(?,?,?,?,?,?)',(g.user['id'],field('crop',crops),field('town',towns),number('kg'),number('price'),field('grade',['A','B','C']))); db().commit(); flash('Lot published. Buyers can now find it.'); return redirect('/lots')

@app.post('/lots/<int:lot>/close')
@roles('Farmer')
def close_lot(lot):
    db().execute("UPDATE lots SET status='Closed' WHERE id=? AND owner=?",(lot,g.user['id'])); db().commit(); return redirect('/lots')

@app.route('/reports',methods=['GET','POST'])
@roles('Admin','Analyst')
def reports():
    if request.method=='POST':
        if g.user['role']!='Admin': abort(403)
        db().execute('UPDATE quotes SET previous=?,price=? WHERE town=? AND crop=?',(number('previous'),number('price'),field('town',towns),field('crop',crops))); db().commit(); flash('Reference quotes updated.'); return redirect('/reports')
    return page('reports',records=db().execute('SELECT crop,town,count(*) AS choices,sum(kg) AS kg,sum(net) AS net FROM decisions GROUP BY crop,town').fetchall())

@app.cli.command('staff')
@click.argument('name')
@click.argument('role',type=click.Choice(['Analyst','Admin']))
@click.password_option()
def staff(name,role,password):
    if len(password)<8: raise click.ClickException('Use at least eight characters.')
    db().execute('INSERT INTO users(name,hash,role) VALUES(?,?,?)',(name,generate_password_hash(password),role)); db().commit()

if __name__=='__main__': app.run(port=5050)
