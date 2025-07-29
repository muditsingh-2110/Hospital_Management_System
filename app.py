from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import mysql.connector
import uuid
from functools import wraps
from datetime import timedelta

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.permanent_session_lifetime = timedelta(minutes=30)

# MySQL Configuration
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="ynsingh99*",
    database="hospital_db"
)

# -------------------- HELPER FUNCTIONS & DECORATOR --------------------

@app.before_request
def before_request_handler():
    session.permanent = True
    try:
        if not db.is_connected():
            db.reconnect()
    except mysql.connector.Error:
        db.reconnect(attempts=1, delay=0)

def login_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'username' not in session:
                return redirect(url_for('login'))
            if session.get('role') != role:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def generate_uhid():
    return "UHID-" + str(uuid.uuid4())[:8]

def get_next_queue_number():
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT MAX(queue_no) AS max_no FROM (SELECT queue_no FROM employee_patient UNION SELECT queue_no FROM nonemployee_patient) AS all_queues")
    result = cursor.fetchone()
    cursor.close()
    return (result["max_no"] or 0) + 1

# -------------------- GENERAL & LOGIN ROUTES --------------------

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s AND password = %s", (username, password))
        user = cursor.fetchone()
        cursor.close()
        if user:
            session['username'] = username
            session['role'] = user['role']
            if user['role'] == 'receptionist':
                return redirect(url_for('reception_dashboard'))
            elif user['role'] == 'doctor':
                return redirect(url_for('doctor_dashboard'))
            elif user['role'] == 'pharmacist':
                return redirect(url_for('pharmacy_dashboard'))
            else:
                return "Unknown role!"
        else:
            return render_template('login.html', error="Invalid username or password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# -------------------- RECEPTIONIST ROUTES --------------------

@app.route('/reception_dashboard')
@login_required('receptionist')
def reception_dashboard():
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT ep.uhid, ep.emp_id, e.name as emp_name, ep.symptoms, ep.queue_no, ep.doctor_assigned
        FROM employee_patient ep JOIN employee e ON ep.emp_id = e.emp_id
    """)
    employee_data = cursor.fetchall()
    cursor.execute("SELECT nep.uhid, nep.name, nep.age, nep.gender, nep.symptoms, nep.queue_no, nep.doctor_assigned, nep.bill FROM nonemployee_patient nep")
    nonemployee_data = cursor.fetchall()
    cursor.close()
    return render_template('reception_dashboard.html', employee_data=employee_data, nonemployee_data=nonemployee_data)

@app.route('/billing_items')
@login_required('receptionist')
def billing_items():
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, price FROM billing_items")
        items = cursor.fetchall()
        cursor.close()
        return jsonify(items)
    except Exception as e:
        print(f"[ERROR in /billing_items]: {e}")
        return jsonify({"error": "An internal error occurred"}), 500

@app.route('/register', methods=['GET', 'POST'])
@login_required('receptionist')
def register():
    cursor = db.cursor(dictionary=True)
    if request.method == 'POST':
        patient_type = request.form['patientType']
        name = request.form['name']
        symptoms = request.form['symptoms']
        queue_no = get_next_queue_number()

        cursor.execute("SELECT department FROM symptom_department WHERE symptom = %s", (symptoms,))
        dept_row = cursor.fetchone()
        department = dept_row['department'] if dept_row else symptoms
        
        cursor.execute("SELECT name FROM doctor WHERE department = %s LIMIT 1", (department,))
        doctor = cursor.fetchone()
        doctor_assigned = doctor['name'] if doctor else "Dr. Placeholder"

        if patient_type == "employee":
            emp_id = request.form['emp_id']
            if not emp_id: return "Error: Employee ID not found."
            uhid = generate_uhid()
            cursor.execute("INSERT INTO employee_patient (uhid, emp_id, symptoms, queue_no, doctor_assigned) VALUES (%s, %s, %s, %s, %s)", (uhid, emp_id, symptoms, queue_no, doctor_assigned))
        else:
            age = request.form['age']
            gender = request.form['gender']
            uhid = generate_uhid()
            cursor.execute("INSERT INTO nonemployee_patient (uhid, name, age, gender, symptoms, queue_no, doctor_assigned) VALUES (%s, %s, %s, %s, %s, %s, %s)", (uhid, name, age, gender, symptoms, queue_no, doctor_assigned))
        
        db.commit()
        cursor.close()
        return render_template('register_success.html')
    
    cursor.close()
    return render_template('register.html')

@app.route('/check_uhid_employee', methods=['POST'])
@login_required('receptionist')
def check_uhid_employee():
    cursor = db.cursor(dictionary=True)
    data = request.get_json()
    name = data.get("name")
    cursor.execute("SELECT emp_id FROM employee WHERE name = %s", (name,))
    emp = cursor.fetchone()
    if emp:
        emp_id = emp['emp_id']
        cursor.execute("SELECT uhid FROM employee_patient WHERE emp_id = %s", (emp_id,))
        patient = cursor.fetchone()
        cursor.close()
        return jsonify({"exists": bool(patient), "uhid": patient['uhid'] if patient else None, "emp_id": emp_id})
    cursor.close()
    return jsonify({"exists": False})

@app.route('/check_uhid_nonemployee', methods=['POST'])
@login_required('receptionist')
def check_uhid_nonemployee():
    cursor = db.cursor(dictionary=True)
    data = request.get_json()
    name = data.get("name")
    cursor.execute("SELECT uhid FROM nonemployee_patient WHERE name = %s", (name,))
    patient = cursor.fetchone()
    cursor.close()
    return jsonify({"exists": bool(patient), "uhid": patient['uhid'] if patient else None})

@app.route('/add_bill_items', methods=['POST'])
@login_required('receptionist')
def add_bill_items():
    data = request.get_json()
    uhid, items = data.get('uhid'), data.get('items')
    total_bill = sum(float(item['total']) for item in items)
    try:
        cursor = db.cursor(dictionary=True)
        for item in items:
            cursor.execute("INSERT INTO bill_history (uhid, item_id, item_name, price, quantity, total) VALUES (%s, %s, %s, %s, %s, %s)", (uhid, item['item_id'], item['item_name'], item['price'], item['quantity'], item['total']))
        cursor.execute("UPDATE nonemployee_patient SET bill = bill + %s WHERE uhid = %s", (total_bill, uhid))
        db.commit()
        cursor.close()
        return jsonify({'success': True, 'total': total_bill})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/bill_history/<uhid>')
@login_required('receptionist')
def bill_history(uhid):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT item_name, price, quantity, total, created_at FROM bill_history WHERE uhid = %s ORDER BY created_at DESC", (uhid,))
    history = cursor.fetchall()
    cursor.close()
    return jsonify(history)

@app.route('/clear_bill', methods=['POST'])
@login_required('receptionist')
def clear_bill():
    data = request.get_json()
    uhid, pay_method = data.get('uhid'), data.get('pay_method')
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT bill FROM nonemployee_patient WHERE uhid = %s", (uhid,))
        row = cursor.fetchone()
        bill_amt = row['bill'] if row else 0
        cursor.execute("INSERT INTO bill_payment (uhid, amount, pay_method) VALUES (%s, %s, %s)", (uhid, bill_amt, pay_method))
        cursor.execute("UPDATE nonemployee_patient SET bill = 0 WHERE uhid = %s", (uhid,))
        db.commit()
        cursor.close()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)})

# -------------------- DOCTOR ROUTES --------------------

@app.route('/doctor_dashboard')
@login_required('doctor')
def doctor_dashboard():
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT ep.uhid, e.name, ep.symptoms, ep.queue_no, d.department
        FROM employee_patient ep
        JOIN employee e ON ep.emp_id = e.emp_id
        LEFT JOIN doctor d ON ep.doctor_assigned = d.name
        WHERE ep.status IS NULL
        UNION
        SELECT nep.uhid, nep.name, nep.symptoms, nep.queue_no, d.department
        FROM nonemployee_patient nep
        LEFT JOIN doctor d ON nep.doctor_assigned = d.name
        WHERE nep.status IS NULL
        ORDER BY queue_no
    """)
    patients = cursor.fetchall()
    cursor.close()
    return render_template('doctor_dashboard.html', patients=patients)

