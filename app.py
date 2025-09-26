import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_mysqldb import MySQL
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = 'baby_purple_secret'  # for user sessions

# ===== MySQL configuration using environment variables =====
app.config['MYSQL_HOST'] = os.environ.get('MYSQL_HOST', 'localhost')
app.config['MYSQL_USER'] = os.environ.get('MYSQL_USER', 'root')
app.config['MYSQL_PASSWORD'] = os.environ.get('MYSQL_PASSWORD', 'gopal')
app.config['MYSQL_DB'] = os.environ.get('MYSQL_DB', 'focus_app')

mysql = MySQL(app)

# ===== Daily XP reset =====
def reset_daily_xp():
    cur = mysql.connection.cursor()
    cur.execute("UPDATE users SET xp = 0")
    mysql.connection.commit()
    cur.close()
    print(f"[{date.today()}] Daily XP reset completed!")

scheduler = BackgroundScheduler()
scheduler.add_job(func=reset_daily_xp, trigger="cron", hour=0, minute=0)  # runs at 00:00
scheduler.start()


# ===== SIGNUP =====
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_pw = generate_password_hash(password)
        
        cur = mysql.connection.cursor()
        # Check if username exists
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        existing = cur.fetchone()
        if existing:
            cur.close()
            return "Username already exists! <a href='/signup'>Try again</a>"
        
        cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_pw))
        mysql.connection.commit()
        cur.close()
        return redirect('/login')
    return render_template('signup.html')


# ===== LOGIN =====
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cur = mysql.connection.cursor()
        cur.execute("SELECT id, password FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['username'] = username
            return redirect('/')
        else:
            return "Invalid username or password! <a href='/login'>Try again</a>"
    return render_template('login.html')


# ===== LOGOUT =====
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ===== HOME ===== 
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()
    
    # ---- Reset daily XP if last update was before today ----
    cur.execute("""
        UPDATE users 
        SET xp = 0, last_xp_update = CURDATE() 
        WHERE id=%s AND (last_xp_update IS NULL OR last_xp_update < CURDATE())
    """, (session['user_id'],))
    mysql.connection.commit()
    
    # ---- Fetch tasks ----
    cur.execute("SELECT * FROM tasks WHERE user_id=%s", (session['user_id'],))
    tasks = cur.fetchall()
    
    # ---- Optionally fetch friend timers if you implemented them ----
    # cur.execute("SELECT * FROM friend_timers WHERE ...")
    # friend_timers = cur.fetchall()

    cur.close()
    return render_template('index.html', tasks=tasks, user_id=session['user_id'])

# ===== TASK ROUTES =====
@app.route('/add_task', methods=['POST'])
def add_task():
    description = request.form['description']
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO tasks (user_id, description) VALUES (%s, %s)", 
                (session['user_id'], description))
    mysql.connection.commit()
    cur.close()
    return redirect('/')


@app.route('/toggle_task/<int:task_id>')
def toggle_task(task_id):
    cur = mysql.connection.cursor()
    cur.execute("UPDATE tasks SET completed = NOT completed WHERE id = %s AND user_id = %s", 
                (task_id, session['user_id']))
    mysql.connection.commit()
    cur.close()
    return redirect('/')


@app.route('/delete_task/<int:task_id>')
def delete_task(task_id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM tasks WHERE id=%s AND user_id=%s", (task_id, session['user_id']))
    mysql.connection.commit()
    cur.close()
    return redirect('/')


@app.route('/clear_tasks')
def clear_tasks():
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM tasks WHERE user_id=%s", (session['user_id'],))
    mysql.connection.commit()
    cur.close()
    return redirect('/')


# ===== TIMER =====

@app.route('/start_timer', methods=['POST'])
def start_timer():
    if 'user_id' not in session:
        return redirect('/login')
    
    minutes = int(request.form['minutes'])
    friend_id = request.form.get('friend_id')  # optional
    
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO sessions (user_id, start_time) VALUES (%s, %s)", 
                (session['user_id'], datetime.now()))
    mysql.connection.commit()
    
    cur.execute("SELECT LAST_INSERT_ID()")
    session_id = cur.fetchone()[0]

    # Share timer if friend selected
    if friend_id:
        cur.execute("INSERT INTO shared_timers (user_id, friend_id, start_time, minutes) VALUES (%s, %s, %s, %s)",
                    (session['user_id'], friend_id, datetime.now(), minutes))
        mysql.connection.commit()
    
    cur.close()
    return redirect(url_for('home', session_id=session_id))


@app.route('/end_timer/<int:session_id>', methods=['POST'])
def end_timer(session_id):
    if 'user_id' not in session:
        return redirect('/login')
    
    focused = request.form['focused'] == 'yes'
    reason = request.form.get('reason', '')
    minutes = int(request.form.get('minutes', 0))  # THIS LINE ADDED
    cur = mysql.connection.cursor()
    
    cur.execute(
        "UPDATE sessions SET end_time=%s, focused=%s, reason=%s WHERE id=%s AND user_id=%s",
        (datetime.now(), focused, reason, session_id, session['user_id'])
    )
    
    if focused:
        cur.execute("UPDATE users SET xp = xp + %s WHERE id=%s", (minutes, session['user_id']))
        cur.execute("UPDATE users SET level = level + 1 WHERE xp >= 50 AND id=%s", (session['user_id'],))
    
    mysql.connection.commit()
    cur.close()
    return '', 204


# ===== FRIENDS & REQUESTS =====
@app.route('/friends')
def friends_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    cur = mysql.connection.cursor()
    
    # Incoming friend requests
    cur.execute("""
        SELECT f.id, u.username 
        FROM friends f 
        JOIN users u ON f.user_id = u.id 
        WHERE f.friend_id=%s AND f.status='pending'
    """, (session['user_id'],))
    incoming_requests = cur.fetchall()
    
    # Friends list
    cur.execute("""
        SELECT u.id, u.username 
        FROM users u
        JOIN friends f 
          ON ( (f.user_id = u.id AND f.friend_id = %s) OR (f.friend_id = u.id AND f.user_id = %s) )
        WHERE f.status='accepted'
    """, (session['user_id'], session['user_id']))
    friends_list = cur.fetchall()
    
    cur.close()
    return render_template('friends.html', incoming_requests=incoming_requests, friends=friends_list, user_id=session['user_id'])


# Send request by code (user ID)
@app.route('/add_friend_by_code', methods=['POST'])
def add_friend_by_code():
    if 'user_id' not in session:
        return redirect('/login')
    
    friend_id = int(request.form['friend_id'])
    cur = mysql.connection.cursor()

    # Check if user exists
    cur.execute("SELECT id FROM users WHERE id=%s", (friend_id,))
    if not cur.fetchone():
        cur.close()
        return "User not found 😅 <a href='/friends'>Go back</a>"
    
    # Check if request/friendship exists
    cur.execute("""
        SELECT * FROM friends 
        WHERE (user_id=%s AND friend_id=%s) OR (user_id=%s AND friend_id=%s)
    """, (session['user_id'], friend_id, friend_id, session['user_id']))
    if cur.fetchone():
        cur.close()
        return "Already friends or request pending 😅 <a href='/friends'>Go back</a>"
    
    # Add pending request
    cur.execute("INSERT INTO friends (user_id, friend_id, status) VALUES (%s,%s,'pending')", 
                (session['user_id'], friend_id))
    mysql.connection.commit()
    cur.close()
    return redirect('/friends')


# Accept/Reject friend request
@app.route('/respond_request/<int:request_id>/<string:action>')
def respond_request(request_id, action):
    if 'user_id' not in session:
        return redirect('/login')
    
    cur = mysql.connection.cursor()
    cur.execute("SELECT user_id, friend_id FROM friends WHERE id=%s", (request_id,))
    req = cur.fetchone()
    
    if req and req[1] == session['user_id']:
        if action == 'accept':
            cur.execute("UPDATE friends SET status='accepted' WHERE id=%s", (request_id,))
        elif action == 'reject':
            cur.execute("DELETE FROM friends WHERE id=%s", (request_id,))
        mysql.connection.commit()
    cur.close()
    return redirect('/friends')




# ===== FRIEND TASKS =====
@app.route('/friend_tasks/<int:friend_id>')
def friend_tasks(friend_id):
    if 'user_id' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()
    # Check if they are actually friends
    cur.execute("""
        SELECT * FROM friends 
        WHERE (user_id=%s AND friend_id=%s) OR (user_id=%s AND friend_id=%s)
    """, (session['user_id'], friend_id, friend_id, session['user_id']))
    
    if not cur.fetchone():
        cur.close()
        return "Not friends 😅"

    # Fetch friend's tasks
    cur.execute("SELECT * FROM tasks WHERE user_id=%s", (friend_id,))
    tasks = cur.fetchall()
    cur.close()
    return render_template('friend_tasks.html', tasks=tasks)

# ===== LEADERBOARD =====
@app.route('/leaderboard')
def leaderboard():
    if 'user_id' not in session:
        return redirect('/login')
    cur = mysql.connection.cursor()
    cur.execute("SELECT username, level, xp FROM users ORDER BY xp DESC LIMIT 10")
    top_users = cur.fetchall()
    cur.close()
    return render_template('leaderboard.html', top_users=top_users)


if __name__ == '__main__':
    app.run(debug=True)