@app.route('/examine/<uhid>', methods=['GET', 'POST'])
@login_required('doctor')
def examine_patient(uhid):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT name FROM medicine")
    medicines = [row['name'] for row in cursor.fetchall()]
    if request.method == 'POST':
        prescription = request.form['prescription']
        cursor.execute("UPDATE employee_patient SET prescription = %s, status = 'examined' WHERE uhid = %s", (prescription, uhid))
        if cursor.rowcount == 0:
            cursor.execute("UPDATE nonemployee_patient SET prescription = %s, status = 'examined' WHERE uhid = %s", (prescription, uhid))
        db.commit()
        cursor.close()
        return redirect(url_for('doctor_dashboard'))
    cursor.close()
    return render_template('examine.html', uhid=uhid, medicines=medicines)

# -------------------- PHARMACIST ROUTES --------------------

@app.route('/pharmacy_dashboard')
@login_required('pharmacist')
def pharmacy_dashboard():
    cursor = db.cursor(dictionary=True)
    import json
    cursor.execute("""
        SELECT uhid, prescription FROM employee_patient
        WHERE prescription IS NOT NULL AND status = 'examined'
        UNION
        SELECT uhid, prescription FROM nonemployee_patient
        WHERE prescription IS NOT NULL AND status = 'examined'
    """)
    prescriptions = cursor.fetchall()
    for p in prescriptions:
        try:
            p['prescription_list'] = json.loads(p['prescription']) if p['prescription'] else []
        except Exception:
            p['prescription_list'] = []
    
    cursor.execute("SELECT name, stock FROM medicine")
    medicines = cursor.fetchall()
    cursor.close()
    return render_template('pharmacy_dashboard.html', prescriptions=prescriptions, medicines=medicines)

@app.route('/dispense_prescription', methods=['POST'])
@login_required('pharmacist')
def dispense_prescription():
    uhid = request.form.get('uhid')
    import json
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT prescription FROM employee_patient WHERE uhid = %s UNION SELECT prescription FROM nonemployee_patient WHERE uhid = %s", (uhid, uhid))
        row = cursor.fetchone()
        if not row or not row['prescription']:
            cursor.close()
            return jsonify({'success': False, 'error': 'Prescription not found'}), 400
        
        prescription_list = json.loads(row['prescription'])
        
        for item in prescription_list:
            cursor.execute("SELECT stock FROM medicine WHERE name = %s", (item['medicine'],))
            med_row = cursor.fetchone()
            if not med_row or med_row['stock'] < int(item['qty']):
                raise Exception(f"Insufficient stock for {item['medicine']}")
            cursor.execute("UPDATE medicine SET stock = stock - %s WHERE name = %s", (item['qty'], item['medicine']))
            
        cursor.execute("UPDATE employee_patient SET status = 'dispensed' WHERE uhid = %s", (uhid,))
        if cursor.rowcount == 0:
            cursor.execute("UPDATE nonemployee_patient SET status = 'dispensed' WHERE uhid = %s", (uhid,))
        
        db.commit()
        cursor.close()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/update_stock', methods=['POST'])
@login_required('pharmacist')
def update_stock():
    data = request.get_json()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("UPDATE medicine SET stock = %s WHERE name = %s", (data.get('stock'), data.get('name')))
        db.commit()
        cursor.close()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/add_medicine', methods=['POST'])
@login_required('pharmacist')
def add_medicine():
    data = request.get_json()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("INSERT INTO medicine (name, stock) VALUES (%s, %s)", (data.get('name'), data.get('stock')))
        db.commit()
        cursor.close()
        return jsonify({'success': True})
    except mysql.connector.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'error': 'Medicine already exists!'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)})

# -------------------- MAIN EXECUTION --------------------

if __name__ == '__main__':
    app.run(debug=True)